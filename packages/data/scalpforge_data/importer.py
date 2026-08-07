import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from scalpforge_core.models import MarketTick

from scalpforge_data.quality import QualityIssue, Severity, TickQualityValidator


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    source_file: str
    sha256: str
    created_at: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    issue_counts: dict[str, int]


@dataclass(frozen=True)
class ImportResult:
    ticks: list[MarketTick]
    issues: list[QualityIssue]
    manifest: DatasetManifest

    @property
    def is_usable(self) -> bool:
        return not any(issue.severity is Severity.ERROR for issue in self.issues)


class TickCsvImporter:
    TIMESTAMP_COLUMNS = ("occurred_at", "timestamp", "datetime", "time")

    def __init__(self, validator: TickQualityValidator | None = None) -> None:
        self.validator = validator or TickQualityValidator()

    def load(self, path: Path, *, source: str, instrument: str = "XAUUSD") -> ImportResult:
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        ticks: list[MarketTick] = []
        issues: list[QualityIssue] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            reader = csv.DictReader(handle, dialect=dialect)
            headers = {header.strip().lower() for header in (reader.fieldnames or [])}
            if not {"bid", "ask"}.issubset(headers):
                raise ValueError("missing required bid/ask columns")
            has_timestamp = any(column in headers for column in self.TIMESTAMP_COLUMNS)
            if not has_timestamp and not {"date", "time"}.issubset(headers):
                raise ValueError("missing timestamp or date/time columns")
            for row_number, raw_row in enumerate(reader, start=2):
                row = {key.strip().lower(): value.strip() for key, value in raw_row.items()}
                try:
                    occurred_at = self._parse_timestamp(row)
                    if occurred_at.tzinfo is None:
                        raise ValueError("occurred_at must include a timezone")
                    ticks.append(
                        MarketTick(
                            instrument=row.get("instrument") or instrument,
                            occurred_at=occurred_at.astimezone(UTC),
                            received_at=occurred_at.astimezone(UTC),
                            bid=float(row["bid"]),
                            ask=float(row["ask"]),
                            source=row.get("source") or source,
                            source_sequence=row.get("source_sequence") or "",
                        )
                    )
                except (ValueError, ValidationError) as exc:
                    issues.append(QualityIssue("invalid_row", Severity.ERROR, str(exc), row_number))
        issues.extend(self.validator.validate(ticks))
        counts = {
            severity.value: sum(i.severity is severity for i in issues)
            for severity in Severity
        }
        manifest = DatasetManifest(
            dataset_id=f"ticks-{content_hash[:16]}",
            source_file=path.name,
            sha256=content_hash,
            created_at=datetime.now(UTC).isoformat(),
            row_count=len(ticks),
            first_timestamp=ticks[0].occurred_at.isoformat() if ticks else None,
            last_timestamp=ticks[-1].occurred_at.isoformat() if ticks else None,
            issue_counts=counts,
        )
        return ImportResult(ticks, issues, manifest)

    @classmethod
    def _parse_timestamp(cls, row: dict[str, str]) -> datetime:
        raw = next((row[name] for name in cls.TIMESTAMP_COLUMNS if row.get(name)), None)
        if row.get("date") and row.get("time"):
            raw = f"{row['date']} {row['time']}"
        if raw is None:
            raise ValueError("row has no timestamp")
        normalized = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            for pattern in ("%Y.%m.%d %H:%M:%S.%f%z", "%Y.%m.%d %H:%M:%S%z"):
                try:
                    return datetime.strptime(normalized, pattern)
                except ValueError:
                    continue
        raise ValueError(f"unsupported timestamp: {raw}")

    @staticmethod
    def write_manifest(result: ImportResult, path: Path) -> None:
        path.write_text(json.dumps(asdict(result.manifest), indent=2) + "\n", encoding="utf-8")
