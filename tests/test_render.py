"""Tests for delivery/charts.py and delivery/render.py.

All matplotlib operations use the Agg (non-interactive) backend, set
before any pyplot import inside charts.py, so these tests run headlessly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from news_flashes.models.schema import Flash, FlashStatus, HistoryPoint, MarketContext, Quote
from news_flashes.delivery.charts import chart_data_uri, render_history_chart
from news_flashes.delivery.render import load_disclaimer, render_email


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_history(start_value: float, days: int = 5) -> list[HistoryPoint]:
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    return [
        HistoryPoint(t=base + timedelta(days=i), value=start_value + i * 0.1)
        for i in range(days)
    ]


def _make_market_context() -> MarketContext:
    asof = datetime(2025, 6, 11, tzinfo=timezone.utc)
    return MarketContext(
        quotes={
            "DXY": Quote(level=104.20, change=-0.35, asof=asof),
            "USDJPY": Quote(level=157.30, change=-0.82, asof=asof),
        },
        history={
            "DXY": _make_history(104.0),
            "USDJPY": _make_history(156.0),
        },
    )


def _make_flash(market_context: MarketContext | None = None) -> Flash:
    if market_context is None:
        market_context = _make_market_context()

    flash = Flash(
        status=FlashStatus.APPROVED,
        dedup_key="test:render:1",
        subject="Flash FX — Test | US",
        edited_text=(
            "**Synthèse**\n\n"
            "Le CPI américain est ressorti à **3,3 %**, en dessous des attentes.\n\n"
            "**DXY** cède 0,35 % à **104,20**."
        ),
    )
    flash.set_market_context(market_context)
    flash.set_event(
        __import__("news_flashes.models.schema", fromlist=["Event"]).Event(
            source="calendar",
            title="Test Event",
            dedup_key="test:render:1",
        )
    )
    return flash


# ---------------------------------------------------------------------------
# render_history_chart
# ---------------------------------------------------------------------------

class TestRenderHistoryChart:
    def test_returns_png_bytes_with_history(self):
        mc = _make_market_context()
        png = render_history_chart(mc)
        assert png is not None
        assert isinstance(png, bytes)
        assert len(png) > 0
        # PNG magic bytes
        assert png[:4] == b"\x89PNG"

    def test_returns_none_for_empty_market_context(self):
        mc = MarketContext()
        result = render_history_chart(mc)
        assert result is None

    def test_returns_none_when_no_matching_symbols(self):
        mc = MarketContext(
            history={"DXY": _make_history(104.0), "USDJPY": _make_history(156.0)},
        )
        result = render_history_chart(mc, symbols=["GBPUSD"])
        assert result is None

    def test_explicit_symbols_subset(self):
        mc = _make_market_context()
        png = render_history_chart(mc, symbols=["DXY"])
        assert png is not None
        assert png[:4] == b"\x89PNG"

    def test_falls_back_to_available_symbols_when_preferred_absent(self):
        mc = MarketContext(
            history={"EURUSD": _make_history(1.07)},
        )
        png = render_history_chart(mc)
        assert png is not None
        assert png[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# chart_data_uri
# ---------------------------------------------------------------------------

class TestChartDataUri:
    def test_produces_data_uri_prefix(self):
        mc = _make_market_context()
        png = render_history_chart(mc)
        assert png is not None
        uri = chart_data_uri(png)
        assert uri.startswith("data:image/png;base64,")

    def test_uri_is_string(self):
        mc = _make_market_context()
        png = render_history_chart(mc)
        assert png is not None
        uri = chart_data_uri(png)
        assert isinstance(uri, str)


# ---------------------------------------------------------------------------
# load_disclaimer
# ---------------------------------------------------------------------------

class TestLoadDisclaimer:
    def test_does_not_contain_hash_comment_line(self):
        text = load_disclaimer()
        for line in text.splitlines():
            assert not line.strip().startswith("#"), (
                f"Disclaimer still contains internal comment line: {line!r}"
            )

    def test_contains_legal_body(self):
        text = load_disclaimer()
        # Should contain at least part of the actual legal body
        assert "informatif" in text or "instrument financier" in text


# ---------------------------------------------------------------------------
# render_email
# ---------------------------------------------------------------------------

class TestRenderEmail:
    def test_html_contains_subject(self):
        flash = _make_flash()
        html = render_email(flash)
        assert "Flash FX — Test | US" in html

    def test_html_contains_body_text(self):
        flash = _make_flash()
        html = render_email(flash)
        # The word "Synthèse" should survive Markdown rendering
        assert "Synthèse" in html

    def test_html_contains_disclaimer_body(self):
        flash = _make_flash()
        html = render_email(flash)
        # Fragment from the real disclaimer body
        assert "informatif" in html or "instrument financier" in html

    def test_html_does_not_contain_hash_comment(self):
        flash = _make_flash()
        html = render_email(flash)
        assert "BROUILLON" not in html
        # Verify no line in rendered HTML starts with the internal marker
        for line in html.splitlines():
            assert not line.strip().startswith("# BROUILLON"), (
                f"Internal comment leaked into HTML: {line!r}"
            )

    def test_html_contains_chart_when_history_present(self):
        flash = _make_flash()
        html = render_email(flash)
        assert "<img" in html
        assert "data:image/png;base64," in html

    def test_html_no_chart_when_no_history(self):
        mc = MarketContext()  # empty history
        flash = _make_flash(market_context=mc)
        html = render_email(flash)
        assert "data:image/png;base64," not in html

    def test_prefers_edited_text_over_draft_text(self):
        flash = _make_flash()
        flash.draft_text = "DRAFT only text"
        flash.edited_text = "EDITED final text"
        html = render_email(flash)
        assert "EDITED final text" in html
        assert "DRAFT only text" not in html

    def test_falls_back_to_draft_when_no_edited_text(self):
        flash = _make_flash()
        flash.draft_text = "Only draft available"
        flash.edited_text = None
        html = render_email(flash)
        assert "Only draft available" in html

    def test_explicit_chart_png_override(self):
        """Passing explicit chart_png bytes should embed those bytes."""
        mc = MarketContext()  # no history, wouldn't auto-generate
        flash = _make_flash(market_context=mc)

        # Generate a real PNG from a different context to use as override
        mc2 = _make_market_context()
        png = render_history_chart(mc2)
        assert png is not None

        html = render_email(flash, chart_png=png)
        assert "data:image/png;base64," in html

    def test_include_chart_false_suppresses_chart(self):
        flash = _make_flash()
        html = render_email(flash, include_chart=False)
        assert "data:image/png;base64," not in html

    def test_no_crash_and_no_img_when_empty_market_context(self):
        mc = MarketContext()
        flash = _make_flash(market_context=mc)
        html = render_email(flash)
        assert isinstance(html, str)
        assert len(html) > 100
        assert "data:image/png;base64," not in html
