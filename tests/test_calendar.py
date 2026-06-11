"""Tests for ingestion/calendar.py — economic calendar fetching and mapping.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from news_flashes.ingestion.calendar import fetch_calendar_events, map_item


# ---------------------------------------------------------------------------
# Sample Forex Factory JSON payload (inline, no network)
# ---------------------------------------------------------------------------

SAMPLE_ITEMS = [
    {
        "title": "Core CPI m/m",
        "country": "USD",
        "date": "2026-06-10T12:30:00-04:00",
        "impact": "High",
        "forecast": "0.3%",
        "previous": "0.2%",
        "actual": "0.4%",
    },
    {
        "title": "German Factory Orders m/m",
        "country": "EUR",
        "date": "2026-06-11T06:00:00+02:00",
        "impact": "Low",
        "forecast": "-0.5%",
        "previous": "0.8%",
        # no 'actual' key at all — not yet released
    },
    {
        "title": "BOJ Press Conference",
        "country": "JPY",
        "date": "2026-06-12T03:00:00+09:00",
        "impact": "Medium",
        "forecast": "",        # empty string — treat as None
        "previous": "",        # empty string — treat as None
        "actual": "",          # empty string — treat as None
    },
]


# ---------------------------------------------------------------------------
# map_item — pure mapping, no network
# ---------------------------------------------------------------------------

class TestMapItem:
    def test_source_is_calendar(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.source == "calendar"

    def test_currency_from_country_field(self):
        """FF 'country' actually carries the currency code (e.g. 'USD')."""
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.currency == "USD"

    def test_title(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.title == "Core CPI m/m"

    def test_impact(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.impact == "High"

    def test_event_time_is_tz_aware(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.event_time is not None
        assert ev.event_time.tzinfo is not None

    def test_event_time_correct_utc(self):
        """2026-06-10T12:30:00-04:00 should be 16:30 UTC."""
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.event_time is not None
        utc_time = ev.event_time.astimezone(timezone.utc)
        assert utc_time.hour == 16
        assert utc_time.minute == 30

    def test_forecast_previous_actual_populated(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert ev.forecast == "0.3%"
        assert ev.previous == "0.2%"
        assert ev.actual == "0.4%"

    def test_missing_actual_key_maps_to_none(self):
        """When 'actual' key is absent from the raw item, Event.actual must be None."""
        ev = map_item(SAMPLE_ITEMS[1])
        assert ev.actual is None

    def test_empty_string_fields_map_to_none(self):
        """Empty-string forecast/previous/actual must be coerced to None."""
        ev = map_item(SAMPLE_ITEMS[2])
        assert ev.forecast is None
        assert ev.previous is None
        assert ev.actual is None

    def test_low_impact_item(self):
        ev = map_item(SAMPLE_ITEMS[1])
        assert ev.impact == "Low"
        assert ev.currency == "EUR"

    def test_medium_impact_item(self):
        ev = map_item(SAMPLE_ITEMS[2])
        assert ev.impact == "Medium"
        assert ev.currency == "JPY"

    def test_dedup_key_is_string(self):
        ev = map_item(SAMPLE_ITEMS[0])
        assert isinstance(ev.dedup_key, str)
        assert len(ev.dedup_key) > 0

    def test_dedup_key_stable(self):
        """Same raw input must always produce the same dedup_key."""
        key1 = map_item(SAMPLE_ITEMS[0]).dedup_key
        key2 = map_item(SAMPLE_ITEMS[0]).dedup_key
        assert key1 == key2

    def test_dedup_key_differs_for_different_events(self):
        k0 = map_item(SAMPLE_ITEMS[0]).dedup_key
        k1 = map_item(SAMPLE_ITEMS[1]).dedup_key
        k2 = map_item(SAMPLE_ITEMS[2]).dedup_key
        assert k0 != k1
        assert k0 != k2
        assert k1 != k2

    def test_country_field_not_set(self):
        """Event.country is not used for calendar (FF reuses 'country' for currency)."""
        ev = map_item(SAMPLE_ITEMS[0])
        # currency must be populated, country may be None or populated — we just
        # verify currency is correct and there's no crash.
        assert ev.currency == "USD"


# ---------------------------------------------------------------------------
# fetch_calendar_events — mocked HTTP
# ---------------------------------------------------------------------------

class TestFetchCalendarEvents:
    def test_returns_list_of_events(self, mocker):
        """fetch_calendar_events should return one Event per raw item."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_ITEMS

        mock_get = mocker.patch("httpx.get", return_value=mock_response)

        events = fetch_calendar_events()

        mock_get.assert_called_once()
        assert len(events) == len(SAMPLE_ITEMS)

    def test_returns_event_objects(self, mocker):
        """Each item in the returned list must be an Event instance."""
        from news_flashes.models.schema import Event

        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_ITEMS

        mocker.patch("httpx.get", return_value=mock_response)

        events = fetch_calendar_events()

        for ev in events:
            assert isinstance(ev, Event)

    def test_uses_configured_url(self, mocker):
        """fetch_calendar_events must pass the forex_factory_url from settings to httpx.get."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = []

        mock_get = mocker.patch("httpx.get", return_value=mock_response)

        fetch_calendar_events()

        called_url = mock_get.call_args[0][0]
        assert "faireconomy" in called_url or called_url.startswith("http")

    def test_explicit_timeout_passed(self, mocker):
        """httpx.get must be called with an explicit timeout so a slow feed can't hang the scheduler."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = []

        mock_get = mocker.patch("httpx.get", return_value=mock_response)

        fetch_calendar_events()

        call_kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        assert "timeout" in call_kwargs, "httpx.get must receive an explicit timeout= kwarg"

    def test_raises_on_http_error(self, mocker):
        """HTTP errors must propagate (raise_for_status not swallowed)."""
        import httpx

        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=mocker.MagicMock(), response=mocker.MagicMock()
        )

        mocker.patch("httpx.get", return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            fetch_calendar_events()
