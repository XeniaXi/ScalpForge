from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from scalpforge_strategy.experiment_registry import register_experiment


@dataclass(frozen=True)
class TradeManagementConfig:
    maximum_entry_lag_seconds: int = 30
    minimum_pairs: int = 30

    def __post_init__(self) -> None:
        if self.maximum_entry_lag_seconds < 0:
            raise ValueError("maximum entry lag cannot be negative")
        if self.minimum_pairs < 1:
            raise ValueError("minimum pairs must be positive")


@dataclass(frozen=True)
class TradeManagementReport:
    report_id: str
    schema_version: int
    created_at: str
    provider_dataset_id: str
    copied_dataset_id: str
    config: dict[str, object]
    provider_trade_count: int
    copied_trade_count: int
    matched_pair_count: int
    provider_match_rate: float
    copied_match_rate: float
    median_entry_lag_seconds: float | None
    median_adverse_entry_bps: float | None
    mean_provider_path_bps: float | None
    mean_copied_entry_provider_exit_bps: float | None
    mean_provider_entry_copied_exit_bps: float | None
    mean_copied_path_bps: float | None
    mean_entry_effect_bps: float | None
    mean_exit_effect_bps: float | None
    profitable_pair_ratio_provider_path: float | None
    profitable_pair_ratio_copied_path: float | None
    sufficient_sample: bool
    pair_partition: str
    holdout_evaluated: bool = False
    research_only: bool = True
    real_money_enabled: bool = False


def run_trade_management_lab(
    provider_manifest: Path,
    copied_manifest: Path,
    output_root: Path,
    config: TradeManagementConfig | None = None,
) -> TradeManagementReport:
    cfg = config or TradeManagementConfig()
    provider_meta, provider = _load(provider_manifest, "provider_master")
    copied_meta, copied = _load(copied_manifest, "copied_account")
    pairs = match_trades(provider, copied, cfg.maximum_entry_lag_seconds)
    identity = json.dumps(
        {
            "provider": provider_meta["dataset_id"],
            "copied": copied_meta["dataset_id"],
            "config": asdict(cfg),
        },
        sort_keys=True,
    ).encode()
    report_id = "trade-management-lab-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / report_id
    root.mkdir(parents=True, exist_ok=True)
    pair_path = root / "matched-trades.parquet"
    pq.write_table(
        pa.Table.from_pylist(pairs, schema=_pair_schema()), pair_path, compression="zstd"
    )

    provider_path = _values(pairs, "provider_path_bps")
    copied_at_provider_exit = _values(pairs, "copied_entry_provider_exit_bps")
    provider_at_copied_exit = _values(pairs, "provider_entry_copied_exit_bps")
    copied_path = _values(pairs, "copied_path_bps")
    entry_effects = _values(pairs, "entry_effect_bps")
    exit_effects = _values(pairs, "exit_effect_bps")
    entry_lags = _values(pairs, "entry_lag_seconds")
    adverse_entries = _values(pairs, "adverse_entry_bps")
    report = TradeManagementReport(
        report_id=report_id,
        schema_version=1,
        created_at=datetime.now(UTC).isoformat(),
        provider_dataset_id=str(provider_meta["dataset_id"]),
        copied_dataset_id=str(copied_meta["dataset_id"]),
        config=asdict(cfg),
        provider_trade_count=len(provider),
        copied_trade_count=len(copied),
        matched_pair_count=len(pairs),
        provider_match_rate=len(pairs) / len(provider) if provider else 0.0,
        copied_match_rate=len(pairs) / len(copied) if copied else 0.0,
        median_entry_lag_seconds=_median(entry_lags),
        median_adverse_entry_bps=_median(adverse_entries),
        mean_provider_path_bps=_mean(provider_path),
        mean_copied_entry_provider_exit_bps=_mean(copied_at_provider_exit),
        mean_provider_entry_copied_exit_bps=_mean(provider_at_copied_exit),
        mean_copied_path_bps=_mean(copied_path),
        mean_entry_effect_bps=_mean(entry_effects),
        mean_exit_effect_bps=_mean(exit_effects),
        profitable_pair_ratio_provider_path=_positive_ratio(provider_path),
        profitable_pair_ratio_copied_path=_positive_ratio(copied_path),
        sufficient_sample=len(pairs) >= cfg.minimum_pairs,
        pair_partition=str(pair_path),
    )
    (root / "report.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    register_experiment(
        output_root / "experiment-registry.jsonl",
        report_id=report_id,
        experiment_family="trade-management-lab",
        dataset_ids=(report.provider_dataset_id, report.copied_dataset_id),
        hypothesis_count=2,
        holdout_evaluated=False,
    )
    return report


def match_trades(
    provider: list[dict[str, object]], copied: list[dict[str, object]], tolerance: int
) -> list[dict[str, object]]:
    """Return deterministic one-to-one matches without using trade outcomes for selection."""
    candidates: list[tuple[float, datetime, str, str, int, int]] = []
    for provider_index, source in enumerate(provider):
        for copied_index, target in enumerate(copied):
            if source["symbol"] != target["symbol"] or source["side"] != target["side"]:
                continue
            lag = (_dt(target["opened_at"]) - _dt(source["opened_at"])).total_seconds()
            if abs(lag) <= tolerance:
                candidates.append(
                    (
                        abs(lag),
                        _dt(source["opened_at"]),
                        str(source["ticket"]),
                        str(target["ticket"]),
                        provider_index,
                        copied_index,
                    )
                )
    used_provider: set[int] = set()
    used_copied: set[int] = set()
    pairs: list[dict[str, object]] = []
    for _, _, _, _, provider_index, copied_index in sorted(candidates):
        if provider_index in used_provider or copied_index in used_copied:
            continue
        used_provider.add(provider_index)
        used_copied.add(copied_index)
        pairs.append(_pair(provider[provider_index], copied[copied_index]))
    return sorted(pairs, key=lambda row: (row["provider_opened_at"], row["provider_ticket"]))


