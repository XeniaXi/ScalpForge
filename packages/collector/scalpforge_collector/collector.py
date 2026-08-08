from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .schedule import is_gold_market_open


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

    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    day = now.strftime("%Y/%m/%d")
    destination_dir = archive_root / day
    destination_dir.mkdir(parents=True, exist_ok=True)
    state_path = archive_root / "collector.state.json"
    state = _read_state(state_path)
    source_state = state.get(source.name, {})
    offset = int(source_state.get("offset", 0))
    if offset > len(content):
        offset = 0
    newline = content.find(b"\n")
    header = content[: newline + 1] if newline >= 0 else b""
    complete_end = content.rfind(b"\n") + 1
    destination: Path | None = None
    if complete_end > offset and header:
        chunk = content[:complete_end] if offset == 0 else header + content[offset:complete_end]
        chunk_hash = hashlib.sha256(chunk).hexdigest()
        destination = destination_dir / f"{source.stem}_chunk_{now:%H%M%S}_{chunk_hash[:12]}.csv"
        if not destination.exists():
            destination.write_bytes(chunk)
        chunk_rows, _, _ = _inspect(destination, now)
        manifest_data = {
            "format": "incremental_chunk_v1",
            "source": str(source),
            "snapshot": str(destination),
            "rows": chunk_rows,
            "source_offset_start": offset,
            "source_offset_end": complete_end,
            "sha256": chunk_hash,
        }
        destination.with_suffix(".manifest.json").write_text(
            json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8"
        )
        source_state = {"offset": complete_end, "last_snapshot": str(destination)}
        state[source.name] = source_state
        _write_state(state_path, state)
    rows, last, age = _inspect(source, now)
    fresh = age is not None and age <= stale_after_seconds
    if fresh and not is_gold_market_open(now):
        status = "market_closed_heartbeat_healthy"
    else:
        status = "healthy" if fresh else "stale"
    snapshot = str(destination) if destination else source_state.get("last_snapshot")
    result = CollectionResult(status, str(source), snapshot, rows, last, age, digest)
    _write_health(archive_root, result)
    return result


def _write_health(archive_root: Path, result: CollectionResult) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    (archive_root / "health.latest.json").write_text(
        json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8"
    )


def _read_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
