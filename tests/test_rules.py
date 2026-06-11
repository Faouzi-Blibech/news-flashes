"""Tests for triggers/rules.py — flash-worthiness filtering logic.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

import pytest

from news_flashes.models.schema import Event
from news_flashes.triggers.rules import (
    filter_events,
    is_basket_relevant,
    is_high_impact,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal Event fixtures without going through ingestion
# ---------------------------------------------------------------------------

def _calendar_event(
    currency: str | None = "USD",
    impact: str | None = "High",
    dedup_key: str = "calendar:test-key",
) -> Event:
    return Event(
        source="calendar",
        title="Test Calendar Event",
        currency=currency,
        impact=impact,
        dedup_key=dedup_key,
    )


def _news_event(
    currency: str | None = "USD",
    dedup_key: str = "news:test-key",
) -> Event:
    return Event(
        source="news",
        title="Dollar hits two-year high",
        currency=currency,
        impact=None,   # news events never carry an impact level
        headline="Dollar hits two-year high",
        url="https://example.com/fx-news",
        dedup_key=dedup_key,
    )


# ---------------------------------------------------------------------------
# is_high_impact
# ---------------------------------------------------------------------------

class TestIsHighImpact:
    def test_high_calendar_event_returns_true(self):
        assert is_high_impact(_calendar_event(impact="High")) is True

    def test_high_case_insensitive(self):
        """Impact strings should be compared case-insensitively."""
        assert is_high_impact(_calendar_event(impact="high")) is True
        assert is_high_impact(_calendar_event(impact="HIGH")) is True

    def test_medium_calendar_event_returns_false(self):
        assert is_high_impact(_calendar_event(impact="Medium")) is False

    def test_low_calendar_event_returns_false(self):
        assert is_high_impact(_calendar_event(impact="Low")) is False

    def test_holiday_calendar_event_returns_false(self):
        assert is_high_impact(_calendar_event(impact="Holiday")) is False

    def test_news_event_with_none_impact_returns_true(self):
        """News events have no impact level but already passed an FX-relevance
        filter upstream; treat them as flash-worthy here."""
        assert is_high_impact(_news_event()) is True

    def test_calendar_event_with_none_impact_returns_false(self):
        """A calendar event with no impact (unexpected data) should not be
        treated as high-impact."""
        assert is_high_impact(_calendar_event(impact=None)) is False


# ---------------------------------------------------------------------------
# is_basket_relevant
# ---------------------------------------------------------------------------

class TestIsBasketRelevant:
    def test_usd_in_default_basket_returns_true(self):
        assert is_basket_relevant(_calendar_event(currency="USD")) is True

    def test_eur_in_default_basket_returns_true(self):
        assert is_basket_relevant(_calendar_event(currency="EUR")) is True

    def test_jpy_in_default_basket_returns_true(self):
        assert is_basket_relevant(_calendar_event(currency="JPY")) is True

    def test_tnd_in_default_basket_returns_true(self):
        assert is_basket_relevant(_calendar_event(currency="TND")) is True

    def test_non_basket_currency_returns_false(self):
        assert is_basket_relevant(_calendar_event(currency="GBP")) is False

    def test_none_currency_returns_false(self):
        assert is_basket_relevant(_calendar_event(currency=None)) is False

    def test_case_insensitive_match(self):
        """Currency codes in events may appear in lower case; still match."""
        assert is_basket_relevant(_calendar_event(currency="usd")) is True
        assert is_basket_relevant(_calendar_event(currency="eur")) is True

    def test_custom_basket_overrides_default(self):
        """Passing an explicit basket bypasses the settings default."""
        assert is_basket_relevant(_calendar_event(currency="GBP"), basket=["GBP"]) is True
        assert is_basket_relevant(_calendar_event(currency="USD"), basket=["GBP"]) is False

    def test_empty_basket_always_false(self):
        assert is_basket_relevant(_calendar_event(currency="USD"), basket=[]) is False


# ---------------------------------------------------------------------------
# filter_events
# ---------------------------------------------------------------------------

class TestFilterEvents:
    def _make_events(self):
        """Return a controlled list with known expected outcomes.

        kept:
          high_usd    — calendar High + USD → keep
          news_usd    — news event + USD → keep (news counts as high-impact)
        dropped:
          low_usd     — calendar Low + USD → drop (not high-impact)
          high_gbp    — calendar High + GBP → drop (not basket-relevant)
          dup_usd     — same dedup_key as high_usd → drop (duplicate)
        """
        high_usd = _calendar_event(
            currency="USD", impact="High", dedup_key="calendar:usd-cpi"
        )
        low_usd = _calendar_event(
            currency="USD", impact="Low", dedup_key="calendar:usd-low"
        )
        high_gbp = _calendar_event(
            currency="GBP", impact="High", dedup_key="calendar:gbp-high"
        )
        dup_usd = _calendar_event(
            currency="USD", impact="High", dedup_key="calendar:usd-cpi"  # same key
        )
        news_usd = _news_event(currency="USD", dedup_key="news:usd-article")
        return high_usd, low_usd, high_gbp, dup_usd, news_usd

    def test_keeps_high_impact_basket_events(self):
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        dedup_keys = [ev.dedup_key for ev in result]
        assert "calendar:usd-cpi" in dedup_keys
        assert "news:usd-article" in dedup_keys

    def test_drops_low_impact_event(self):
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        dedup_keys = [ev.dedup_key for ev in result]
        assert "calendar:usd-low" not in dedup_keys

    def test_drops_non_basket_event(self):
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        dedup_keys = [ev.dedup_key for ev in result]
        assert "calendar:gbp-high" not in dedup_keys

    def test_deduplicates_by_dedup_key_first_wins(self):
        """When two events share a dedup_key the first one is kept."""
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        usd_cpi_events = [ev for ev in result if ev.dedup_key == "calendar:usd-cpi"]
        assert len(usd_cpi_events) == 1

    def test_result_count(self):
        """Exactly 2 events should survive from the fixture list."""
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        assert len(result) == 2

    def test_order_preserved(self):
        """Surviving events must appear in their original relative order."""
        high_usd, low_usd, high_gbp, dup_usd, news_usd = self._make_events()
        result = filter_events([high_usd, low_usd, high_gbp, dup_usd, news_usd])
        # high_usd (index 0) must precede news_usd (index 4)
        keys = [ev.dedup_key for ev in result]
        assert keys.index("calendar:usd-cpi") < keys.index("news:usd-article")

    def test_empty_input_returns_empty(self):
        assert filter_events([]) == []

    def test_all_filtered_returns_empty(self):
        """When no event passes the filter the result is an empty list."""
        low_usd = _calendar_event(currency="USD", impact="Low", dedup_key="k1")
        high_gbp = _calendar_event(currency="GBP", impact="High", dedup_key="k2")
        assert filter_events([low_usd, high_gbp]) == []

    def test_custom_basket_respected(self):
        """Passing an explicit basket to filter_events overrides the default."""
        high_gbp = _calendar_event(currency="GBP", impact="High", dedup_key="k-gbp")
        high_usd = _calendar_event(currency="USD", impact="High", dedup_key="k-usd")
        result = filter_events([high_gbp, high_usd], basket=["GBP"])
        assert len(result) == 1
        assert result[0].dedup_key == "k-gbp"
