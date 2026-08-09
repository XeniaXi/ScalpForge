from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]


@dataclass(frozen=True)
class CausalExecutionConfig:
    decision_latency_ms: int = 50
    maximum_quote_delay_seconds: int = 2
    maximum_continuity_gap_seconds: int = 5

    def __post_init__(self) -> None:
        if self.decision_latency_ms < 0 or self.maximum_quote_delay_seconds < 0:
            raise ValueError("latency and quote delay cannot be negative")
        if self.maximum_continuity_gap_seconds <= 0:
            raise ValueError("continuity gap must be positive")


@dataclass(frozen=True)
class CausalQuoteSeries:
    occurred_at: list[datetime]
    feature_available_at: list[datetime]
    open_bid: list[float]
    open_ask: list[float]
    segments: list[int]

    @classmethod
    def from_feature_table(
        cls, table: pa.Table, maximum_continuity_gap_seconds: int = 5
    ) -> CausalQuoteSeries:
        required = {
            "occurred_at",
            "feature_available_at",
            "bar_open_bid",
            "bar_open_ask",
        }
        missing = required.difference(table.column_names)
        if missing:
            raise ValueError(f"feature table lacks causal execution columns: {sorted(missing)}")
        occurred_at = _utc_timestamps(table["occurred_at"])
        available_at = _utc_timestamps(table["feature_available_at"])
        open_bid = [float(value) for value in table["bar_open_bid"].to_pylist()]
        open_ask = [float(value) for value in table["bar_open_ask"].to_pylist()]
        if any(right <= left for left, right in zip(occurred_at, occurred_at[1:], strict=False)):
            raise ValueError("feature timestamps must be strictly increasing")
        availability_pairs = zip(occurred_at, available_at, strict=True)
        if any(available <= occurred for occurred, available in availability_pairs):
            raise ValueError("features must become available after their observation timestamp")
        segments = [0] * len(occurred_at)
        for index in range(1, len(occurred_at)):
            gap = (occurred_at[index] - occurred_at[index - 1]).total_seconds()
            segments[index] = segments[index - 1] + int(
                gap > maximum_continuity_gap_seconds
            )
        return cls(occurred_at, available_at, open_bid, open_ask, segments)

    def entry_indices(self, config: CausalExecutionConfig) -> list[int | None]:
        entries: list[int | None] = [None] * len(self.occurred_at)
        latency = timedelta(milliseconds=config.decision_latency_ms)
        for signal_index, available_at in enumerate(self.feature_available_at):
            eligible_at = available_at + latency
            quote_index = bisect.bisect_left(
                self.occurred_at, eligible_at, lo=signal_index + 1
            )
            if quote_index >= len(self.occurred_at):
                continue
            delay = (self.occurred_at[quote_index] - eligible_at).total_seconds()
            if delay > config.maximum_quote_delay_seconds:
                continue
            if self.segments[quote_index] != self.segments[signal_index]:
                continue
            entries[signal_index] = quote_index
        return entries

    def exit_indices(
        self,
        entries: list[int | None],
        horizon_seconds: int,
        config: CausalExecutionConfig,
    ) -> list[int | None]:
        if horizon_seconds <= 0:
            raise ValueError("exit horizon must be positive")
        exits: list[int | None] = [None] * len(entries)
        for signal_index, entry_index in enumerate(entries):
            if entry_index is None:
                continue
            target = self.occurred_at[entry_index] + timedelta(seconds=horizon_seconds)
            exit_index = bisect.bisect_left(
                self.occurred_at, target, lo=entry_index + 1
            )
            if exit_index >= len(self.occurred_at):
                continue
            delay = (self.occurred_at[exit_index] - target).total_seconds()
            if delay > config.maximum_quote_delay_seconds:
                continue
            if self.segments[exit_index] != self.segments[entry_index]:
                continue
            exits[signal_index] = exit_index
        return exits


def _utc_timestamps(column: pa.ChunkedArray) -> list[datetime]:
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[
        column.type.unit
    ]
    return [
        datetime.fromtimestamp(value / divisor, UTC)
        for value in column.cast(pa.int64()).to_pylist()
    ]
