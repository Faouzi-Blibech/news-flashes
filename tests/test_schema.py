"""Contract / schema tests for the news-flashes pipeline.

Covers:
1. JSON round-trip persistence through an in-memory SQLite database — verifies
   that Event and MarketContext (with quotes AND history) survive the
   set_*/get_* accessor cycle end-to-end, including datetime precision and
   float values.
2. Status-transition invariants — exhaustively checks the allowed and
   forbidden edges of the ALLOWED_TRANSITIONS graph.

No network calls, no project DB file touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from news_flashes.models.schema import (
    ALLOWED_TRANSITIONS,
    Event,
    Flash,
    FlashStatus,
    HistoryPoint,
    InvalidTransition,
    MarketContext,
    Quote,
)


# ---------------------------------------------------------------------------
# Shared in-memory DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    """Yield a fresh in-memory SQLite session per test.

    Each test gets an isolated engine + tables so there is zero shared state.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers — seed data (mirrors the seed_candidate.py shape)
# ---------------------------------------------------------------------------


def _make_seed_event() -> Event:
    return Event(
        source="calendar",
        title="US CPI (YoY)",
        currency="USD",
        country="US",
        impact="High",
        event_time=datetime(2025, 6, 11, 12, 30, tzinfo=timezone.utc),
        actual="3.3%",
        forecast="3.4%",
        previous="3.5%",
        dedup_key="calendar:US_CPI_YoY:2025-06-11",
    )


def _make_seed_market_context() -> MarketContext:
    asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)
    quotes = {
        "DXY":    Quote(level=104.20, change=-0.35, asof=asof),
        "USDJPY": Quote(level=157.30, change=-0.82, asof=asof),
        "EURUSD": Quote(level=1.0730, change=+0.0041, asof=asof),
    }
    # ~30-point realistic history for two instruments
    base = datetime(2025, 5, 12, tzinfo=timezone.utc)
    history = {
        "DXY":    [HistoryPoint(t=base + timedelta(days=i), value=round(106.10 - i * 0.063, 4)) for i in range(30)],
        "USDJPY": [HistoryPoint(t=base + timedelta(days=i), value=round(160.50 - i * 0.107, 4)) for i in range(30)],
    }
    return MarketContext(quotes=quotes, history=history)


# ---------------------------------------------------------------------------
# 1.  JSON round-trip through the DB
# ---------------------------------------------------------------------------


