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
class SessionEpisodeConfig:
    schema_revision: int = 2
    label_horizon_seconds: int = 300
    maximum_spread_bps: float = 4.0

    def __post_init__(self) -> None:
        if self.schema_revision != 2 or self.label_horizon_seconds != 300:
            raise ValueError("only preregistered 300-second session episodes are supported")
        if self.maximum_spread_bps <= 0:
            raise ValueError("maximum spread must be positive")


@dataclass(frozen=True)
class SessionEpisodeManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    feature_dataset_id: str
    session_range_dataset_id: str
    structural_dataset_id: str
    outcome_dataset_id: str
    row_count: int
    config: dict[str, object]
    feature_columns: list[str]
    label_columns: list[str]
    feature_partition: str
    label_partition: str
    join_key: str = "episode_id"
    point_in_time: bool = True
    labels_physically_separate: bool = True
    future_information_in_feature_partition: bool = False
    future_information_in_label_partition: bool = True
    external_non_executable: bool = True


def write_session_episode_dataset(
    feature_manifest: Path,
    session_manifest: Path,
    structural_manifest: Path,
    outcome_manifest: Path,
    output_root: Path,
    config: SessionEpisodeConfig | None = None,
    *,
    batch_size: int = 50_000,
) -> SessionEpisodeManifest:
    cfg = config or SessionEpisodeConfig()
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    feature_meta = _meta(feature_manifest)
    session_meta = _meta(session_manifest)
    structure_meta = _meta(structural_manifest)
    outcome_meta = _meta(outcome_manifest)
    _validate(feature_meta, session_meta, structure_meta, outcome_meta, cfg)
    serialized = asdict(cfg)
    identity = json.dumps(
        {
            "features": feature_meta["dataset_id"],
            "sessions": session_meta["dataset_id"],
            "structure": structure_meta["dataset_id"],
            "outcomes": outcome_meta["dataset_id"],
            "config": serialized,
        },
        sort_keys=True,
    ).encode()
    dataset_id = "xauusd-session-episodes-" + hashlib.sha256(identity).hexdigest()[:16]
    root = output_root / dataset_id
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return SessionEpisodeManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    feature_path = staging / "episodes.parquet"
    label_path = staging / "labels.parquet"
    feature_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    windows = [item["name"] for item in session_meta["session_config"]["windows"]]
    previous_sides = {window: 0 for window in windows}
    emitted_windows: set[str] = set()
    current_day: str | None = None
    try:
        for features, sessions, structure, outcomes in _aligned_batches(
            feature_manifest,
            feature_meta,
            session_manifest,
            session_meta,
            structural_manifest,
            structure_meta,
            outcome_manifest,
            outcome_meta,
            windows,
            cfg,
            batch_size,
        ):
            timestamps = _utc_timestamps(features["occurred_at"])
            days = sessions["session_day_utc"].to_pylist()
            for index, timestamp in enumerate(timestamps):
                day = str(days[index])
                if day != current_day:
                    current_day = day
                    previous_sides = {window: 0 for window in windows}
                    emitted_windows = set()
                for window in windows:
                    side = int(sessions[f"{window}_breakout_side"][index].as_py() or 0)
                    episode_start = (
                        side != 0
                        and previous_sides[window] == 0
                        and window not in emitted_windows
                    )
                    previous_sides[window] = side
                    if not episode_start:
                        continue
                    spread = float(features["spread_bps"][index].as_py())
                    if spread > cfg.maximum_spread_bps:
                        continue
                    prefix = f"h{cfg.label_horizon_seconds}"
                    if not outcomes[f"{prefix}_valid"][index].as_py():
                        continue
                    episode_id = hashlib.sha256(
                        f"{timestamp.isoformat()}|{window}|{side}".encode()
                    ).hexdigest()[:24]
                    feature_rows.append(
                        _feature_row(
                            episode_id,
                            timestamp,
                            window,
                            side,
                            index,
                            features,
                            sessions,
                            structure,
                        )
                    )
                    label_rows.append(
                        _label_row(
                            episode_id,
                            timestamp,
                            side,
                            index,
                            outcomes,
                            cfg.label_horizon_seconds,
                        )
                    )
                    emitted_windows.add(window)
        if not feature_rows:
            raise ValueError("no eligible session episodes were found")
        feature_table = pa.Table.from_pylist(feature_rows, schema=_feature_schema())
        label_table = pa.Table.from_pylist(label_rows, schema=_label_schema())
        pq.write_table(feature_table, feature_path, compression="zstd")
        pq.write_table(label_table, label_path, compression="zstd")
        final_feature = root / feature_path.name
        final_label = root / label_path.name
        manifest = SessionEpisodeManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            feature_dataset_id=str(feature_meta["dataset_id"]),
            session_range_dataset_id=str(session_meta["dataset_id"]),
            structural_dataset_id=str(structure_meta["dataset_id"]),
            outcome_dataset_id=str(outcome_meta["dataset_id"]),
            row_count=len(feature_rows),
            config=serialized,
            feature_columns=feature_table.column_names,
            label_columns=label_table.column_names,
            feature_partition=str(final_feature),
            label_partition=str(final_label),
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _feature_row(episode_id, timestamp, window, side, index, features, sessions, structure):
    return {
        "episode_id": episode_id,
        "occurred_at": timestamp,
        "window": window,
        "side": side,
        "spread_bps": float(features["spread_bps"][index].as_py()),
        "spread_shock_ratio": float(features["spread_shock_ratio"][index].as_py()),
        "tick_intensity_ratio": float(features["tick_intensity_ratio"][index].as_py()),
        "realized_volatility_60s": float(
            features["realized_volatility_60s"][index].as_py()
        ),
        "return_5s_signed": _signed(features["return_5s"][index].as_py(), side),
        "return_30s_signed": _signed(features["return_30s"][index].as_py(), side),
        "return_60s_signed": _signed(features["return_60s"][index].as_py(), side),
        "range_width_bps": _float(sessions[f"{window}_width_bps"][index].as_py()),
        "breakout_distance_bps": _float(
            sessions[f"{window}_breakout_distance_bps"][index].as_py()
        ),
        "distance_from_tick_vwap_signed_bps": side
        * float(structure["distance_from_tick_vwap_bps"][index].as_py()),
        "compression_60_to_300": _float(
            structure["compression_60_to_300"][index].as_py()
        ),
    }


