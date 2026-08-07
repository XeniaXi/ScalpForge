from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class CollectionResult:
    status: str
    source: str
    snapshot: str | None
    rows: int
    last_received_utc: str | None
    age_seconds: float | None
    sha256: str | None


def _inspect(path: Path, now: datetime) -> tuple[int, str | None, float | None]:
    rows = 0
    last = None
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("record_type") == "tick":
                rows += 1
            if row.get("received_utc"):
                last = row["received_utc"]
    if not last:
        return rows, None, None
    parsed = datetime.strptime(last, "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
    return rows, last, max(0.0, (now - parsed).total_seconds())


def collect_once(
    source: Path,
    archive_root: Path,
    *,
    stale_after_seconds: int = 120,
    now: datetime | None = None,
) -> CollectionResult:
    now = now or datetime.now(UTC)
    if not source.is_file():
        result = CollectionResult("missing", str(source), None, 0, None, None, None)
        _write_health(archive_root, result)
        return result

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    day = now.strftime("%Y/%m/%d")
    destination_dir = archive_root / day
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.stem}_{now:%H%M%S}_{digest[:12]}.csv"
    if not destination.exists():
        shutil.copy2(source, destination)
    rows, last, age = _inspect(destination, now)
    status = "healthy" if age is not None and age <= stale_after_seconds else "stale"
    result = CollectionResult(status, str(source), str(destination), rows, last, age, digest)
    manifest = destination.with_suffix(".manifest.json")
    manifest.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    _write_health(archive_root, result)
    return result


def _write_health(archive_root: Path, result: CollectionResult) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "health.latest.json").write_text(
        json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8"
    )