def _pair(provider: dict[str, object], copied: dict[str, object]) -> dict[str, object]:
    side = 1.0 if provider["side"] == "buy" else -1.0
    provider_entry = float(provider["entry_price"])
    copied_entry = float(copied["entry_price"])
    provider_exit = _price(provider.get("exit_price"))
    copied_exit = _price(copied.get("exit_price"))
    provider_path = _return_bps(provider_entry, provider_exit, side)
    copied_at_provider_exit = _return_bps(copied_entry, provider_exit, side)
    provider_at_copied_exit = _return_bps(provider_entry, copied_exit, side)
    copied_path = _return_bps(copied_entry, copied_exit, side)
    return {
        "provider_ticket": str(provider["ticket"]),
        "copied_ticket": str(copied["ticket"]),
        "symbol": str(provider["symbol"]),
        "side": str(provider["side"]),
        "provider_opened_at": _dt(provider["opened_at"]),
        "copied_opened_at": _dt(copied["opened_at"]),
        "entry_lag_seconds": (
            _dt(copied["opened_at"]) - _dt(provider["opened_at"])
        ).total_seconds(),
        "provider_closed_at": _optional_dt(provider.get("closed_at")),
        "copied_closed_at": _optional_dt(copied.get("closed_at")),
        "exit_lag_seconds": _time_difference(copied.get("closed_at"), provider.get("closed_at")),
        "provider_volume_lots": float(provider["volume_lots"]),
        "copied_volume_lots": float(copied["volume_lots"]),
        "volume_ratio": float(copied["volume_lots"]) / float(provider["volume_lots"]),
        "provider_entry_price": provider_entry,
        "copied_entry_price": copied_entry,
        "provider_exit_price": provider_exit,
        "copied_exit_price": copied_exit,
        "adverse_entry_bps": side * (copied_entry - provider_entry) / provider_entry * 10_000,
        "provider_path_bps": provider_path,
        "copied_entry_provider_exit_bps": copied_at_provider_exit,
        "provider_entry_copied_exit_bps": provider_at_copied_exit,
        "copied_path_bps": copied_path,
        "entry_effect_bps": _difference(copied_at_provider_exit, provider_path),
        "exit_effect_bps": _difference(copied_path, copied_at_provider_exit),
    }


def _load(path: Path, expected_role: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    meta = json.loads(path.read_text(encoding="utf-8"))
    if meta.get("source_role") != expected_role:
        raise ValueError(f"expected {expected_role} dataset")
    if not meta.get("research_only") or not meta.get("external_non_executable"):
        raise ValueError("trade observations must be research-only and non-executable")
    rows: list[dict[str, object]] = []
    for partition in meta.get("partitions", []):
        rows.extend(pq.read_table(partition).to_pylist())
    return meta, rows


def _return_bps(entry: float, exit_price: float | None, side: float) -> float | None:
    return side * (exit_price - entry) / entry * 10_000 if exit_price is not None else None


def _difference(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def _price(value: object) -> float | None:
    return float(value) if value is not None else None


def _dt(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("trade timestamp is invalid")
    return value.astimezone(UTC)


def _optional_dt(value: object) -> datetime | None:
    return _dt(value) if value is not None else None


def _time_difference(left: object, right: object) -> float | None:
    if left is None or right is None:
        return None
    return (_dt(left) - _dt(right)).total_seconds()


def _values(rows: list[dict[str, object]], field: str) -> list[float]:
    return [float(row[field]) for row in rows if row[field] is not None]


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _positive_ratio(values: list[float]) -> float | None:
    return sum(value > 0 for value in values) / len(values) if values else None


def _pair_schema() -> pa.Schema:
    return pa.schema(
        [
            ("provider_ticket", pa.string()), ("copied_ticket", pa.string()),
            ("symbol", pa.string()), ("side", pa.string()),
            ("provider_opened_at", pa.timestamp("us", tz="UTC")),
            ("copied_opened_at", pa.timestamp("us", tz="UTC")),
            ("entry_lag_seconds", pa.float64()),
            ("provider_closed_at", pa.timestamp("us", tz="UTC")),
            ("copied_closed_at", pa.timestamp("us", tz="UTC")),
            ("exit_lag_seconds", pa.float64()),
            ("provider_volume_lots", pa.float64()), ("copied_volume_lots", pa.float64()),
            ("volume_ratio", pa.float64()), ("provider_entry_price", pa.float64()),
            ("copied_entry_price", pa.float64()), ("provider_exit_price", pa.float64()),
            ("copied_exit_price", pa.float64()), ("adverse_entry_bps", pa.float64()),
            ("provider_path_bps", pa.float64()),
            ("copied_entry_provider_exit_bps", pa.float64()),
            ("provider_entry_copied_exit_bps", pa.float64()),
            ("copied_path_bps", pa.float64()), ("entry_effect_bps", pa.float64()),
            ("exit_effect_bps", pa.float64()),
        ]
    )
