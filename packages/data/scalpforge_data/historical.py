from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


@dataclass(frozen=True)
class HistoricalImportManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    provider: str
    venue: str
    instrument: str
    source_file: str
    source_sha256: str
    timezone_assumption: str
    price_scale: float
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    duplicate_rows: int
    gap_count: int
    maximum_gap_seconds: float
    partitions: list[str]
    external_non_executable: bool = True


@dataclass(frozen=True)
class HistoricalImportResult:
    manifest: HistoricalImportManifest
    manifest_path: str


@dataclass(frozen=True)
class _ValidationStats:
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    duplicate_rows: int
    gap_count: int
    maximum_gap_seconds: float


class HistoricalCsvNormalizer:
    """Normalize ordered bid/ask CSV history without loading the dataset into memory."""

    TIMESTAMP_COLUMNS = ("occurred_at", "timestamp", "datetime", "time")

    def normalize(
        self,
        source_path: Path,
        output_root: Path,
        *,
        provider: str,
        venue: str,
        instrument: str = "XAUUSD",
        source_timezone: str,
        price_scale: float = 1.0,
        gap_threshold_seconds: float = 30.0,
        batch_size: int = 100_000,
    ) -> HistoricalImportResult:
        if not provider.strip() or not venue.strip():
            raise ValueError("provider and venue must be non-empty")
        if price_scale <= 0:
            raise ValueError("price_scale must be positive")
        source_tz = _parse_timezone(source_timezone)
        source_hash = _sha256(source_path)
        dataset_key = json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_hash,
                "provider": provider,
                "venue": venue,
                "instrument": instrument,
                "source_timezone": source_timezone,
                "price_scale": price_scale,
            },
            sort_keys=True,
        ).encode()
        dataset_id = "xauusd-history-" + hashlib.sha256(dataset_key).hexdigest()[:16]
        dataset_root = output_root / dataset_id
        manifest_path = dataset_root / "manifest.json"
        if manifest_path.is_file():
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = HistoricalImportManifest(**stored)
            return HistoricalImportResult(manifest, str(manifest_path))

        stats = self._validate(
            source_path,
            source_tz=source_tz,
            price_scale=price_scale,
            gap_threshold_seconds=gap_threshold_seconds,
        )
        dataset_root.mkdir(parents=True, exist_ok=False)
        partitions = self._write_partitions(
            source_path,
            dataset_root,
            source_tz=source_tz,
            provider=provider,
            venue=venue,
            instrument=instrument,
            price_scale=price_scale,
            batch_size=batch_size,
        )
        manifest = HistoricalImportManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            provider=provider,
            venue=venue,
            instrument=instrument,
            source_file=source_path.name,
            source_sha256=source_hash,
            timezone_assumption=source_timezone,
            price_scale=price_scale,
            row_count=stats.row_count,
            first_timestamp=stats.first_timestamp,
            last_timestamp=stats.last_timestamp,
            duplicate_rows=stats.duplicate_rows,
            gap_count=stats.gap_count,
            maximum_gap_seconds=stats.maximum_gap_seconds,
            partitions=partitions,
        )
        manifest_path.write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
        return HistoricalImportResult(manifest, str(manifest_path))

    def _validate(
        self,
        source_path: Path,
        *,
        source_tz: tzinfo,
        price_scale: float,
        gap_threshold_seconds: float,
    ) -> _ValidationStats:
        previous: tuple[datetime, float, float] | None = None
        first: datetime | None = None
        last: datetime | None = None
        rows = duplicates = gaps = 0
        maximum_gap = 0.0
        for row_number, row in self._rows(source_path, source_tz, price_scale):
            timestamp, bid, ask = row
            if bid <= 0 or ask <= 0 or ask < bid:
                raise ValueError(f"row {row_number}: invalid bid/ask")
            if previous is not None:
                if timestamp < previous[0]:
                    raise ValueError(f"row {row_number}: timestamps are not ordered")
                if row == previous:
                    duplicates += 1
                gap = (timestamp - previous[0]).total_seconds()
                maximum_gap = max(maximum_gap, gap)
                if gap > gap_threshold_seconds:
                    gaps += 1
            first = first or timestamp
            last = timestamp
            previous = row
            rows += 1
        if rows == 0:
            raise ValueError("source contains no tick rows")
        return _ValidationStats(
            row_count=rows,
            first_timestamp=first.isoformat() if first else None,
            last_timestamp=last.isoformat() if last else None,
            duplicate_rows=duplicates,
            gap_count=gaps,
            maximum_gap_seconds=maximum_gap,
        )

    def _write_partitions(
        self,
        source_path: Path,
        dataset_root: Path,
        *,
        source_tz: tzinfo,
        provider: str,
        venue: str,
        instrument: str,
        price_scale: float,
        batch_size: int,
    ) -> list[str]:
        partitions: list[str] = []
        batch: list[dict[str, object]] = []
        current_day: str | None = None
        writer: pq.ParquetWriter | None = None

        def flush() -> None:
            nonlocal batch, writer
            if not batch:
                return
            table = pa.Table.from_pylist(batch)
            if writer is None:
                timestamp = batch[0]["occurred_at"]
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
            batch = []

        try:
            for row_number, (timestamp, bid, ask) in self._rows(
                source_path, source_tz, price_scale
            ):
                day = timestamp.date().isoformat()
                if current_day is not None and day != current_day:
                    flush()
                    if writer is not None:
                        writer.close()
                    writer = None
                current_day = day
                parquet_timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
                batch.append(
                    {
                        "occurred_at": parquet_timestamp,
                        "received_at": parquet_timestamp,
                        "provider": provider,
                        "venue": venue,
                        "instrument": instrument,
                        "bid": bid,
                        "ask": ask,
                        "source_sequence": str(row_number - 1),
                        "external_non_executable": True,
                    }
                )
                if len(batch) >= batch_size:
                    flush()
            flush()
        finally:
            if writer is not None:
                writer.close()
        return partitions

    def _rows(
        self, source_path: Path, source_tz: tzinfo, price_scale: float
    ) -> Iterator[tuple[int, tuple[datetime, float, float]]]:
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            reader = csv.DictReader(handle, dialect=dialect)
            headers = {header.strip().lower() for header in (reader.fieldnames or [])}
            if not {"bid", "ask"}.issubset(headers):
                raise ValueError("missing required bid/ask columns")
            if not any(column in headers for column in self.TIMESTAMP_COLUMNS) and not {
                "date",
                "time",
            }.issubset(headers):
                raise ValueError("missing timestamp or date/time columns")
            for row_number, raw in enumerate(reader, start=2):
                row = {str(key).strip().lower(): str(value).strip() for key, value in raw.items()}
                timestamp = _timestamp(row, source_tz)
                yield row_number, (
                    timestamp,
                    float(row["bid"]) * price_scale,
                    float(row["ask"]) * price_scale,
                )


def _timestamp(row: dict[str, str], source_tz: tzinfo) -> datetime:
    raw = next(
        (row[name] for name in HistoricalCsvNormalizer.TIMESTAMP_COLUMNS if row.get(name)),
        None,
    )
    if row.get("date") and row.get("time"):
        raw = f"{row['date']} {row['time']}"
    if raw is None:
        raise ValueError("row has no timestamp")
    normalized = raw.replace("Z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in (
            "%Y.%m.%d %H:%M:%S.%f%z",
            "%Y.%m.%d %H:%M:%S%z",
            "%Y.%m.%d %H:%M:%S.%f",
            "%Y.%m.%d %H:%M:%S",
            "%d.%m.%Y %H:%M:%S.%f",
        ):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unsupported timestamp: {raw}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)
    return parsed.astimezone(UTC)


def _parse_timezone(value: str) -> tzinfo:
    if value.upper() in {"UTC", "Z"}:
        return UTC
    if value.startswith(("+", "-")):
        sign = 1 if value[0] == "+" else -1
        hours, minutes = (int(part) for part in value[1:].split(":"))
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {value}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
