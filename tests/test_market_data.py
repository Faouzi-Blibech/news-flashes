"""Tests for ingestion/market_data.py — FX market data fetching and mapping.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from news_flashes.ingestion.market_data import (
    fetch_market_context,
    map_quote,
    _normalise_symbol,
    DEFAULT_INSTRUMENTS,
)
from news_flashes.models.schema import MarketContext, Quote


# ---------------------------------------------------------------------------
# Sample Twelve Data quote payloads (inline, no network)
# ---------------------------------------------------------------------------

SAMPLE_USDJPY = {
    "symbol": "USD/JPY",
    "close": "157.20",
    "percent_change": "0.35",
    "datetime": "2026-06-10",
}

SAMPLE_EURUSD = {
    "symbol": "EUR/USD",
    "close": "1.0845",
    "percent_change": "-0.12",
    "datetime": "2026-06-10",
}

# DXY (may not be on free tier) — simulated as a valid but alternate format
SAMPLE_DXY = {
    "symbol": "DXY",
    "close": "104.50",
    "percent_change": "0.08",
    "datetime": "2026-06-10",
}

# Malformed / missing fields
SAMPLE_MISSING_CHANGE = {
    "symbol": "USD/JPY",
    "close": "157.20",
    # no percent_change key
    "datetime": "2026-06-10",
}

SAMPLE_MISSING_DATETIME = {
    "symbol": "EUR/USD",
    "close": "1.0845",
    "percent_change": "-0.12",
    # no datetime key
}

SAMPLE_BAD_DATETIME = {
    "symbol": "EUR/USD",
    "close": "1.0845",
    "percent_change": "-0.12",
    "datetime": "not-a-date",
}

SAMPLE_MISSING_CLOSE = {
    "symbol": "EUR/USD",
    # no close key at all
    "percent_change": "0.1",
    "datetime": "2026-06-10",
}


# ---------------------------------------------------------------------------
# _normalise_symbol — compact key conversion
# ---------------------------------------------------------------------------

class TestNormaliseSymbol:
    def test_slash_pair_normalised(self):
        assert _normalise_symbol("USD/JPY") == "USDJPY"

    def test_eur_usd_normalised(self):
        assert _normalise_symbol("EUR/USD") == "EURUSD"

    def test_dxy_unchanged(self):
        """DXY has no slash — must pass through as-is."""
        assert _normalise_symbol("DXY") == "DXY"

    def test_already_compact_unchanged(self):
        assert _normalise_symbol("USDJPY") == "USDJPY"

    def test_case_preserved(self):
        """Normalisation does not change case."""
        assert _normalise_symbol("EUR/USD") == "EURUSD"


# ---------------------------------------------------------------------------
# map_quote — pure mapping, no network
# ---------------------------------------------------------------------------

class TestMapQuote:
    def test_level_parsed_from_close_string(self):
        q = map_quote("USDJPY", SAMPLE_USDJPY)
        assert q.level == pytest.approx(157.20)

    def test_change_parsed_from_percent_change(self):
        q = map_quote("USDJPY", SAMPLE_USDJPY)
        assert q.change == pytest.approx(0.35)

    def test_negative_change(self):
        q = map_quote("EURUSD", SAMPLE_EURUSD)
        assert q.change == pytest.approx(-0.12)

    def test_asof_is_tz_aware(self):
        q = map_quote("USDJPY", SAMPLE_USDJPY)
        assert q.asof is not None
        assert q.asof.tzinfo is not None

    def test_asof_parsed_correctly(self):
        """2026-06-10 should parse to midnight UTC."""
        q = map_quote("USDJPY", SAMPLE_USDJPY)
        assert q.asof is not None
        assert q.asof.year == 2026
        assert q.asof.month == 6
        assert q.asof.day == 10

    def test_returns_quote_instance(self):
        q = map_quote("USDJPY", SAMPLE_USDJPY)
        assert isinstance(q, Quote)

    def test_dxy_parsed(self):
        q = map_quote("DXY", SAMPLE_DXY)
        assert q.level == pytest.approx(104.50)

    # --- robustness to missing / bad fields ---

    def test_missing_percent_change_yields_none(self):
        q = map_quote("USDJPY", SAMPLE_MISSING_CHANGE)
        assert q.change is None

    def test_missing_datetime_yields_none_asof(self):
        q = map_quote("EURUSD", SAMPLE_MISSING_DATETIME)
        assert q.asof is None

    def test_bad_datetime_yields_none_asof(self):
        q = map_quote("EURUSD", SAMPLE_BAD_DATETIME)
        assert q.asof is None

    def test_level_still_parsed_when_datetime_missing(self):
        """A bad datetime must not prevent level from being parsed."""
        q = map_quote("EURUSD", SAMPLE_MISSING_DATETIME)
        assert q.level == pytest.approx(1.0845)

    def test_level_still_parsed_when_change_missing(self):
        q = map_quote("USDJPY", SAMPLE_MISSING_CHANGE)
        assert q.level == pytest.approx(157.20)


# ---------------------------------------------------------------------------
# fetch_market_context — mocked HTTP, no network
# ---------------------------------------------------------------------------

class TestFetchMarketContext:
    def _make_mock_response(self, mocker, payload: dict):
        """Helper: build a mock httpx response returning *payload* as JSON."""
        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = payload
        return mock_resp

    def test_returns_market_context(self, mocker):
        """fetch_market_context must return a MarketContext instance."""
        mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        ctx = fetch_market_context(instruments=["USD/JPY"])
        assert isinstance(ctx, MarketContext)

    def test_compact_key_in_quotes(self, mocker):
        """The quotes dict must use compact keys (USDJPY, not USD/JPY)."""
        mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        ctx = fetch_market_context(instruments=["USD/JPY"])
        assert "USDJPY" in ctx.quotes
        assert "USD/JPY" not in ctx.quotes

    def test_quote_values_are_quote_instances(self, mocker):
        mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        ctx = fetch_market_context(instruments=["USD/JPY"])
        assert isinstance(ctx.quotes["USDJPY"], Quote)

    def test_multiple_instruments(self, mocker):
        """Each instrument gets an independent httpx.get call; all land in quotes."""
        responses = {
            "USD/JPY": SAMPLE_USDJPY,
            "EUR/USD": SAMPLE_EURUSD,
        }
        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            # determine which symbol from the URL params
            symbol = kwargs.get("params", {}).get("symbol", "")
            payload = responses.get(symbol, SAMPLE_USDJPY)
            mock_resp = mocker.MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = payload
            return mock_resp

        mocker.patch("httpx.get", side_effect=fake_get)

        ctx = fetch_market_context(instruments=["USD/JPY", "EUR/USD"])
        assert "USDJPY" in ctx.quotes
        assert "EURUSD" in ctx.quotes
        assert call_count == 2

    def test_failed_symbol_skipped_gracefully(self, mocker):
        """A per-symbol HTTP error must not abort the whole batch."""
        import httpx as _httpx

        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            symbol = kwargs.get("params", {}).get("symbol", "")
            if symbol == "DXY":
                # simulate DXY not on free tier — raise on status
                mock_resp = mocker.MagicMock()
                mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
                    "403", request=mocker.MagicMock(), response=mocker.MagicMock()
                )
                return mock_resp
            mock_resp = mocker.MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = SAMPLE_USDJPY
            return mock_resp

        mocker.patch("httpx.get", side_effect=fake_get)

        ctx = fetch_market_context(instruments=["DXY", "USD/JPY"])
        # DXY must be absent (failed), USDJPY must be present
        assert "DXY" not in ctx.quotes
        assert "USDJPY" in ctx.quotes

    def test_explicit_timeout_passed(self, mocker):
        """httpx.get must be called with an explicit timeout= kwarg."""
        mock_get = mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        fetch_market_context(instruments=["USD/JPY"])
        call_kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        assert "timeout" in call_kwargs, "httpx.get must receive an explicit timeout= kwarg"

    def test_raise_for_status_called(self, mocker):
        """raise_for_status must be called so HTTP errors surface."""
        mock_resp = self._make_mock_response(mocker, SAMPLE_USDJPY)
        mocker.patch("httpx.get", return_value=mock_resp)
        fetch_market_context(instruments=["USD/JPY"])
        mock_resp.raise_for_status.assert_called_once()

    def test_default_instruments_used_when_none_passed(self, mocker):
        """Calling fetch_market_context() with no args must still attempt fetches."""
        mock_get = mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        fetch_market_context()
        # Should have called httpx.get at least once (for each default instrument)
        assert mock_get.call_count >= 1

    def test_uses_api_key_from_settings(self, mocker):
        """The API key from settings must be passed in the request."""
        mock_get = mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        fetch_market_context(instruments=["USD/JPY"])
        call_kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        params = call_kwargs.get("params", {})
        # apikey is the Twelve Data param name
        assert "apikey" in params or "apiKey" in params

    def test_level_correct_in_result(self, mocker):
        mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        ctx = fetch_market_context(instruments=["USD/JPY"])
        assert ctx.quotes["USDJPY"].level == pytest.approx(157.20)

    def test_quotes_empty_when_all_fail(self, mocker):
        """If every symbol fails, quotes dict must be empty (not crash)."""
        import httpx as _httpx

        mock_resp = mocker.MagicMock()
        mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "403", request=mocker.MagicMock(), response=mocker.MagicMock()
        )
        mocker.patch("httpx.get", return_value=mock_resp)

        ctx = fetch_market_context(instruments=["DXY"])
        assert ctx.quotes == {}

    def test_extra_instruments_included(self, mocker):
        """Instruments passed explicitly (e.g. TND pairs) land in quotes."""
        mocker.patch(
            "httpx.get",
            return_value=self._make_mock_response(mocker, SAMPLE_USDJPY),
        )
        ctx = fetch_market_context(instruments=["USD/TND"])
        assert "USDTND" in ctx.quotes
