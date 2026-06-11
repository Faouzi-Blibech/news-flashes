"""Tests for the generation stage (Task 2).

Uses a fake Anthropic client — no network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from news_flashes.models.schema import (
    Event,
    Flash,
    FlashStatus,
    HistoryPoint,
    MarketContext,
    Quote,
    InvalidTransition,
)
from news_flashes.generation.generator import generate_draft
from news_flashes.generation.prompt import build_user_message, load_example

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_BODY = (
    "Bonsoir chers clients,\n\n"
    "L'IPC américain est ressorti à 3,3 %, sous les attentes.\n\n"
    "**1. Niveaux techniques à surveiller**\n"
    "DXY : 104,20. Support : 103,80.\n\n"
    "**2. Contexte de marché**\n"
    "La Fed pourrait assouplir sa politique monétaire.\n\n"
    "**3. Impact sur les cotations TND**\n"
    "Pression baissière sur USD/TND.\n\n"
    "**Synthèse**\n"
    "Le dollar reste sous pression à court terme.\n\n"
    "Voir graphique ci-dessous.\n\n"
    "Bien cordialement,\nLa Desk FX"
)


def _make_fake_client(text: str = FAKE_BODY):
    """Return a minimal stub mimicking anthropic.Anthropic.messages.create."""

    # Match the real SDK shape: Message.content is a list of objects with .text
    content_block = SimpleNamespace(text=text, type="text")
    fake_message = SimpleNamespace(content=[content_block])

    class FakeMessages:
        def create(self, **kwargs):  # noqa: ANN001
            return fake_message

    class FakeClient:
        messages = FakeMessages()

    return FakeClient()


def _make_flash() -> Flash:
    """Return an in-memory CANDIDATE Flash matching the seed_candidate scenario."""
    asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)
    event = Event(
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
    quotes = {
        "DXY":    Quote(level=104.20, change=-0.35, asof=asof),
        "USDJPY": Quote(level=157.30, change=-0.82, asof=asof),
        "EURUSD": Quote(level=1.0730, change=+0.0041, asof=asof),
    }
    mc = MarketContext(quotes=quotes, history={})

    flash = Flash(status=FlashStatus.CANDIDATE, dedup_key=event.dedup_key)
    flash.set_event(event)
    flash.set_market_context(mc)
    return flash


# ---------------------------------------------------------------------------
# Core generation tests
# ---------------------------------------------------------------------------

class TestGenerateDraft:
    def test_sets_draft_text_to_model_output(self):
        flash = _make_flash()
        fake = _make_fake_client()
        result = generate_draft(flash, client=fake)
        assert result.draft_text == FAKE_BODY

    def test_status_advances_to_draft(self):
        flash = _make_flash()
        result = generate_draft(flash, client=_make_fake_client())
        assert result.status == FlashStatus.DRAFT

    def test_subject_is_non_empty(self):
        flash = _make_flash()
        generate_draft(flash, client=_make_fake_client())
        assert flash.subject
        assert len(flash.subject) > 0

    def test_subject_contains_event_title(self):
        flash = _make_flash()
        generate_draft(flash, client=_make_fake_client())
        # The deterministic subject should reference the event title.
        assert "US CPI (YoY)" in flash.subject

    def test_returns_same_flash_object(self):
        flash = _make_flash()
        result = generate_draft(flash, client=_make_fake_client())
        assert result is flash

    def test_model_override_is_passed_to_client(self):
        """Verify the resolved model name is forwarded to the API."""
        received: dict = {}

        class CapturingMessages:
            def create(self, **kwargs):
                received.update(kwargs)
                content_block = SimpleNamespace(text=FAKE_BODY, type="text")
                return SimpleNamespace(content=[content_block])

        class CapturingClient:
            messages = CapturingMessages()

        flash = _make_flash()
        generate_draft(flash, model="claude-opus-4-8", client=CapturingClient())
        assert received.get("model") == "claude-opus-4-8"

    def test_raises_invalid_transition_on_non_candidate(self):
        """Calling generate_draft on an already-DRAFT flash must raise."""
        flash = _make_flash()
        # First call succeeds
        generate_draft(flash, client=_make_fake_client())
        assert flash.status == FlashStatus.DRAFT

        # Second call: advance_to(DRAFT) from DRAFT is illegal
        with pytest.raises(InvalidTransition):
            generate_draft(flash, client=_make_fake_client())


# ---------------------------------------------------------------------------
# Section-content tests (leverage the fake response)
# ---------------------------------------------------------------------------

class TestDraftTextContent:
    def test_draft_contains_greeting(self):
        flash = _make_flash()
        generate_draft(flash, client=_make_fake_client())
        assert "Bonsoir chers clients," in flash.draft_text

    def test_draft_contains_synthese(self):
        flash = _make_flash()
        generate_draft(flash, client=_make_fake_client())
        assert "Synthèse" in flash.draft_text


# ---------------------------------------------------------------------------
# Pure prompt-builder tests
# ---------------------------------------------------------------------------

class TestBuildUserMessage:
    def _get_event_and_mc(self):
        asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)
        event = Event(
            source="calendar",
            title="US CPI (YoY)",
            currency="USD",
            country="US",
            impact="High",
            event_time=datetime(2025, 6, 11, 12, 30, tzinfo=timezone.utc),
            actual="3.3%",
            forecast="3.4%",
            previous="3.5%",
            dedup_key="test-key",
        )
        quotes = {"DXY": Quote(level=104.20, change=-0.35, asof=asof)}
        mc = MarketContext(quotes=quotes, history={})
        return event, mc

    def test_contains_event_title(self):
        event, mc = self._get_event_and_mc()
        msg = build_user_message(event, mc)
        assert "US CPI (YoY)" in msg

    def test_contains_dxy_level(self):
        event, mc = self._get_event_and_mc()
        msg = build_user_message(event, mc)
        assert "104.2" in msg or "104,2" in msg or "DXY" in msg

    def test_contains_actual_and_forecast(self):
        event, mc = self._get_event_and_mc()
        msg = build_user_message(event, mc)
        assert "3.3%" in msg
        assert "3.4%" in msg

    def test_contains_country(self):
        event, mc = self._get_event_and_mc()
        msg = build_user_message(event, mc)
        assert "US" in msg

    def test_trend_included_when_history_provided(self):
        asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)
        event = Event(
            source="calendar",
            title="Test Event",
            dedup_key="test-key-2",
        )
        base = datetime(2025, 5, 12, tzinfo=timezone.utc)
        from datetime import timedelta
        pts = [
            HistoryPoint(t=base + timedelta(days=i), value=100.0 + i * 0.1)
            for i in range(10)
        ]
        mc = MarketContext(quotes={}, history={"DXY": pts})
        msg = build_user_message(event, mc)
        assert "DXY" in msg
        assert "tendance" in msg.lower()


class TestLoadExample:
    def test_returns_string(self):
        result = load_example()
        assert isinstance(result, str)

    def test_contains_greeting(self):
        result = load_example()
        assert "Bonsoir chers clients," in result

    def test_placeholder_comment_stripped(self):
        result = load_example()
        assert "PLACEHOLDER" not in result
        assert "<!--" not in result

    def test_contains_synthese(self):
        result = load_example()
        assert "Synthèse" in result

    def test_is_non_empty(self):
        result = load_example()
        assert len(result) > 200