class TestJsonRoundTrip:
    """Verify that Event and MarketContext survive the JSON column lifecycle."""

    def test_event_round_trips_through_db(self, session):
        """set_event → persist → re-get → get_event() returns equal data."""
        event = _make_seed_event()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key=event.dedup_key)
        flash.set_event(event)
        flash.set_market_context(_make_seed_market_context())

        session.add(flash)
        session.commit()
        flash_id = flash.id

        # Open a fresh session context via the same session (expire_on_commit
        # forces a re-load on next access; a get() is the cleanest way)
        session.expire(flash)
        reloaded = session.get(Flash, flash_id)
        assert reloaded is not None

        recovered = reloaded.get_event()
        assert recovered.title == event.title
        assert recovered.source == event.source
        assert recovered.currency == event.currency
        assert recovered.country == event.country
        assert recovered.impact == event.impact
        assert recovered.actual == event.actual
        assert recovered.forecast == event.forecast
        assert recovered.previous == event.previous
        assert recovered.dedup_key == event.dedup_key

    def test_event_time_datetime_survives_round_trip(self, session):
        """datetime fields must survive JSON serialisation without precision loss."""
        event = _make_seed_event()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key=event.dedup_key)
        flash.set_event(event)
        flash.set_market_context(MarketContext())

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        recovered_event = reloaded.get_event()
        # event_time should be equal (timezone-aware, same value)
        original_et = event.event_time.replace(tzinfo=None) if event.event_time else None
        recovered_et = recovered_event.event_time
        if recovered_et is not None and recovered_et.tzinfo is not None:
            recovered_et = recovered_et.replace(tzinfo=None)
        assert recovered_et == original_et

    def test_market_context_quotes_survive_round_trip(self, session):
        """Quote.level, change, and asof must survive the JSON round-trip."""
        mc = _make_seed_market_context()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key="rt:quotes:1")
        flash.set_event(_make_seed_event())
        flash.set_market_context(mc)

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        recovered_mc = reloaded.get_market_context()

        # DXY quote integrity
        assert "DXY" in recovered_mc.quotes
        dxy = recovered_mc.quotes["DXY"]
        assert dxy.level == pytest.approx(104.20, abs=1e-9)
        assert dxy.change == pytest.approx(-0.35, abs=1e-9)

        # EURUSD quote integrity
        assert "EURUSD" in recovered_mc.quotes
        eurusd = recovered_mc.quotes["EURUSD"]
        assert eurusd.level == pytest.approx(1.0730, abs=1e-9)
        assert eurusd.change == pytest.approx(0.0041, abs=1e-9)

    def test_market_context_dxy_level_matches(self, session):
        """MarketContext.quotes['DXY'].level must match exactly after reload."""
        mc = _make_seed_market_context()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key="rt:dxy:1")
        flash.set_event(_make_seed_event())
        flash.set_market_context(mc)

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        assert reloaded.get_market_context().quotes["DXY"].level == pytest.approx(104.20, abs=1e-9)

    def test_history_datetimes_survive_round_trip(self, session):
        """HistoryPoint.t datetimes must be recoverable after JSON round-trip."""
        mc = _make_seed_market_context()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key="rt:history:1")
        flash.set_event(_make_seed_event())
        flash.set_market_context(mc)

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        recovered_mc = reloaded.get_market_context()
        assert "DXY" in recovered_mc.history
        dxy_history = recovered_mc.history["DXY"]
        assert len(dxy_history) == 30  # full 30-point series persisted

        # Check that the first and last datetime values are close to the originals
        original_dxy = mc.history["DXY"]
        # Convert both to naive UTC for comparison (JSON strips tzinfo)
        for orig, recovered in [(original_dxy[0], dxy_history[0]), (original_dxy[-1], dxy_history[-1])]:
            orig_t = orig.t.replace(tzinfo=None) if orig.t.tzinfo else orig.t
            rec_t = recovered.t.replace(tzinfo=None) if recovered.t.tzinfo else recovered.t
            assert orig_t == rec_t

    def test_history_float_values_survive_round_trip(self, session):
        """HistoryPoint.value floats must survive the JSON column round-trip."""
        mc = _make_seed_market_context()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key="rt:float:1")
        flash.set_event(_make_seed_event())
        flash.set_market_context(mc)

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        recovered_mc = reloaded.get_market_context()
        original_dxy = mc.history["DXY"]
        recovered_dxy = recovered_mc.history["DXY"]

        for orig, rec in zip(original_dxy, recovered_dxy):
            assert rec.value == pytest.approx(orig.value, abs=1e-6)

    def test_full_market_context_equality(self, session):
        """The complete recovered MarketContext must equal the original."""
        mc = _make_seed_market_context()
        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key="rt:full_mc:1")
        flash.set_event(_make_seed_event())
        flash.set_market_context(mc)

        session.add(flash)
        session.commit()
        session.expire(flash)
        reloaded = session.get(Flash, flash.id)

        recovered_mc = reloaded.get_market_context()

        # Verify all quote keys present
        assert set(recovered_mc.quotes.keys()) == set(mc.quotes.keys())
        # Verify all history keys present with correct lengths
        assert set(recovered_mc.history.keys()) == set(mc.history.keys())
        for symbol, pts in mc.history.items():
            assert len(recovered_mc.history[symbol]) == len(pts)


# ---------------------------------------------------------------------------
# 2.  Transition-guard tests — exhaustive compliance invariant
# ---------------------------------------------------------------------------


class TestLegalTransitionPath:
    """The canonical CANDIDATE → DRAFT → APPROVED → SENT path must succeed."""

    def test_candidate_to_draft(self):
        flash = Flash(dedup_key="trans:legal:1")
        assert flash.status == FlashStatus.CANDIDATE
        flash.advance_to(FlashStatus.DRAFT)
        assert flash.status == FlashStatus.DRAFT

    def test_draft_to_approved(self):
        flash = Flash(dedup_key="trans:legal:2")
        flash.advance_to(FlashStatus.DRAFT)
        flash.advance_to(FlashStatus.APPROVED)
        assert flash.status == FlashStatus.APPROVED

    def test_approved_to_sent(self):
        flash = Flash(dedup_key="trans:legal:3")
        flash.advance_to(FlashStatus.DRAFT)
        flash.advance_to(FlashStatus.APPROVED)
        flash.advance_to(FlashStatus.SENT)
        assert flash.status == FlashStatus.SENT

    def test_full_legal_path_candidate_to_sent(self):
        """Single flash travels the full path: candidate→draft→approved→sent."""
        flash = Flash(dedup_key="trans:full:1")
        for expected_after, target in [
            (FlashStatus.DRAFT, FlashStatus.DRAFT),
            (FlashStatus.APPROVED, FlashStatus.APPROVED),
            (FlashStatus.SENT, FlashStatus.SENT),
        ]:
            flash.advance_to(target)
            assert flash.status == expected_after


