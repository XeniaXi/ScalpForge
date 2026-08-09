from datetime import UTC, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
from scalpforge_strategy.execution_clock import CausalExecutionConfig, CausalQuoteSeries


def _table(seconds: list[int]) -> pa.Table:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    occurred = [start + timedelta(seconds=value) for value in seconds]
    return pa.Table.from_pydict(
        {
            "occurred_at": occurred,
            "feature_available_at": [value + timedelta(seconds=1) for value in occurred],
            "bar_open_bid": [100.0 + index for index in range(len(seconds))],
            "bar_open_ask": [100.5 + index for index in range(len(seconds))],
        }
    )


def test_signal_cannot_fill_inside_its_observation_second() -> None:
    quotes = CausalQuoteSeries.from_feature_table(_table([0, 1, 2]))
    entries = quotes.entry_indices(CausalExecutionConfig(decision_latency_ms=0))
    assert entries[0] == 1
    assert quotes.open_ask[entries[0]] == 101.5


def test_latency_uses_first_quote_after_eligibility() -> None:
    quotes = CausalQuoteSeries.from_feature_table(_table([0, 1, 2, 3]))
    entries = quotes.entry_indices(
        CausalExecutionConfig(decision_latency_ms=500, maximum_quote_delay_seconds=1)
    )
    assert entries[0] == 2


def test_entry_and_exit_reject_market_gap() -> None:
    quotes = CausalQuoteSeries.from_feature_table(_table([0, 1, 10, 11]))
    config = CausalExecutionConfig(decision_latency_ms=0, maximum_quote_delay_seconds=10)
    entries = quotes.entry_indices(config)
    assert entries[1] is None
    exits = quotes.exit_indices(entries, 9, config)
    assert exits[0] is None


def test_future_rows_cannot_change_existing_entry_decision() -> None:
    config = CausalExecutionConfig(decision_latency_ms=0)
    before = CausalQuoteSeries.from_feature_table(_table([0, 1, 2])).entry_indices(config)
    after = CausalQuoteSeries.from_feature_table(_table([0, 1, 2, 3])).entry_indices(config)
    assert before[:2] == after[:2]
