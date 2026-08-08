from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .snapshots import latest_valid_snapshots


@dataclass(frozen=True)
class ParquetDatasetManifest:
    dataset_id: str
    created_at: str
    source_root: str
    row_count: int
    duplicate_rows_removed: int
    partitions: list[str]
    source_sha256: list[str]


def _tick_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("record_type") != "tick":
                continue
            received = datetime.strptime(row["received_utc"], "%Y.%m.%d %H:%M:%S").replace(
                tzinfo=UTC
            )
            rows.append(
                {
                    "received_utc": received,
                    "server_time": row["server_time"],
                    "session_id": row.get("session_id", ""),
                    "source_sequence": row.get("source_sequence", ""),
                    "broker": row["broker"],
                    "server": row["server"],
                    "symbol": row["symbol"],
                    "bid": float(row["bid"]),
                    "ask": float(row["ask"]),
                    "spread_points": float(row["spread_points"]),
                }
            )
    return rows


def build_parquet_dataset(source_root: Path, output_root: Path) -> ParquetDatasetManifest:
    snapshots, invalid = latest_valid_snapshots(source_root)
    if invalid:
        raise ValueError(f"cannot build dataset with {invalid} invalid snapshots")
    all_rows: list[dict[str, object]] = []
    source_hashes: list[str] = []
    for snapshot in snapshots:
        content = snapshot.read_bytes()
        source_hashes.append(hashlib.sha256(content).hexdigest())
        all_rows.extend(_tick_rows(snapshot))
    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for row in all_rows:
        key = (
            row["broker"],
            row["server"],
            row["symbol"],
            row["session_id"],
            row["source_sequence"],
            row["received_utc"],
        )
        unique[key] = row
    rows = sorted(unique.values(), key=lambda row: (row["received_utc"], row["source_sequence"]))
    canonical = json.dumps(
        [{**row, "received_utc": row["received_utc"].isoformat()} for row in rows],
        sort_keys=True,
    ).encode()
    dataset_id = "xauusd-" + hashlib.sha256(canonical).hexdigest()[:16]
    dataset_root = output_root / dataset_id
    partitions: list[str] = []
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        day = row["received_utc"].strftime("%Y-%m-%d")
        grouped.setdefault(day, []).append(row)
    for _day, day_rows in grouped.items():
        timestamp = day_rows[0]["received_utc"]
        folder = (
            dataset_root / f"year={timestamp:%Y}" / f"month={timestamp:%m}" / f"day={timestamp:%d}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "ticks.parquet"
        if not path.exists():
            pq.write_table(pa.Table.from_pylist(day_rows), path, compression="zstd")
        partitions.append(str(path))
    manifest = ParquetDatasetManifest(
        dataset_id=dataset_id,
        created_at=datetime.now(UTC).isoformat(),
        source_root=str(source_root),
        row_count=len(rows),
        duplicate_rows_removed=len(all_rows) - len(rows),
        partitions=partitions,
        source_sha256=sorted(source_hashes),
    )
    manifest_path = dataset_root / "manifest.json"
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
    return manifest