def _label_row(episode_id, timestamp, side, index, outcomes, horizon):
    prefix = f"h{horizon}"
    direction = "long" if side > 0 else "short"
    return {
        "episode_id": episode_id,
        "occurred_at": timestamp,
        "gross_bps": float(outcomes[f"{prefix}_{direction}_gross_bps"][index].as_py()),
        "net_bps": float(outcomes[f"{prefix}_{direction}_net_bps"][index].as_py()),
        "mfe_bps": float(outcomes[f"{prefix}_{direction}_mfe_bps"][index].as_py()),
        "mae_bps": float(outcomes[f"{prefix}_{direction}_mae_bps"][index].as_py()),
    }


def _validate(features, sessions, structure, outcomes, cfg):
    for name, meta in (("features", features), ("sessions", sessions), ("structure", structure)):
        if meta.get("point_in_time") is not True or meta.get("labels_included") is not False:
            raise ValueError(f"{name} must be point-in-time and label-free")
    source_id = features.get("dataset_id")
    if sessions.get("source_feature_dataset_id") != source_id:
        raise ValueError("session ranges do not belong to feature dataset")
    if structure.get("source_feature_dataset_id") != source_id:
        raise ValueError("structure does not belong to feature dataset")
    if outcomes.get("source_feature_dataset_id") != source_id:
        raise ValueError("outcomes do not belong to feature dataset")
    if str(cfg.label_horizon_seconds) not in outcomes.get("horizon_partitions", {}):
        raise ValueError("outcome horizon is unavailable")


