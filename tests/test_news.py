"""Tests for ingestion/news.py — news fetching and mapping.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from news_flashes.ingestion.news import (
    fetch_news_events,
    infer_currency,
    map_article,
)
from news_flashes.models.schema import Event


# ---------------------------------------------------------------------------
# Sample NewsAPI.org payload (inline, no network)
# ---------------------------------------------------------------------------

SAMPLE_ARTICLES = [
    {
        "title": "Dollar rallies as Fed signals higher-for-longer rates",
        "description": "The greenback surged against major peers on Wednesday after Fed officials hinted at prolonged tightening.",
        "url": "https://example.com/dollar-rallies",
        "publishedAt": "2026-06-10T14:00:00Z",
        "source": {"name": "Reuters"},
    },
    {
        "title": "Euro slides on weak German data",
        "description": "The EUR fell sharply following a disappointing German industrial output report.",
        "url": "https://example.com/euro-slides",
        "publishedAt": "2026-06-10T09:30:00Z",
        "source": {"name": "Bloomberg"},
    },
    {
        "title": "Yen weakens as BOJ keeps ultra-loose policy",
        "description": "The Japanese yen hit a new low after the Bank of Japan opted to maintain its yield curve control.",
        "url": "https://example.com/yen-weakens",
        "publishedAt": "2026-06-10T03:00:00Z",
        "source": {"name": "FT"},
    },
    {
        "title": "Dinar exchange rate stable amid central bank intervention",
        "description": "The Tunisian dinar held steady as the BCT sold USD reserves to defend the peg.",
        "url": "https://example.com/dinar-stable",
        "publishedAt": "2026-06-10T11:00:00Z",
        "source": {"name": "TAP"},
    },
    {
        "title": "Celebrity gossip: Hollywood's biggest breakup of the year",
        "description": "Sources close to the couple confirm the split after months of speculation.",
        "url": "https://example.com/celebrity-gossip",
        "publishedAt": "2026-06-10T08:00:00Z",
        "source": {"name": "TMZ"},
    },
]

SAMPLE_RESPONSE = {"articles": SAMPLE_ARTICLES}


# ---------------------------------------------------------------------------
# infer_currency — pure function, no network
# ---------------------------------------------------------------------------

class TestInferCurrency:
    def test_dollar_keyword_maps_to_usd(self):
        assert infer_currency("Dollar rallies after Fed statement") == "USD"

    def test_greenback_maps_to_usd(self):
        assert infer_currency("The greenback surged") == "USD"

    def test_usd_ticker_maps_to_usd(self):
        assert infer_currency("USD/JPY breaks above 150") == "USD"

    def test_euro_keyword_maps_to_eur(self):
        assert infer_currency("Euro slides on weak PMI data") == "EUR"

    def test_eur_ticker_maps_to_eur(self):
        assert infer_currency("EUR falls below 1.05") == "EUR"

    def test_yen_keyword_maps_to_jpy(self):
        assert infer_currency("Yen hits multi-decade low") == "JPY"

    def test_jpy_ticker_maps_to_jpy(self):
        assert infer_currency("JPY weakens on BOJ decision") == "JPY"

    def test_dinar_keyword_maps_to_tnd(self):
        assert infer_currency("Dinar stable after central bank move") == "TND"

    def test_tnd_ticker_maps_to_tnd(self):
        assert infer_currency("TND exchange rate update") == "TND"

    def test_case_insensitive(self):
        assert infer_currency("DOLLAR dominates FX markets") == "USD"
        assert infer_currency("the EURO rallied") == "EUR"

    def test_no_match_returns_none(self):
        assert infer_currency("Celebrity gossip from Hollywood") is None

    def test_gibberish_returns_none(self):
        assert infer_currency("xyzzy frobnicator blorple") is None

    def test_empty_string_returns_none(self):
        assert infer_currency("") is None


# ---------------------------------------------------------------------------
# map_article — pure mapping, no network
# ---------------------------------------------------------------------------

class TestMapArticle:
    def test_source_is_news(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.source == "news"

    def test_title_populated(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.title == SAMPLE_ARTICLES[0]["title"]

    def test_headline_equals_title(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.headline == SAMPLE_ARTICLES[0]["title"]

    def test_url_populated(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.url == SAMPLE_ARTICLES[0]["url"]

    def test_summary_equals_description(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.summary == SAMPLE_ARTICLES[0]["description"]

    def test_event_time_is_tz_aware(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.event_time is not None
        assert ev.event_time.tzinfo is not None

    def test_event_time_correct_utc(self):
        """publishedAt 2026-06-10T14:00:00Z should parse to 14:00 UTC."""
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.event_time is not None
        utc_time = ev.event_time.astimezone(timezone.utc)
        assert utc_time.hour == 14
        assert utc_time.minute == 0

    def test_currency_inferred_from_title_and_description(self):
        """Dollar article → USD."""
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.currency == "USD"

    def test_currency_euro_article(self):
        ev = map_article(SAMPLE_ARTICLES[1])
        assert ev.currency == "EUR"

    def test_currency_jpy_article(self):
        ev = map_article(SAMPLE_ARTICLES[2])
        assert ev.currency == "JPY"

    def test_currency_tnd_article(self):
        ev = map_article(SAMPLE_ARTICLES[3])
        assert ev.currency == "TND"

    def test_impact_is_none(self):
        """News articles have no impact level."""
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.impact is None

    def test_country_is_none(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.country is None

    def test_calendar_extras_are_none(self):
        """News events must not populate calendar-specific fields."""
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.actual is None
        assert ev.forecast is None
        assert ev.previous is None

    def test_returns_event_instance(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert isinstance(ev, Event)

    def test_dedup_key_is_non_empty_string(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert isinstance(ev.dedup_key, str)
        assert len(ev.dedup_key) > 0

    def test_dedup_key_stable(self):
        """Same raw input must always produce the same dedup_key."""
        key1 = map_article(SAMPLE_ARTICLES[0]).dedup_key
        key2 = map_article(SAMPLE_ARTICLES[0]).dedup_key
        assert key1 == key2

    def test_dedup_key_differs_for_different_articles(self):
        keys = [map_article(a).dedup_key for a in SAMPLE_ARTICLES[:4]]
        assert len(set(keys)) == 4, "All four FX articles must have unique dedup_keys"

    def test_dedup_key_prefixed_with_news(self):
        ev = map_article(SAMPLE_ARTICLES[0])
        assert ev.dedup_key.startswith("news:")


# ---------------------------------------------------------------------------
# fetch_news_events — mocked HTTP, no network
# ---------------------------------------------------------------------------

class TestFetchNewsEvents:
    def test_returns_list(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_RESPONSE

        mocker.patch("httpx.get", return_value=mock_response)

        result = fetch_news_events()
        assert isinstance(result, list)

    def test_irrelevant_article_filtered_out(self, mocker):
        """The celebrity gossip article must not appear in the result."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_RESPONSE

        mocker.patch("httpx.get", return_value=mock_response)

        result = fetch_news_events()
        urls = [ev.url for ev in result]
        assert "https://example.com/celebrity-gossip" not in urls

    def test_fx_relevant_articles_returned(self, mocker):
        """All four FX articles (USD, EUR, JPY, TND) must appear."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_RESPONSE

        mocker.patch("httpx.get", return_value=mock_response)

        result = fetch_news_events()
        assert len(result) == 4

    def test_returns_event_objects(self, mocker):
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = SAMPLE_RESPONSE

        mocker.patch("httpx.get", return_value=mock_response)

        for ev in fetch_news_events():
            assert isinstance(ev, Event)

    def test_uses_api_key_from_settings(self, mocker):
        """fetch_news_events must pass settings.news_api_key in the request."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"articles": []}

        mock_get = mocker.patch("httpx.get", return_value=mock_response)

        fetch_news_events()

        call_kwargs = mock_get.call_args
        # API key can be in params or baked into the URL
        called_url = call_kwargs[0][0] if call_kwargs[0] else ""
        called_params = call_kwargs[1].get("params", {}) if call_kwargs[1] else {}
        assert "apiKey" in called_url or "apiKey" in called_params or "apikey" in called_params

    def test_explicit_timeout_passed(self, mocker):
        """httpx.get must be called with an explicit timeout (lessons from calendar.py audit)."""
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"articles": []}

        mock_get = mocker.patch("httpx.get", return_value=mock_response)

        fetch_news_events()

        call_kwargs = mock_get.call_args[1] if mock_get.call_args[1] else {}
        assert "timeout" in call_kwargs, "httpx.get must receive an explicit timeout= kwarg"

    def test_raises_on_http_error(self, mocker):
        """HTTP errors from the news API must propagate."""
        import httpx

        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=mocker.MagicMock(), response=mocker.MagicMock()
        )

        mocker.patch("httpx.get", return_value=mock_response)

        with pytest.raises(httpx.HTTPStatusError):
            fetch_news_events()
