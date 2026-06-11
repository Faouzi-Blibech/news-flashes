"""Tests for the shared data contract: Event, Quote, MarketContext, Flash, Client."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from news_flashes.models.schema import (
    Client,
    Event,
    Flash,
    FlashStatus,
    MarketContext,
    Quote,
)


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

class TestQuote:
    def test_minimal_construction(self):
        q = Quote(level=1.0850)
        assert q.level == pytest.approx(1.0850)
        assert q.change is None
        assert q.asof is None

    def test_full_construction(self):
        now = datetime.now(timezone.utc)
        q = Quote(level=105.32, change=-0.45, asof=now)
        assert q.change == pytest.approx(-0.45)
        assert q.asof == now

    def test_round_trip(self):
        q = Quote(level=3.15, change=0.01)
        restored = Quote.model_validate(q.model_dump())
        assert restored == q


# ---------------------------------------------------------------------------
# MarketContext
# ---------------------------------------------------------------------------

class TestMarketContext:
    def test_empty_quotes(self):
        mc = MarketContext()
        assert mc.quotes == {}

    def test_with_quotes(self):
        mc = MarketContext(
            quotes={
                "DXY": Quote(level=104.5, change=-0.3),
                "EURUSD": Quote(level=1.085),
            }
        )
        assert "DXY" in mc.quotes
        assert mc.quotes["EURUSD"].level == pytest.approx(1.085)

    def test_round_trip(self):
        mc = MarketContext(
            quotes={
                "USDJPY": Quote(level=157.2, change=0.5, asof=datetime.now(timezone.utc)),
            }
        )
        data = mc.model_dump()
        restored = MarketContext.model_validate(data)
        assert restored.quotes["USDJPY"].level == pytest.approx(157.2)
        assert restored == mc


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

class TestEvent:
    def _calendar_event(self) -> Event:
        return Event(
            source="calendar",
            title="US Non-Farm Payrolls",
            currency="USD",
            country="US",
            impact="High",
            event_time=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
            actual="272K",
            forecast="185K",
            previous="165K",
            dedup_key="calendar:USD:US Non-Farm Payrolls:2026-06-06",
        )

    def _news_event(self) -> Event:
        return Event(
            source="news",
            title="Fed signals rate cut pause",
            currency="USD",
            impact="High",
            headline="Federal Reserve holds rates, signals caution",
            url="https://example.com/fed-news",
            summary="The Fed kept rates steady and indicated no imminent cuts.",
            dedup_key="news:USD:Fed signals rate cut pause:2026-06-10",
        )

    def test_calendar_event_construction(self):
        ev = self._calendar_event()
        assert ev.source == "calendar"
        assert ev.impact == "High"
        assert ev.actual == "272K"
        assert ev.headline is None  # calendar extras absent

    def test_news_event_construction(self):
        ev = self._news_event()
        assert ev.source == "news"
        assert ev.headline == "Federal Reserve holds rates, signals caution"
        assert ev.actual is None  # calendar extras absent

    def test_calendar_round_trip(self):
        ev = self._calendar_event()
        data = ev.model_dump()
        restored = Event.model_validate(data)
        assert restored == ev
        assert restored.dedup_key == ev.dedup_key

    def test_news_round_trip(self):
        ev = self._news_event()
        restored = Event.model_validate(ev.model_dump())
        assert restored == ev

    def test_minimal_event(self):
        ev = Event(source="calendar", title="BCT Rate Decision", dedup_key="calendar:TND:BCT:2026-06-01")
        assert ev.currency is None
        assert ev.impact is None
        assert ev.event_time is None


# ---------------------------------------------------------------------------
# Flash
# ---------------------------------------------------------------------------

class TestFlash:
    def test_default_status_is_candidate(self):
        flash = Flash(
            event={"source": "calendar", "title": "Test", "dedup_key": "k1"},
            dedup_key="k1",
        )
        assert flash.status == FlashStatus.candidate.value
        assert flash.status == "candidate"

    def test_optional_fields_default_none(self):
        flash = Flash(
            event={"source": "news", "title": "Headline", "dedup_key": "k2"},
            dedup_key="k2",
        )
        assert flash.id is None
        assert flash.market_context is None
        assert flash.draft_text is None
        assert flash.edited_text is None
        assert flash.subject is None
        assert flash.approved_by is None
        assert flash.approved_at is None
        assert flash.sent_at is None

    def test_created_at_auto_set(self):
        before = datetime.now(timezone.utc)
        flash = Flash(
            event={"dedup_key": "k3"},
            dedup_key="k3",
        )
        after = datetime.now(timezone.utc)
        assert before <= flash.created_at <= after

    def test_flash_status_enum_values(self):
        assert FlashStatus.candidate == "candidate"
        assert FlashStatus.draft == "draft"
        assert FlashStatus.approved == "approved"
        assert FlashStatus.sent == "sent"
        assert FlashStatus.rejected == "rejected"

    def test_invalid_status_raises_value_error(self):
        """Constructing a Flash with an unrecognised status must raise ValueError."""
        with pytest.raises(ValueError):
            Flash(
                event={"source": "calendar", "title": "Test", "dedup_key": "k-inv"},
                dedup_key="k-inv",
                status="published",  # not a valid FlashStatus
            )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TestClient:
    def test_defaults(self):
        c = Client(email="test@example.com")
        assert c.lang == "fr"
        assert c.active is True
        assert c.name is None
        assert c.segment is None

    def test_full_construction(self):
        c = Client(
            email="client@bank.tn",
            name="Faouzi Blibech",
            segment="corporate",
            lang="fr",
            active=True,
        )
        assert c.email == "client@bank.tn"
        assert c.segment == "corporate"
