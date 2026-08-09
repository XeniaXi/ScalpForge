from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from scalpforge_core.models import MarketTick


@dataclass(frozen=True)
class FeatureConfig:
    schema_revision: int = 2
    bar_seconds: int = 1
    maximum_carry_seconds: int = 5
    return_horizons_seconds: tuple[int, ...] = (1, 5, 30, 60)
    volatility_window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.schema_revision != 2:
            raise ValueError("only causal feature schema revision 2 is supported")
        if self.bar_seconds != 1:
            raise ValueError("only one-second research bars are currently supported")
        if self.maximum_carry_seconds < 0:
            raise ValueError("maximum carry must be non-negative")
        if not self.return_horizons_seconds or min(self.return_horizons_seconds) <= 0:
            raise ValueError("return horizons must be positive")
        if self.volatility_window_seconds <= 1:
            raise ValueError("volatility window must exceed one second")


@dataclass(frozen=True)
class WalkForwardConfig:
    train_days: int
    validation_days: int
    test_days: int
    step_days: int
    purge_seconds: int
    embargo_seconds: int

    def __post_init__(self) -> None:
        positive = (self.train_days, self.validation_days, self.test_days, self.step_days)
        if any(value <= 0 for value in positive):
            raise ValueError("walk-forward day lengths must be positive")
        if self.purge_seconds < 0 or self.embargo_seconds < 0:
            raise ValueError("purge and embargo must be non-negative")


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    train_start: datetime
    train_end_exclusive: datetime
    validation_start: datetime
    validation_end_exclusive: datetime
    test_start: datetime
    test_end_exclusive: datetime
    purge_seconds: int
    embargo_seconds: int


@dataclass(frozen=True)
class FeatureDatasetManifest:
    dataset_id: str
    schema_version: int
    created_at: str
    source_dataset_id: str
    source_manifest: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    feature_config: dict[str, object]
    feature_columns: list[str]
    partitions: list[str]
    point_in_time: bool = True
    labels_included: bool = False
    external_non_executable: bool = True


@dataclass
class _SecondBar:
    occurred_at: datetime
    bid: float
    ask: float
    first_bid: float
    first_ask: float
    first_tick_at: datetime
    first_mid: float
    mid: float
    tick_count: int
    quote_change_count: int
    last_tick_at: datetime


class PointInTimeFeatureBuilder:
    """Builds causal features; every row depends only on ticks at or before its timestamp."""

    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.config = config or FeatureConfig()

    def rows(self, ticks: Iterable[MarketTick]) -> Iterator[dict[str, object]]:
        history: deque[_SecondBar] = deque()
        previous: _SecondBar | None = None
        for bar in _second_bars(ticks):
            maximum_history = max(
                max(self.config.return_horizons_seconds),
                self.config.volatility_window_seconds,
            )
            cutoff = bar.occurred_at - timedelta(seconds=maximum_history + 1)
            while history and history[0].occurred_at < cutoff:
                history.popleft()

            row = self._features(bar, history, previous)
            yield row
            history.append(bar)
            previous = bar

    def _features(
        self,
        bar: _SecondBar,
        history: deque[_SecondBar],
        previous: _SecondBar | None,
    ) -> dict[str, object]:
        mid = bar.mid
        spread_bps = (bar.ask - bar.bid) / mid * 10_000
        prior_spreads = [
            (item.ask - item.bid) / item.mid * 10_000
            for item in history
            if item.occurred_at >= bar.occurred_at - timedelta(seconds=60)
        ]
        median_spread = _median(prior_spreads) if prior_spreads else spread_bps
        returns: dict[str, float | None] = {}
        for horizon in self.config.return_horizons_seconds:
            prior = _at_or_before(history, bar.occurred_at - timedelta(seconds=horizon))
            returns[f"return_{horizon}s"] = (mid / prior.mid - 1) if prior else None

        recent = [
            item
            for item in history
            if item.occurred_at
            >= bar.occurred_at - timedelta(seconds=self.config.volatility_window_seconds)
        ]
        mids = [item.mid for item in recent] + [mid]
        one_step_returns = [
            (right / left) - 1 for left, right in zip(mids, mids[1:], strict=False)
        ]
        realized_volatility = (
            (sum(value * value for value in one_step_returns) / len(one_step_returns)) ** 0.5
            if one_step_returns
            else 0.0
        )
        previous_age = (
            (bar.occurred_at - previous.occurred_at).total_seconds() if previous else None
        )
        gap_start = previous_age is not None and previous_age > self.config.maximum_carry_seconds
        prior_tick_rate = sum(item.tick_count for item in recent) / max(len(recent), 1)
        return {
            "occurred_at": bar.occurred_at,
            "feature_available_at": bar.occurred_at + timedelta(seconds=1),
            "bar_open_at": bar.first_tick_at,
            "bar_open_bid": bar.first_bid,
            "bar_open_ask": bar.first_ask,
            "bid": bar.bid,
            "ask": bar.ask,
            "mid": mid,
            "spread_bps": spread_bps,
            "spread_shock_ratio": spread_bps / median_spread if median_spread else 1.0,
            "tick_count": bar.tick_count,
            "quote_change_count": bar.quote_change_count,
            "tick_intensity_ratio": bar.tick_count / prior_tick_rate if prior_tick_rate else 1.0,
            "intrasecond_return": mid / bar.first_mid - 1,
            "realized_volatility_60s": realized_volatility,
            "seconds_since_previous_active_bar": previous_age,
            "is_gap_start": gap_start,
            "session": _session(bar.occurred_at),
            **returns,
        }


