from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class MultiHourOutcomeConfig:
    primary_horizons_seconds: tuple[int, ...] = (3600, 7200, 14400, 28800)
    diagnostic_horizons_seconds: tuple[int, ...] = (21600, 86400)
    slippage_bps_per_side: float = 0.5
    cost_stress_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    schema_revision: int = 1

    def __post_init__(self) -> None:
        horizons = self.primary_horizons_seconds + self.diagnostic_horizons_seconds
        if not horizons or any(value <= 0 for value in horizons):
            raise ValueError("horizons must be positive")
        if len(set(horizons)) != len(horizons):
            raise ValueError("primary and diagnostic horizons must be unique")
        if self.slippage_bps_per_side < 0:
            raise ValueError("slippage cannot be negative")
        if self.cost_stress_multipliers != (1.0, 1.5, 2.0):
            raise ValueError("base, 1.5x, and 2x cost stress are required")


@dataclass(frozen=True)
class MultiHourOutcomeManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_multi_hour_dataset_id: str
    source_multi_hour_manifest: str
    row_count: int
    valid_counts: dict[str, int]
    config: dict[str, object]
    outcome_columns: list[str]
    partitions: list[str]
    limitations: list[str]
    future_information: bool = True
    physically_separate_from_features: bool = True
    join_key: str = "occurred_at"
    evaluation_role: str = "development_only"
    holdout_eligible: bool = False
    research_only: bool = True
    real_money_enabled: bool = False


def build_multi_hour_outcomes(
    table: pa.Table, config: MultiHourOutcomeConfig | None = None
) -> tuple[pa.Table, dict[str, int]]:
    cfg = config or MultiHourOutcomeConfig()
    required = {
        "occurred_at",
        "feature_available_at",
        "bar_open_at",
        "bar_open_bid",
        "bar_open_ask",
        "bar_high",
        "bar_low",
        "is_gap_start",
    }
    if not required.issubset(table.column_names):
        raise ValueError("multi-hour features lack executable opening sides")
    times = _timestamps(table["bar_open_at"])
    occurred = _timestamps(table["occurred_at"])
    available = _timestamps(table["feature_available_at"])
    bid = [float(value) for value in table["bar_open_bid"].to_pylist()]
    ask = [float(value) for value in table["bar_open_ask"].to_pylist()]
    highs = [float(value) for value in table["bar_high"].to_pylist()]
    lows = [float(value) for value in table["bar_low"].to_pylist()]
    gaps = [bool(value) for value in table["is_gap_start"].to_pylist()]
    bar_seconds = _bar_seconds(times)
    rows: dict[str, list[object]] = {"occurred_at": occurred}
    valid_counts: dict[str, int] = {}
    horizons = cfg.primary_horizons_seconds + cfg.diagnostic_horizons_seconds
    for horizon in horizons:
        if horizon % bar_seconds:
            raise ValueError("horizon must be a multiple of the decision bar")
        steps = horizon // bar_seconds
        prefix = f"h{horizon}"
        columns = {name: [None] * len(times) for name in _names(prefix)}
        columns[f"{prefix}_valid"] = [False] * len(times)
        valid = 0
        for signal in range(len(times)):
            entry = signal + 1
            exit_index = entry + steps
            if exit_index >= len(times) or not _continuous(
                times, gaps, entry, exit_index, bar_seconds
            ):
                continue
            entry_mid = (bid[entry] + ask[entry]) / 2
            exit_mid = (bid[exit_index] + ask[exit_index]) / 2
            columns[f"{prefix}_valid"][signal] = True
            columns[f"{prefix}_entry_delay_seconds"][signal] = (
                times[entry] - available[signal]
            ).total_seconds()
            columns[f"{prefix}_endpoint_delay_seconds"][signal] = 0.0
            columns[f"{prefix}_long_gross_bps"][signal] = (exit_mid / entry_mid - 1) * 10_000
            columns[f"{prefix}_short_gross_bps"][signal] = (entry_mid / exit_mid - 1) * 10_000
            for multiplier, suffix in (
                (1.0, "base"),
                (1.5, "cost_1_5x"),
                (2.0, "cost_2x"),
            ):
                slip = cfg.slippage_bps_per_side * multiplier / 10_000
                long_entry = ask[entry] * (1 + slip)
                long_exit = bid[exit_index] * (1 - slip)
                short_entry = bid[entry] * (1 - slip)
                short_exit = ask[exit_index] * (1 + slip)
                columns[f"{prefix}_long_net_{suffix}_bps"][signal] = (
                    long_exit / long_entry - 1
                ) * 10_000
                columns[f"{prefix}_short_net_{suffix}_bps"][signal] = (
                    short_entry / short_exit - 1
                ) * 10_000
            path_high = max(highs[entry : exit_index + 1])
            path_low = min(lows[entry : exit_index + 1])
            high_offset = highs[entry : exit_index + 1].index(path_high)
            low_offset = lows[entry : exit_index + 1].index(path_low)
            columns[f"{prefix}_long_mfe_proxy_bps"][signal] = (path_high / ask[entry] - 1) * 10_000
            columns[f"{prefix}_long_mae_proxy_bps"][signal] = (path_low / ask[entry] - 1) * 10_000
            columns[f"{prefix}_short_mfe_proxy_bps"][signal] = (bid[entry] / path_low - 1) * 10_000
            columns[f"{prefix}_short_mae_proxy_bps"][signal] = (bid[entry] / path_high - 1) * 10_000
            columns[f"{prefix}_long_time_to_mfe_seconds"][signal] = (
                times[entry + high_offset] - times[entry]
            ).total_seconds()
            columns[f"{prefix}_long_time_to_mae_seconds"][signal] = (
                times[entry + low_offset] - times[entry]
            ).total_seconds()
            columns[f"{prefix}_short_time_to_mfe_seconds"][signal] = (
                times[entry + low_offset] - times[entry]
            ).total_seconds()
            columns[f"{prefix}_short_time_to_mae_seconds"][signal] = (
                times[entry + high_offset] - times[entry]
            ).total_seconds()
            valid += 1
        rows.update(columns)
        valid_counts[str(horizon)] = valid
    return pa.Table.from_pydict(rows), valid_counts


