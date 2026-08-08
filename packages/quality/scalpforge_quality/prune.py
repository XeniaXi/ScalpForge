from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class PruneReport:
    archive_root: str
    applied: bool
    legacy_snapshots_found: int
    legacy_snapshots_retained: int
    redundant_snapshots: int
    redundant_manifests: int
    recoverable_bytes: int
    invalid_entries: int
    deleted_files: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def prune_legacy_snapshots(root: Path, *, apply: bool = False) -> PruneReport:
    resolved_root = root.resolve()
    groups: dict[str, list[tuple[int, Path, Path]]] = {}
    invalid = 0
    for manifest_path in root.rglob("*.manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("format") == "incremental_chunk_v1":
                continue
            source_name = Path(str(manifest["source"])).name
            snapshot = Path(str(manifest["snapshot"]))
            if not snapshot.is_absolute():
                snapshot = manifest_path.parent / snapshot.name
            resolved_snapshot = snapshot.resolve()
            if not resolved_snapshot.is_relative_to(resolved_root):
                invalid += 1
                continue
            if not resolved_snapshot.is_file():
                invalid += 1
                continue
            digest = hashlib.sha256(resolved_snapshot.read_bytes()).hexdigest()
            if digest != manifest.get("sha256"):
                invalid += 1
                continue
            groups.setdefault(source_name, []).append(
                (int(manifest.get("rows", 0)), resolved_snapshot, manifest_path.resolve())
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            invalid += 1

    redundant: list[tuple[Path, Path]] = []
    retained = 0
    for items in groups.values():
        ordered = sorted(items, key=lambda item: (item[0], item[1].stat().st_size))
        retained += 1
        redundant.extend((snapshot, manifest) for _, snapshot, manifest in ordered[:-1])
    recoverable = sum(
        snapshot.stat().st_size + manifest.stat().st_size for snapshot, manifest in redundant
    )
    if apply and invalid:
        raise ValueError(f"refusing cleanup because {invalid} archive entries are invalid")
    deleted = 0
    if apply:
        for snapshot, manifest in redundant:
            snapshot.unlink()
            deleted += 1
            manifest.unlink()
            deleted += 1
    return PruneReport(
        archive_root=str(resolved_root),
        applied=apply,
        legacy_snapshots_found=sum(len(items) for items in groups.values()),
        legacy_snapshots_retained=retained,
        redundant_snapshots=len(redundant),
        redundant_manifests=len(redundant),
        recoverable_bytes=recoverable,
        invalid_entries=invalid,
        deleted_files=deleted,
    )
