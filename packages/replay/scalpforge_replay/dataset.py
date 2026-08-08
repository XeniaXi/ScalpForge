from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_core.models import MarketTick

from scalpforge_replay.engine import ReplayEvent


class ParquetTickReplaySource:
    """Read a content-addressed historical dataset in deterministic event-time order."""

    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path.resolve()
        self.dataset_root = self.manifest_path.parent
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema_version") != 1:
            raise ValueError("unsupported historical dataset schema")

    def events(self) -> Iterator[ReplayEvent[MarketTick]]:
        sequence = 0
        previous: datetime | None = None
        for stored_path in self.manifest.get("partitions", []):
            path = Path(stored_path)
            if not path.is_absolute():
                path = self.dataset_root / path
            path = path.resolve()
            if not path.is_relative_to(self.dataset_root):
                raise ValueError("dataset partition escapes manifest directory")
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=100_000):
                for row in batch.to_pylist():
                    occurred_at = row["occurred_at"]
                    if occurred_at.tzinfo is None:
                        occurred_at = occurred_at.replace(tzinfo=UTC)
                    received_at = row["received_at"]
                    if received_at.tzinfo is None:
                        received_at = received_at.replace(tzinfo=UTC)
                    if previous is not None and occurred_at < previous:
                        raise ValueError("dataset partitions are not ordered")
                    tick = MarketTick(
                        instrument=row["instrument"],
                        occurred_at=occurred_at,
                        received_at=received_at,
                        bid=row["bid"],
                        ask=row["ask"],
                        source=f"{row['provider']}:{row['venue']}",
                        source_sequence=row["source_sequence"],
                    )
                    sequence += 1
                    previous = occurred_at
                    yield ReplayEvent(
                        occurred_at=occurred_at,
                        sequence=sequence,
                        payload=tick,
                    )
