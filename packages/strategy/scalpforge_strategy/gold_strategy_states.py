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
class GoldStateConfig:
    m15_seconds: int = 900
    h1_seconds: int = 3600
    h4_seconds: int = 14400
    ema_fast_bars: int = 12
    ema_slow_bars: int = 26
    atr_bars: int = 14
    fvg_expiry_m15_bars: int = 20
    displacement_atr_multiple: float = 1.5
    schema_revision: int = 1


@dataclass(frozen=True)
class GoldStateManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_multi_hour_dataset_id: str
    source_multi_hour_manifest: str
    row_count: int
    config: dict[str, object]
    state_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    evaluation_role: str = "development_only"
    holdout_eligible: bool = False
    research_only: bool = True
    real_money_enabled: bool = False


def build_gold_strategy_states(source: pa.Table, config: GoldStateConfig | None = None) -> pa.Table:
    cfg = config or GoldStateConfig()
    required = {
        "bar_open_at",
        "feature_available_at",
        "bar_open",
        "bar_high",
        "bar_low",
        "bar_close",
        "spread_bps",
        "volatility_expansion_ratio",
        "path_efficiency_1800s",
        "prior_high_14400s",
        "prior_low_14400s",
    }
    if not required.issubset(source.column_names):
        raise ValueError("multi-hour source lacks required causal state columns")
    rows = _rows_without_timezone_conversion(source)
    completed = {900: [], 3600: [], 14400: []}
    forming: dict[int, dict[str, object] | None] = {900: None, 3600: None, 14400: None}
    active_fvg: dict[str, object] | None = None
    output: list[dict[str, object]] = []
    for row in rows:
        timestamp = row["bar_open_at"]
        for seconds in forming:
            bucket = int(timestamp.timestamp()) // seconds * seconds
            current = forming[seconds]
            if current is not None and current["bucket"] != bucket:
                completed[seconds].append(current)
                forming[seconds] = None
            forming[seconds] = _update_bar(forming[seconds], row, bucket)
        m15 = completed[900]
        h1 = completed[3600]
        h4 = completed[14400]
        if len(m15) >= 3:
            candidate = _fvg(m15[-3:], cfg, len(m15) - 1)
            if candidate is not None:
                active_fvg = candidate
        if active_fvg is not None:
            age = len(m15) - 1 - int(active_fvg["formed_index"])
            close = float(row["bar_close"])
            mitigated = float(active_fvg["low"]) <= close <= float(active_fvg["high"])
            if age > cfg.fvg_expiry_m15_bars:
                active_fvg = None
            elif mitigated:
                active_fvg = {**active_fvg, "mitigated": True}
        state = {
            "occurred_at": row["occurred_at"],
            "feature_available_at": row["feature_available_at"],
            "m15_close": _last(m15, "close"),
            "m15_atr": _atr(m15, cfg.atr_bars),
            "m15_displacement_atr": _displacement(m15, cfg),
            "h1_close": _last(h1, "close"),
            "h1_ema_fast": _ema(h1, cfg.ema_fast_bars),
            "h1_ema_slow": _ema(h1, cfg.ema_slow_bars),
            "h1_atr": _atr(h1, cfg.atr_bars),
            "h1_trend_side": _trend(h1, cfg),
            "h4_close": _last(h4, "close"),
            "h4_return_bps": _return(h4, 2),
            "volatility_expansion_ratio": row["volatility_expansion_ratio"],
            "path_efficiency_1800s": row["path_efficiency_1800s"],
            "fvg_active": active_fvg is not None,
            "fvg_side": int(active_fvg["side"]) if active_fvg else 0,
            "fvg_low": active_fvg["low"] if active_fvg else None,
            "fvg_high": active_fvg["high"] if active_fvg else None,
            "fvg_mitigated": bool(active_fvg.get("mitigated")) if active_fvg else False,
            "boundary_rejection_side_4h": _rejection(row),
        }
        output.append(state)
    return pa.Table.from_pylist(output)


