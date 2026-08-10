from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class JForexBatchIngestManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    provider: str
    venue: str
    instrument: str
    start_utc: str
    end_utc_exclusive: str
    batch_count: int
    empty_batch_count: int
    row_count: int
    duplicate_rows: int
    maximum_gap_seconds: float
    source_sha256: list[str]
    archived_manifests: list[str]
    partitions: list[str]
    volume_unit: str
    external_non_executable: bool = True


@dataclass(frozen=True)
class _Batch:
    manifest_path: Path
    csv_path: Path
    start: datetime
    end: datetime
    rows: int
    sha256: str


def ingest_jforex_batches(
    source_dir: Path,
    archive_root: Path,
    output_root: Path,
    *,
    copy_to_archive: bool = True,
    start_utc: datetime | None = None,
    end_utc_exclusive: datetime | None = None,
) -> JForexBatchIngestManifest:
    batches = _validated_batches(source_dir)
    if start_utc is not None or end_utc_exclusive is not None:
        if (
            start_utc is not None
            and end_utc_exclusive is not None
            and start_utc >= end_utc_exclusive
        ):
            raise ValueError("JForex ingestion range must be positive")
        batches = [
            batch
            for batch in batches
            if (start_utc is None or batch.start >= start_utc)
            and (end_utc_exclusive is None or batch.end <= end_utc_exclusive)
        ]
        if not batches:
            raise ValueError("no JForex batches fall inside requested range")
    source_hashes = [batch.sha256 for batch in batches]
    identity = json.dumps(
        {
            "schema_version": 1,
            "provider": "dukascopy-jforex",
            "venue": "SWFX",
            "instrument": "XAUUSD",
            "batches": [
                [batch.start.isoformat(), batch.end.isoformat(), batch.sha256] for batch in batches
            ],
        },
        sort_keys=True,
    ).encode()
    dataset_id = "xauusd-jforex-" + hashlib.sha256(identity).hexdigest()[:16]
    archived_manifests = (
        _archive(batches, archive_root)
        if copy_to_archive
        else [str(batch.manifest_path.resolve()) for batch in batches]
    )
    dataset_root = output_root / dataset_id
    manifest_path = dataset_root / "manifest.json"
    if manifest_path.is_file():
        return JForexBatchIngestManifest(
            **json.loads(manifest_path.read_text(encoding="utf-8"))
        )

    staging = output_root / f"{dataset_id}.partial"
    if staging.exists():
        raise ValueError(f"incomplete staging dataset exists: {staging}")
    staging.mkdir(parents=True)
    try:
        partitions, row_count, duplicates, maximum_gap = _write_parquet(batches, staging)
        manifest = JForexBatchIngestManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            provider="dukascopy-jforex",
            venue="SWFX",
            instrument="XAUUSD",
            start_utc=batches[0].start.isoformat(),
            end_utc_exclusive=batches[-1].end.isoformat(),
            batch_count=len(batches),
            empty_batch_count=sum(batch.rows == 0 for batch in batches),
            row_count=row_count,
            duplicate_rows=duplicates,
            maximum_gap_seconds=maximum_gap,
            source_sha256=source_hashes,
            archived_manifests=archived_manifests,
            partitions=[path.replace(str(staging), str(dataset_root), 1) for path in partitions],
            volume_unit="jforex_native_unknown",
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(dataset_root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validated_batches(source_dir: Path) -> list[_Batch]:
    batches: list[_Batch] = []
    for manifest_path in source_dir.glob("scalpforge_jforex_*.manifest.json"):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = {
            "schema_version": 1,
            "provider": "dukascopy",
            "venue": "SWFX",
            "instrument": "XAUUSD",
            "source": "jforex-IHistory.getTicks",
            "read_only": True,
            "external_non_executable": True,
        }
        if any(payload.get(key) != value for key, value in required.items()):
            raise ValueError(f"invalid JForex manifest contract: {manifest_path}")
        csv_path = manifest_path.parent / str(payload["csv"])
        if not csv_path.is_file():
            raise ValueError(f"missing JForex batch CSV: {csv_path}")
        digest = _sha256(csv_path)
        if digest != payload.get("sha256"):
            raise ValueError(f"checksum mismatch: {csv_path}")
        rows = _count_and_validate_header(csv_path)
        if rows != int(payload["rows"]):
            raise ValueError(f"row-count mismatch: {csv_path}")
        batches.append(
            _Batch(
                manifest_path=manifest_path,
                csv_path=csv_path,
                start=_utc(str(payload["start_utc"])),
                end=_utc(str(payload["end_utc_exclusive"])),
                rows=rows,
                sha256=digest,
            )
        )
    if not batches:
        raise ValueError("no JForex batch manifests found")
    batches.sort(key=lambda batch: batch.start)
    for previous, current in zip(batches, batches[1:], strict=False):
        if current.start < previous.end:
            raise ValueError("JForex batch intervals overlap")
    return batches


def _archive(batches: list[_Batch], archive_root: Path) -> list[str]:
    archived: list[str] = []
    for batch in batches:
        folder = (
            archive_root
            / f"{batch.start:%Y}"
            / f"{batch.start:%m}"
            / f"{batch.start:%d}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        for source in (batch.csv_path, batch.manifest_path):
            destination = folder / source.name
            if destination.exists():
                if _sha256(destination) != _sha256(source):
                    raise ValueError(f"archive conflict: {destination}")
            else:
                temporary = destination.with_suffix(destination.suffix + ".partial")
                shutil.copy2(source, temporary)
                temporary.replace(destination)
        archived.append(str(folder / batch.manifest_path.name))
    return archived


def _write_parquet(
    batches: list[_Batch], dataset_root: Path
) -> tuple[list[str], int, int, float]:
    partitions: list[str] = []
    writer: pq.ParquetWriter | None = None
    active_day: str | None = None
    buffer: list[dict[str, object]] = []
    previous: tuple[datetime, float, float] | None = None
    row_count = duplicates = 0
    maximum_gap = 0.0

    def flush() -> None:
        nonlocal buffer, writer
        if not buffer:
            return
        table = pa.Table.from_pylist(buffer)
        if writer is None:
            timestamp = buffer[0]["occurred_at"]
            folder = (
                dataset_root
                / f"year={timestamp:%Y}"
                / f"month={timestamp:%m}"
                / f"day={timestamp:%d}"
            )
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "ticks.parquet"
            writer = pq.ParquetWriter(path, table.schema, compression="zstd")
            partitions.append(str(path))
        writer.write_table(table)
        buffer = []

    try:
        for batch_number, batch in enumerate(batches, start=1):
            with batch.csv_path.open(encoding="utf-8-sig", newline="") as stream:
                for row_number, row in enumerate(csv.DictReader(stream), start=1):
                    timestamp = _utc(row["occurred_at"])
                    bid = float(row["bid"])
                    ask = float(row["ask"])
                    if not batch.start <= timestamp < batch.end:
                        raise ValueError(f"tick outside manifest interval: {batch.csv_path}")
                    if bid <= 0 or ask < bid:
                        raise ValueError(f"invalid quote: {batch.csv_path}:{row_number + 1}")
                    current = (timestamp, bid, ask)
                    if previous is not None:
                        if timestamp < previous[0]:
                            raise ValueError("ticks are not ordered across JForex batches")
                        if current == previous:
                            duplicates += 1
                        maximum_gap = max(
                            maximum_gap, (timestamp - previous[0]).total_seconds()
                        )
                    day = timestamp.date().isoformat()
                    if active_day is not None and day != active_day:
                        flush()
                        if writer is not None:
                            writer.close()
                        writer = None
                    active_day = day
                    naive_utc = timestamp.replace(tzinfo=None)
                    buffer.append(
                        {
                            "occurred_at": naive_utc,
                            "received_at": naive_utc,
                            "provider": "dukascopy-jforex",
                            "venue": "SWFX",
                            "instrument": "XAUUSD",
                            "bid": bid,
                            "ask": ask,
                            "bid_volume_raw": float(row["bid_volume"]),
                            "ask_volume_raw": float(row["ask_volume"]),
                            "volume_unit": "jforex_native_unknown",
                            "source_sequence": f"{batch_number}:{row['source_sequence']}",
                            "external_non_executable": True,
                        }
                    )
                    if len(buffer) >= 100_000:
                        flush()
                    previous = current
                    row_count += 1
        flush()
    finally:
        if writer is not None:
            writer.close()
    return partitions, row_count, duplicates, maximum_gap


def _count_and_validate_header(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "occurred_at",
            "bid",
            "ask",
            "bid_volume",
            "ask_volume",
            "source_sequence",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"invalid JForex CSV header: {path}")
        return sum(1 for _ in reader)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("JForex timestamp must include UTC offset")
    return parsed.astimezone(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