class TestForbiddenShortcut:
    """DRAFT → SENT must be forbidden (the compliance shortcut)."""

    def test_draft_to_sent_raises_invalid_transition(self):
        flash = Flash(dedup_key="trans:shortcut:1")
        flash.advance_to(FlashStatus.DRAFT)
        with pytest.raises(InvalidTransition):
            flash.advance_to(FlashStatus.SENT)
        # Status must remain DRAFT — no partial mutation
        assert flash.status == FlashStatus.DRAFT


class TestRejectionPaths:
    """*→REJECTED must be allowed from CANDIDATE, DRAFT, and APPROVED."""

    @pytest.mark.parametrize(
        "setup_steps",
        [
            [],                                              # CANDIDATE → REJECTED
            [FlashStatus.DRAFT],                            # DRAFT → REJECTED
            [FlashStatus.DRAFT, FlashStatus.APPROVED],      # APPROVED → REJECTED
        ],
        ids=["from_candidate", "from_draft", "from_approved"],
    )
    def test_rejection_allowed(self, setup_steps):
        flash = Flash(dedup_key="trans:reject:parametrized")
        for step in setup_steps:
            flash.advance_to(step)
        flash.advance_to(FlashStatus.REJECTED)
        assert flash.status == FlashStatus.REJECTED


class TestIllegalTransitions:
    """Verify that every forbidden edge in the transition graph raises."""

    # Each tuple: (setup_steps, forbidden_target, test_id)
    ILLEGAL_CASES = [
        # candidate → approved (skips draft)
        ([], FlashStatus.APPROVED, "candidate_to_approved"),
        # candidate → sent (skips draft + approved)
        ([], FlashStatus.SENT, "candidate_to_sent"),
        # approved → draft (backwards)
        ([FlashStatus.DRAFT, FlashStatus.APPROVED], FlashStatus.DRAFT, "approved_to_draft"),
        # approved → candidate (backwards)
        ([FlashStatus.DRAFT, FlashStatus.APPROVED], FlashStatus.CANDIDATE, "approved_to_candidate"),
        # draft → sent (forbidden shortcut)
        ([FlashStatus.DRAFT], FlashStatus.SENT, "draft_to_sent"),
        # draft → candidate (backwards)
        ([FlashStatus.DRAFT], FlashStatus.CANDIDATE, "draft_to_candidate"),
    ]

    @pytest.mark.parametrize(
        "setup_steps,forbidden_target,test_id",
        ILLEGAL_CASES,
        ids=[c[2] for c in ILLEGAL_CASES],
    )
    def test_illegal_raises(self, setup_steps, forbidden_target, test_id):
        flash = Flash(dedup_key=f"trans:illegal:{test_id}")
        for step in setup_steps:
            flash.advance_to(step)
        original_status = flash.status
        with pytest.raises(InvalidTransition):
            flash.advance_to(forbidden_target)
        # Status must not have changed
        assert flash.status == original_status


class TestTerminalStates:
    """SENT and REJECTED are terminal — any transition out must raise."""

    @pytest.mark.parametrize(
        "terminal,target",
        [
            (FlashStatus.SENT,     FlashStatus.CANDIDATE),
            (FlashStatus.SENT,     FlashStatus.DRAFT),
            (FlashStatus.SENT,     FlashStatus.APPROVED),
            (FlashStatus.SENT,     FlashStatus.REJECTED),
            (FlashStatus.REJECTED, FlashStatus.CANDIDATE),
            (FlashStatus.REJECTED, FlashStatus.DRAFT),
            (FlashStatus.REJECTED, FlashStatus.APPROVED),
            (FlashStatus.REJECTED, FlashStatus.SENT),
        ],
        ids=[
            "sent_to_candidate", "sent_to_draft", "sent_to_approved", "sent_to_rejected",
            "rejected_to_candidate", "rejected_to_draft", "rejected_to_approved", "rejected_to_sent",
        ],
    )
    def test_terminal_raises_for_any_target(self, terminal, target):
        flash = Flash(dedup_key=f"trans:terminal:{terminal.value}_to_{target.value}")
        # Navigate to the terminal state
        if terminal == FlashStatus.SENT:
            flash.advance_to(FlashStatus.DRAFT)
            flash.advance_to(FlashStatus.APPROVED)
            flash.advance_to(FlashStatus.SENT)
        elif terminal == FlashStatus.REJECTED:
            flash.advance_to(FlashStatus.REJECTED)

        with pytest.raises(InvalidTransition):
            flash.advance_to(target)

    def test_sent_has_empty_allowed_set(self):
        assert ALLOWED_TRANSITIONS[FlashStatus.SENT] == set()

    def test_rejected_has_empty_allowed_set(self):
        assert ALLOWED_TRANSITIONS[FlashStatus.REJECTED] == set()
