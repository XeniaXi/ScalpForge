from datetime import UTC, datetime, timedelta

from scalpforge_core.models import MarketTick
from scalpforge_news.attribution import measure_reactions
from scalpforge_news.cli import _write_jsonl
from scalpforge_news.dedup import deduplicate
from scalpforge_news.gdelt import normalize
from scalpforge_news.models import NormalizedEvent
from scalpforge_news.relevance import score_gold_relevance


def test_gdelt_normalization_and_deduplication() -> None:
    payload = {
        "articles": [
            {
                "url": "https://example.test/story",
                "title": "Gold rises as Treasury yields fall",
                "seendate": "20260807T143000Z",
                "domain": "example.test",
                "language": "English",
                "sourcecountry": "United States",
            }
        ]
    }
    events = normalize(payload, received_at=datetime(2026, 8, 7, 14, 31, tzinfo=UTC))
    assert len(events) == 1
    assert events[0].relevance_score > 0.5
    assert deduplicate(events + events) == events


def test_relevance_does_not_invent_sentiment() -> None:
    score, reasons = score_gold_relevance("Federal Reserve discusses inflation outlook")
    assert score > 0
    assert "federal_reserve" in reasons
    assert "inflation" in reasons


def test_reaction_windows_use_observed_bid_ask_midpoints() -> None:
    event_time = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
    event = NormalizedEvent(
        event_key="calendar:cpi:20260807T143000Z",
        source="licensed-calendar",
        event_type="scheduled_release",
        title="US inflation data released",
        occurred_at=event_time,
        published_at=event_time,
        timing_quality="exact_release_time",
        reaction_eligible=True,
    )
    ticks = [
        MarketTick(
            occurred_at=event_time - timedelta(seconds=1), bid=2000, ask=2000.2, source="demo"
        ),
        MarketTick(
            occurred_at=event_time + timedelta(seconds=5), bid=2002, ask=2002.2, source="demo"
        ),
        MarketTick(
            occurred_at=event_time + timedelta(seconds=30), bid=1999, ask=1999.2, source="demo"
        ),
    ]
    reactions = measure_reactions(event, ticks, windows=(5, 30))
    assert [reaction.window_seconds for reaction in reactions] == [5, 30]
    assert reactions[0].return_bps > 0
    assert reactions[1].return_bps < 0


def test_approximate_news_time_is_not_used_for_intraday_reaction() -> None:
    event_time = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
    event = normalize(
        {"articles": [{"title": "Gold update", "seendate": "20260807T143000Z"}]},
        received_at=event_time,
    )[0]
    tick = MarketTick(occurred_at=event_time, bid=2000, ask=2001, source="demo")
    assert measure_reactions(event, [tick]) == []


def test_jsonl_writer_preserves_prior_unique_events(tmp_path) -> None:
    event_time = datetime(2026, 8, 7, 14, 30, tzinfo=UTC)
    first = NormalizedEvent(
        event_key="one",
        source="test",
        event_type="news",
        title="First",
        occurred_at=event_time,
        published_at=event_time,
    )
    second = first.model_copy(update={"event_key": "two", "title": "Second"})
    path = tmp_path / "events.jsonl"
    _write_jsonl(path, [first])
    _write_jsonl(path, [first, second])
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
