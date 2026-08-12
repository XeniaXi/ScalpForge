from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class GoldEpisodeConfig:
    volatility_expansion_threshold: float = 1.5
    displacement_atr_threshold: float = 1.5
    minimum_path_efficiency: float = 0.35
    family_cooldown_seconds: int = 28_800
    schema_revision: int = 1


@dataclass(frozen=True)
class GoldEpisodeManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_state_dataset_id: str
    source_state_manifest: str
    row_count: int
    family_counts: dict[str, int]
    config: dict[str, object]
    episode_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    evaluation_role: str = "development_only"
    holdout_eligible: bool = False
    research_only: bool = True
    real_money_enabled: bool = False


def build_gold_strategy_episodes(
    states: pa.Table, config: GoldEpisodeConfig | None = None
) -> pa.Table:
    cfg = config or GoldEpisodeConfig()
    required = {
        "occurred_at",
        "feature_available_at",
        "h1_trend_side",
        "h4_return_bps",
        "m15_displacement_atr",
        "volatility_expansion_ratio",
        "path_efficiency_1800s",
        "fvg_active",
        "fvg_side",
        "fvg_mitigated",
        "boundary_rejection_side_4h",
    }
    if not required.issubset(states.column_names):
        raise ValueError("gold state source lacks required causal columns")
    timestamp_columns = {"occurred_at", "feature_available_at"}
    values = {
        name: states[name].to_pylist()
        for name in states.column_names
        if name not in timestamp_columns
    }
    timestamps = _timestamps(states["occurred_at"])
    available = _timestamps(states["feature_available_at"])
    previous_active: dict[str, bool] = {}
    last_emitted: dict[str, datetime] = {}
    output: list[dict[str, object]] = []
    for index, timestamp in enumerate(timestamps):
        row = {name: values[name][index] for name in values}
        candidates = _candidates(row, cfg)
        active_families = {family for family, _, _ in candidates}
        for family, side, strength in candidates:
            rising = not previous_active.get(family, False)
            cooled = (
                family not in last_emitted
                or timestamp - last_emitted[family]
                >= timedelta(seconds=cfg.family_cooldown_seconds)
            )
            if not (rising and cooled):
                continue
            episode_key = f"{family}|{timestamp.isoformat()}|{side}"
            output.append(
                {
                    "episode_id": hashlib.sha256(episode_key.encode()).hexdigest()[:20],
                    "occurred_at": timestamp,
                    "feature_available_at": available[index],
                    "family": family,
                    "side": side,
                    "signal_strength": strength,
                    "h1_trend_side": row["h1_trend_side"],
                    "h4_return_bps": row["h4_return_bps"],
                    "m15_displacement_atr": row["m15_displacement_atr"],
                    "volatility_expansion_ratio": row["volatility_expansion_ratio"],
                    "path_efficiency_1800s": row["path_efficiency_1800s"],
                    "simultaneous_family_count": len(active_families),
                }
            )
            last_emitted[family] = timestamp
        for family in set(previous_active) | active_families:
            previous_active[family] = family in active_families
    return pa.Table.from_pylist(output)


def write_gold_strategy_episodes(
    state_manifest: Path, output_root: Path, config: GoldEpisodeConfig | None = None
) -> GoldEpisodeManifest:
    cfg = config or GoldEpisodeConfig()
    meta = json.loads(state_manifest.read_text(encoding="utf-8"))
    if meta.get("point_in_time") is not True or meta.get("labels_included") is not False:
        raise ValueError("state source must be point-in-time and label-free")
    serialized = json.loads(json.dumps(asdict(cfg)))
    identity = json.dumps(
        {"source": meta["dataset_id"], "config": serialized}, sort_keys=True
    ).encode()
    dataset_id = "xauusd-gold-episodes-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return GoldEpisodeManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        table = build_gold_strategy_episodes(_read(state_manifest, meta), cfg)
        if not table.num_rows:
            raise ValueError("state source produced no strategy episodes")
        partition = staging / "episodes.parquet"
        pq.write_table(table, partition, compression="zstd")
        families = table["family"].to_pylist()
        counts = {family: families.count(family) for family in sorted(set(families))}
        manifest = GoldEpisodeManifest(
            dataset_id,
            1,
            datetime.now(UTC).isoformat(),
            str(meta["dataset_id"]),
            str(state_manifest.resolve()),
            table.num_rows,
            counts,
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


def _candidates(row: dict[str, object], cfg: GoldEpisodeConfig) -> list[tuple[str, int, float]]:
    result: list[tuple[str, int, float]] = []
    trend = int(row["h1_trend_side"] or 0)
    h4_return = row["h4_return_bps"]
    efficiency = row["path_efficiency_1800s"]
    aligned = trend and h4_return is not None and float(h4_return) * trend > 0
    efficient = efficiency is not None and float(efficiency) >= cfg.minimum_path_efficiency
    if aligned and efficient:
        result.append(("trend_continuation", trend, abs(float(h4_return))))
    rejection = int(row["boundary_rejection_side_4h"] or 0)
    if rejection:
        result.append(("boundary_rejection", rejection, 1.0))
    displacement = row["m15_displacement_atr"]
    if (
        aligned
        and efficient
        and displacement is not None
        and float(displacement) >= cfg.displacement_atr_threshold
    ):
        result.append(("displacement_persistence", trend, float(displacement)))
    expansion = row["volatility_expansion_ratio"]
    if (
        aligned
        and efficient
        and expansion is not None
        and float(expansion) >= cfg.volatility_expansion_threshold
    ):
        result.append(("volatility_expansion", trend, float(expansion)))
    if row["fvg_active"] and row["fvg_mitigated"] and int(row["fvg_side"] or 0):
        result.append(("fvg_retracement", int(row["fvg_side"]), 1.0))
    return result


def _read(manifest: Path, meta: dict[str, object]) -> pa.Table:
    root = manifest.resolve().parent
    tables = []
    for stored in meta.get("partitions", []):  # type: ignore[union-attr]
        path = Path(str(stored)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("state partition escapes manifest directory")
        tables.append(pq.read_table(path))
    return pa.concat_tables(tables)


def _timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