def write_gold_strategy_states(
    source_manifest: Path, output_root: Path, config: GoldStateConfig | None = None
) -> GoldStateManifest:
    cfg = config or GoldStateConfig()
    meta = json.loads(source_manifest.read_text(encoding="utf-8"))
    if meta.get("point_in_time") is not True or meta.get("labels_included") is not False:
        raise ValueError("source must be point-in-time and label-free")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-gold-states-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return GoldStateManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        table = build_gold_strategy_states(_read(source_manifest, meta), cfg)
        partition = staging / "states.parquet"
        pq.write_table(table, partition, compression="zstd", row_group_size=10_000)
        manifest = GoldStateManifest(
            dataset_id,
            1,
            datetime.now(UTC).isoformat(),
            str(meta["dataset_id"]),
            str(source_manifest.resolve()),
            table.num_rows,
            serialized,
            table.column_names,
            [str(root / partition.name)],
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _update_bar(
    current: dict[str, object] | None, row: dict[str, object], bucket: int
) -> dict[str, object]:
    if current is None:
        return {
            "bucket": bucket,
            "open": row["bar_open"],
            "high": row["bar_high"],
            "low": row["bar_low"],
            "close": row["bar_close"],
        }
    current["high"] = max(float(current["high"]), float(row["bar_high"]))
    current["low"] = min(float(current["low"]), float(row["bar_low"]))
    current["close"] = row["bar_close"]
    return current


def _last(bars: list[dict[str, object]], name: str) -> float | None:
    return float(bars[-1][name]) if bars else None


def _ema(bars: list[dict[str, object]], period: int) -> float | None:
    if len(bars) < period:
        return None
    value = float(bars[-period]["close"])
    alpha = 2 / (period + 1)
    for bar in bars[-period + 1 :]:
        value = alpha * float(bar["close"]) + (1 - alpha) * value
    return value


def _atr(bars: list[dict[str, object]], period: int) -> float | None:
    if len(bars) < period + 1:
        return None
    values = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:], strict=True):
        values.append(
            max(
                float(current["high"]) - float(current["low"]),
                abs(float(current["high"]) - float(previous["close"])),
                abs(float(current["low"]) - float(previous["close"])),
            )
        )
    return sum(values) / len(values)


def _trend(bars: list[dict[str, object]], cfg: GoldStateConfig) -> int:
    fast, slow = _ema(bars, cfg.ema_fast_bars), _ema(bars, cfg.ema_slow_bars)
    return (
        1
        if fast is not None and slow is not None and fast > slow
        else (-1 if fast is not None and slow is not None and fast < slow else 0)
    )


def _return(bars: list[dict[str, object]], count: int) -> float | None:
    return (
        (float(bars[-1]["close"]) / float(bars[-count]["open"]) - 1) * 10_000
        if len(bars) >= count
        else None
    )


def _displacement(bars: list[dict[str, object]], cfg: GoldStateConfig) -> float | None:
    atr = _atr(bars, cfg.atr_bars)
    return abs(float(bars[-1]["close"]) - float(bars[-1]["open"])) / atr if bars and atr else None


def _fvg(
    bars: list[dict[str, object]], cfg: GoldStateConfig, index: int
) -> dict[str, object] | None:
    first, middle, third = bars
    atr = _atr([first, middle, third], 2)
    if (
        not atr
        or abs(float(middle["close"]) - float(middle["open"])) < cfg.displacement_atr_multiple * atr
    ):
        return None
    if float(third["low"]) > float(first["high"]):
        return {"side": 1, "low": first["high"], "high": third["low"], "formed_index": index}
    if float(third["high"]) < float(first["low"]):
        return {"side": -1, "low": third["high"], "high": first["low"], "formed_index": index}
    return None


def _rejection(row: dict[str, object]) -> int:
    high, low = row["prior_high_14400s"], row["prior_low_14400s"]
    if (
        high is not None
        and float(row["bar_high"]) > float(high)
        and float(row["bar_close"]) < float(high)
    ):
        return -1
    if (
        low is not None
        and float(row["bar_low"]) < float(low)
        and float(row["bar_close"]) > float(low)
    ):
        return 1
    return 0


def _read(manifest: Path, meta: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    tables = []
    for stored in meta.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("source partition escapes manifest directory")
        tables.append(pq.read_table(path))
    return pa.concat_tables(tables)


def _rows_without_timezone_conversion(table: pa.Table) -> list[dict[str, object]]:
    timestamp_columns = {"bar_open_at", "occurred_at", "feature_available_at"}
    values: dict[str, list[object]] = {}
    for name in table.column_names:
        column = table[name]
        if name in timestamp_columns:
            divisor = {
                "s": 1,
                "ms": 1_000,
                "us": 1_000_000,
                "ns": 1_000_000_000,
            }[column.type.unit]
            values[name] = [
                datetime.fromtimestamp(value / divisor, UTC)
                for value in column.cast(pa.int64()).to_pylist()
            ]
        else:
            values[name] = column.to_pylist()
    return [
        {name: values[name][index] for name in table.column_names}
        for index in range(table.num_rows)
    ]
