from __future__ import annotations

import hashlib
import json
from pathlib import Path


def valid_snapshot_groups(root: Path) -> tuple[dict[str, list[Path]], int]:
    candidates: dict[str, list[tuple[dict[str, object], Path]]] = {}
    invalid = 0
    for manifest_path in root.rglob("*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            source_name = Path(str(manifest["source"])).name
            candidates.setdefault(source_name, []).append((manifest, manifest_path))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            invalid += 1
    groups: dict[str, list[Path]] = {}
    for source_name, items in candidates.items():
        chunks = [item for item in items if item[0].get("format") == "incremental_chunk_v1"]
        selected = sorted(chunks, key=lambda item: int(item[0].get("source_offset_start", 0)))
        if not selected:
            selected = [max(items, key=lambda item: int(item[0].get("rows", 0)))]
        valid: list[Path] = []
        for manifest, manifest_path in selected:
            snapshot = Path(str(manifest["snapshot"]))
            if not snapshot.is_absolute():
                snapshot = manifest_path.parent / snapshot.name
            if not snapshot.is_file():
                invalid += 1
                continue
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != manifest.get("sha256"):
                invalid += 1
                continue
            valid.append(snapshot)
        if valid:
            groups[source_name] = valid
    return groups, invalid


def latest_valid_snapshots(root: Path) -> tuple[list[Path], int]:
    groups, invalid = valid_snapshot_groups(root)
    return [path for paths in groups.values() for path in paths], invalid