def anchored_walk_forward_folds(
    start: datetime,
    end_exclusive: datetime,
    config: WalkForwardConfig,
) -> list[WalkForwardFold]:
    start = _aware_utc(start)
    end_exclusive = _aware_utc(end_exclusive)
    folds: list[WalkForwardFold] = []
    cursor = start
    fold_number = 1
    while True:
        raw_train_end = cursor + timedelta(days=config.train_days)
        validation_start = raw_train_end + timedelta(seconds=config.purge_seconds)
        validation_end = validation_start + timedelta(days=config.validation_days)
        test_start = validation_end + timedelta(seconds=config.embargo_seconds)
        test_end = test_start + timedelta(days=config.test_days)
        if test_end > end_exclusive:
            break
        folds.append(
            WalkForwardFold(
                fold=fold_number,
                train_start=start,
                train_end_exclusive=raw_train_end,
                validation_start=validation_start,
                validation_end_exclusive=validation_end,
                test_start=test_start,
                test_end_exclusive=test_end,
                purge_seconds=config.purge_seconds,
                embargo_seconds=config.embargo_seconds,
            )
        )
        fold_number += 1
        cursor += timedelta(days=config.step_days)
    return folds


def write_feature_dataset(
    ticks: Iterable[MarketTick],
    source_manifest: Path,
    output_root: Path,
    config: FeatureConfig | None = None,
) -> FeatureDatasetManifest:
    feature_config = config or FeatureConfig()
    serialized_config = json.loads(json.dumps(asdict(feature_config)))
    source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    source_dataset_id = str(source_payload["dataset_id"])
    identity = json.dumps(
        {"source_dataset_id": source_dataset_id, "config": serialized_config},
        sort_keys=True,
    ).encode()
    dataset_id = "xauusd-features-" + hashlib.sha256(identity).hexdigest()[:16]
    dataset_root = output_root / dataset_id
    manifest_path = dataset_root / "manifest.json"
    if manifest_path.exists():
        return FeatureDatasetManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    staging = output_root / f"{dataset_id}.partial"
    staging.mkdir(parents=True, exist_ok=False)
    partition = staging / "features.parquet"
    writer: pq.ParquetWriter | None = None
    buffer: list[dict[str, object]] = []
    row_count = 0
    first: datetime | None = None
    last: datetime | None = None
    columns: list[str] = []
    try:
        for row in PointInTimeFeatureBuilder(feature_config).rows(ticks):
            first = first or row["occurred_at"]  # type: ignore[assignment]
            last = row["occurred_at"]  # type: ignore[assignment]
            buffer.append(row)
            row_count += 1
            if len(buffer) >= 100_000:
                writer = _write_buffer(buffer, partition, writer)
                buffer = []
        if buffer:
            writer = _write_buffer(buffer, partition, writer)
        if writer is None:
            raise ValueError("source dataset contains no active ticks")
        columns = writer.schema.names
        writer.close()
        writer = None
        final_partition = dataset_root / partition.name
        manifest = FeatureDatasetManifest(
            dataset_id=dataset_id,
            schema_version=1,
            created_at=datetime.now(UTC).isoformat(),
            source_dataset_id=source_dataset_id,
            source_manifest=str(source_manifest.resolve()),
            row_count=row_count,
            first_timestamp=first.isoformat() if first else None,
            last_timestamp=last.isoformat() if last else None,
            feature_config=serialized_config,
            feature_columns=columns,
            partitions=[str(final_partition)],
        )
        (staging / "manifest.json").write_text(
            json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
        )
        staging.replace(dataset_root)
        return manifest
    finally:
        if writer is not None:
            writer.close()


def _second_bars(ticks: Iterable[MarketTick]) -> Iterator[_SecondBar]:
    active: _SecondBar | None = None
    previous_tick: MarketTick | None = None
    for tick in ticks:
        timestamp = _aware_utc(tick.occurred_at)
        second = timestamp.replace(microsecond=0)
        if previous_tick and timestamp < previous_tick.occurred_at:
            raise ValueError("ticks must be ordered by event time")
        mid = tick.mid
        if active is None or second != active.occurred_at:
            if active is not None:
                yield active
            active = _SecondBar(
                second,
                tick.bid,
                tick.ask,
                tick.bid,
                tick.ask,
                timestamp,
                mid,
                mid,
                1,
                0,
                timestamp,
            )
        else:
            changed = tick.bid != active.bid or tick.ask != active.ask
            active.bid = tick.bid
            active.ask = tick.ask
            active.mid = mid
            active.tick_count += 1
            active.quote_change_count += int(changed)
            active.last_tick_at = timestamp
        previous_tick = tick
    if active is not None:
        yield active


def _at_or_before(history: deque[_SecondBar], target: datetime) -> _SecondBar | None:
    for item in reversed(history):
        if item.occurred_at <= target:
            return item
    return None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _session(timestamp: datetime) -> str:
    hour = timestamp.hour
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 16:
        return "london_new_york_overlap"
    if 16 <= hour < 21:
        return "new_york"
    return "maintenance_or_late"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _write_buffer(
    buffer: list[dict[str, object]],
    path: Path,
    writer: pq.ParquetWriter | None,
) -> pq.ParquetWriter:
    table = pa.Table.from_pylist(buffer)
    writer = writer or pq.ParquetWriter(path, table.schema, compression="zstd")
    writer.write_table(table)
    return writer