def write_multi_hour_outcomes(
    feature_manifest: Path,
    output_root: Path,
    config: MultiHourOutcomeConfig | None = None,
) -> MultiHourOutcomeManifest:
    cfg = config or MultiHourOutcomeConfig()
    source = json.loads(feature_manifest.read_text(encoding="utf-8"))
    if source.get("point_in_time") is not True or source.get("labels_included") is not False:
        raise ValueError("source must be point-in-time and label-free")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": source["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-multi-hour-outcomes-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return MultiHourOutcomeManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        outcomes, valid = build_multi_hour_outcomes(_read_source(feature_manifest, source), cfg)
        partition = staging / "outcomes.parquet"
        pq.write_table(outcomes, partition, compression="zstd", row_group_size=10_000)
        manifest = MultiHourOutcomeManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_multi_hour_dataset_id=str(source["dataset_id"]),
            source_multi_hour_manifest=str(feature_manifest.resolve()),
            row_count=outcomes.num_rows,
            valid_counts=valid,
            config=serialized,
            outcome_columns=outcomes.column_names,
            partitions=[str(root / partition.name)],
            limitations=[
                "MFE and MAE use five-minute mid-range proxies, not executable tick paths",
                "commission and broker-specific long/short rollover swap are not yet modeled",
                "copy latency and rejected-order behavior are not yet modeled",
                "paths crossing missing bars or market gaps are excluded",
                "six-hour and twenty-four-hour horizons are descriptive only",
            ],
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _names(prefix: str) -> list[str]:
    return [
        f"{prefix}_{name}"
        for name in (
            "valid",
            "entry_delay_seconds",
            "endpoint_delay_seconds",
            "long_gross_bps",
            "short_gross_bps",
            "long_net_base_bps",
            "short_net_base_bps",
            "long_net_cost_1_5x_bps",
            "short_net_cost_1_5x_bps",
            "long_net_cost_2x_bps",
            "short_net_cost_2x_bps",
            "long_mfe_proxy_bps",
            "long_mae_proxy_bps",
            "short_mfe_proxy_bps",
            "short_mae_proxy_bps",
            "long_time_to_mfe_seconds",
            "long_time_to_mae_seconds",
            "short_time_to_mfe_seconds",
            "short_time_to_mae_seconds",
        )
    ]


def _continuous(
    times: list[datetime], gaps: list[bool], start: int, end: int, seconds: int
) -> bool:
    return all(
        not gaps[index] and (times[index] - times[index - 1]).total_seconds() == seconds
        for index in range(start, end + 1)
    )


def _bar_seconds(times: list[datetime]) -> int:
    differences = [
        int((times[index] - times[index - 1]).total_seconds())
        for index in range(1, len(times))
        if times[index] > times[index - 1]
    ]
    return min(differences) if differences else 300


def _read_source(manifest: Path, source: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    tables = []
    for stored in source.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("feature partition escapes manifest directory")
        tables.append(pq.read_table(path))
    if not tables:
        raise ValueError("feature manifest has no partitions")
    return pa.concat_tables(tables)


def _timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[column.type.unit]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
