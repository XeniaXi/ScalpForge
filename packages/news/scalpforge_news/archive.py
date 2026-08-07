from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def archive_payload(
    root: Path, source: str, payload: object, *, received_at: datetime | None = None
) -> Path:
    received_at = received_at or datetime.now(UTC)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    folder = root / source / received_at.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{received_at:%H%M%S}_{digest[:12]}.json"
    if not path.exists():
        path.write_bytes(encoded + b"\n")
    path.with_suffix(".manifest.json").write_text(
        json.dumps(
            {"sha256": digest, "bytes": len(encoded), "received_at": received_at.isoformat()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
