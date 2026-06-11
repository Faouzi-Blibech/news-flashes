"""Tests for scheduler/worker.py — run_ingestion_cycle().

Strategy
--------
- All ingestion functions are monkeypatched on the worker module namespace
  (worker.fetch_calendar_events, worker.fetch_news_events,
   worker.fetch_market_context) so no real network calls are made.
- DB is an in-memory SQLite engine isolated per test.
- filter_events is NOT mocked — we rely on its real logic and supply events
  that will pass the High-impact + basket-currency gates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import create_engine, select

from news_flashes.models.schema import Event, Flash, FlashStatus, MarketContext, Quote
from news_flashes.models.db import init_db, get_session
import news_flashes.scheduler.worker as worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_calendar_event(dedup_key: str = "calendar:test:nfp:1") -> Event:
    """Return a High-impact, USD calendar event that will survive filter_events."""
    return Event(
        source="calendar",
        title="US Non-Farm Payrolls",
        currency="USD",
        impact="High",
        event_time=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
        actual="272K",
        forecast="185K",
        previous="165K",
        dedup_key=dedup_key,
    )


def _make_news_event(dedup_key: str = "news:test:dollar:1") -> Event:
    """Return a news event with USD currency (news events pass is_high_impact unconditionally)."""
    return Event(
        source="news",
        title="Dollar surges on strong jobs data",
        currency="USD",
        impact=None,  # news events have no impact field
        event_time=datetime(2026, 6, 6, 14, 0, tzinfo=timezone.utc),
        headline="Dollar surges on strong jobs data",
        url="https://example.com/news/1",
        summary="The US dollar rose sharply after Non-Farm Payrolls beat expectations.",
        dedup_key=dedup_key,
    )


def _make_market_context() -> MarketContext:
    return MarketContext(
        quotes={
            "DXY": Quote(level=104.5, change=-0.3),
            "USDJPY": Quote(level=157.2, change=0.5),
            "EURUSD": Quote(level=1.085, change=0.002),
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_engine():
    """Isolated in-memory SQLite engine with schema created."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine=eng)
    return eng


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunIngestionCycle:
    def test_writes_candidate_rows_with_event_and_market_context(
        self, tmp_engine, monkeypatch
    ):
        """A cycle with high-impact basket events writes candidate Flash rows
        containing serialised event and market_context JSON."""
        cal_event = _make_calendar_event("calendar:test:nfp:001")
        news_event = _make_news_event("news:test:dollar:001")
        ctx = _make_market_context()

        monkeypatch.setattr(worker, "fetch_calendar_events", lambda: [cal_event])
        monkeypatch.setattr(worker, "fetch_news_events", lambda: [news_event])
        monkeypatch.setattr(worker, "fetch_market_context", lambda: ctx)

        with get_session(engine=tmp_engine) as session:
            count = worker.run_ingestion_cycle(session)

        assert count == 2

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()

        assert len(rows) == 2
        for row in rows:
            assert row.status == FlashStatus.candidate.value
            assert isinstance(row.event, dict)
            assert "title" in row.event
            assert isinstance(row.market_context, dict)
            assert "quotes" in row.market_context

    def test_dedup_prevents_duplicate_candidates_on_second_cycle(
        self, tmp_engine, monkeypatch
    ):
        """Running the cycle twice with the same events writes 0 new rows on
        the second run — dedup_key already present in the DB."""
        cal_event = _make_calendar_event("calendar:dedup:test:001")

        monkeypatch.setattr(worker, "fetch_calendar_events", lambda: [cal_event])
        monkeypatch.setattr(worker, "fetch_news_events", lambda: [])
        monkeypatch.setattr(worker, "fetch_market_context", lambda: _make_market_context())

        with get_session(engine=tmp_engine) as session:
            first = worker.run_ingestion_cycle(session)

        with get_session(engine=tmp_engine) as session:
            second = worker.run_ingestion_cycle(session)

        assert first == 1
        assert second == 0

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()
        assert len(rows) == 1

    def test_calendar_feed_failure_still_processes_news_events(
        self, tmp_engine, monkeypatch
    ):
        """If fetch_calendar_events raises, the cycle continues and still
        persists news events as candidates."""
        news_event = _make_news_event("news:resilience:001")

        def _bad_calendar():
            raise RuntimeError("calendar feed down")

        monkeypatch.setattr(worker, "fetch_calendar_events", _bad_calendar)
        monkeypatch.setattr(worker, "fetch_news_events", lambda: [news_event])
        monkeypatch.setattr(worker, "fetch_market_context", lambda: _make_market_context())

        with get_session(engine=tmp_engine) as session:
            count = worker.run_ingestion_cycle(session)

        assert count == 1

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()
        assert len(rows) == 1
        assert rows[0].event["source"] == "news"

    def test_market_context_failure_still_writes_candidates_with_none_context(
        self, tmp_engine, monkeypatch
    ):
        """If fetch_market_context raises, candidates are still written but
        market_context is None."""
        cal_event = _make_calendar_event("calendar:no-ctx:001")

        def _bad_market():
            raise RuntimeError("market data API down")

        monkeypatch.setattr(worker, "fetch_calendar_events", lambda: [cal_event])
        monkeypatch.setattr(worker, "fetch_news_events", lambda: [])
        monkeypatch.setattr(worker, "fetch_market_context", _bad_market)

        with get_session(engine=tmp_engine) as session:
            count = worker.run_ingestion_cycle(session)

        assert count == 1

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()
        assert len(rows) == 1
        assert rows[0].market_context is None

    def test_low_impact_events_are_filtered_out(self, tmp_engine, monkeypatch):
        """Events that don't survive filter_events (e.g. Low impact) must not
        produce candidate rows."""
        low_impact_event = Event(
            source="calendar",
            title="Some Low Impact Event",
            currency="USD",
            impact="Low",
            event_time=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
            dedup_key="calendar:low:001",
        )

        monkeypatch.setattr(worker, "fetch_calendar_events", lambda: [low_impact_event])
        monkeypatch.setattr(worker, "fetch_news_events", lambda: [])
        monkeypatch.setattr(worker, "fetch_market_context", lambda: _make_market_context())

        with get_session(engine=tmp_engine) as session:
            count = worker.run_ingestion_cycle(session)

        assert count == 0

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()
        assert len(rows) == 0

    def test_news_feed_failure_still_processes_calendar_events(
        self, tmp_engine, monkeypatch
    ):
        """If fetch_news_events raises, the cycle continues and still
        persists calendar events as candidates."""
        cal_event = _make_calendar_event("calendar:news-fail:001")

        def _bad_news():
            raise RuntimeError("news API down")

        monkeypatch.setattr(worker, "fetch_calendar_events", lambda: [cal_event])
        monkeypatch.setattr(worker, "fetch_news_events", _bad_news)
        monkeypatch.setattr(worker, "fetch_market_context", lambda: _make_market_context())

        with get_session(engine=tmp_engine) as session:
            count = worker.run_ingestion_cycle(session)

        assert count == 1

        with get_session(engine=tmp_engine) as session:
            rows = session.exec(select(Flash)).all()
        assert len(rows) == 1
        assert rows[0].event["source"] == "calendar"