def _aligned_batches(fm, fmeta, sm, smeta, stm, stmeta, om, ometa, windows, cfg, size):
    feature_columns = [
        "occurred_at", "spread_bps", "spread_shock_ratio", "tick_intensity_ratio",
        "realized_volatility_60s", "return_5s", "return_30s", "return_60s",
    ]
    session_columns = ["occurred_at", "session_day_utc"]
    for window in windows:
        session_columns.extend(
            [
                f"{window}_width_bps", f"{window}_breakout_side",
                f"{window}_breakout_distance_bps",
            ]
        )
    structure_columns = [
        "occurred_at", "distance_from_tick_vwap_bps", "compression_60_to_300",
    ]
    prefix = f"h{cfg.label_horizon_seconds}"
    outcome_columns = [
        "occurred_at", f"{prefix}_valid", f"{prefix}_long_gross_bps",
        f"{prefix}_short_gross_bps", f"{prefix}_long_net_bps",
        f"{prefix}_short_net_bps", f"{prefix}_long_mfe_bps",
        f"{prefix}_short_mfe_bps", f"{prefix}_long_mae_bps", f"{prefix}_short_mae_bps",
    ]
    streams = [
        _batches(_paths(fm, fmeta), feature_columns, size),
        _batches(_paths(sm, smeta), session_columns, size),
        _batches(_paths(stm, stmeta), structure_columns, size),
        _batches([_horizon_path(om, ometa, cfg.label_horizon_seconds)], outcome_columns, size),
    ]
    sentinel = object()
    while True:
        tables = [next(stream, sentinel) for stream in streams]
        if all(table is sentinel for table in tables):
            return
        if any(table is sentinel for table in tables):
            raise ValueError("research datasets have different row counts")
        aligned = [table for table in tables if isinstance(table, pa.Table)]
        if len({table.num_rows for table in aligned}) != 1:
            raise ValueError("research batch sizes do not align")
        timestamps = [_utc_timestamps(table["occurred_at"]) for table in aligned]
        if any(values != timestamps[0] for values in timestamps[1:]):
            raise ValueError("research timestamps do not align")
        yield tuple(aligned)


def _feature_schema():
    return pa.schema(
        [
            ("episode_id", pa.string()), ("occurred_at", pa.timestamp("us", tz="UTC")),
            ("window", pa.string()), ("side", pa.int64()), ("spread_bps", pa.float64()),
            ("spread_shock_ratio", pa.float64()), ("tick_intensity_ratio", pa.float64()),
            ("realized_volatility_60s", pa.float64()), ("return_5s_signed", pa.float64()),
            ("return_30s_signed", pa.float64()), ("return_60s_signed", pa.float64()),
            ("range_width_bps", pa.float64()), ("breakout_distance_bps", pa.float64()),
            ("distance_from_tick_vwap_signed_bps", pa.float64()),
            ("compression_60_to_300", pa.float64()),
        ]
    )


def _label_schema():
    return pa.schema(
        [
            ("episode_id", pa.string()), ("occurred_at", pa.timestamp("us", tz="UTC")),
            ("gross_bps", pa.float64()), ("net_bps", pa.float64()),
            ("mfe_bps", pa.float64()), ("mae_bps", pa.float64()),
        ]
    )


def _paths(manifest, meta):
    root = manifest.resolve().parent
    paths = [Path(str(value)).resolve() for value in meta.get("partitions", [])]
    if not paths or any(not path.is_relative_to(root) for path in paths):
        raise ValueError("manifest partitions are missing or escape their dataset")
    return paths


def _horizon_path(manifest, meta, horizon):
    root = manifest.resolve().parent
    path = Path(str(meta["horizon_partitions"][str(horizon)])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("outcome partition escapes its dataset")
    return path


def _batches(paths, columns, size):
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=size, columns=columns):
            yield pa.Table.from_batches([batch])


def _utc_timestamps(column):
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]


def _signed(value, side):
    return float(value) * side if value is not None else None


def _float(value):
    return float(value) if value is not None else None


def _meta(path):
    return json.loads(path.read_text(encoding="utf-8"))
