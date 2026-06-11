"""FX market-data ingestion from Twelve Data.

Public API
----------
map_quote(symbol: str, raw: dict) -> Quote
    Pure function: converts one raw Twelve Data quote dict to a Quote.
    Testable without any network access.

fetch_market_context(instruments: list[str] | None = None) -> MarketContext
    Fetches current quotes for each instrument, maps them to Quote objects,
    and assembles a MarketContext.  Per-symbol failures are caught and skipped
    so a single unavailable symbol (e.g. DXY on the free tier) does not abort
    the whole batch.  Uses httpx for HTTP calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from news_flashes.config import settings
from news_flashes.models.schema import MarketContext, Quote

_log = logging.getLogger(__name__)

# Base URL for the Twelve Data quote endpoint.
_BASE_URL = "https://api.twelvedata.com/quote"

# Default instrument list — in Twelve Data's API notation.
# DXY may not be available on the free tier; it is included here and handled
# gracefully (skipped on failure).
DEFAULT_INSTRUMENTS: list[str] = ["DXY", "USD/JPY", "EUR/USD"]

# HTTP request timeout (seconds).
_TIMEOUT = 10.0


def _normalise_symbol(symbol: str) -> str:
    """Convert a Twelve Data symbol to the compact MarketContext key form.

    ``"USD/JPY"`` → ``"USDJPY"``, ``"DXY"`` → ``"DXY"``.
    The slash is the only character that needs stripping; no case change is made.
    """
    return symbol.replace("/", "")


def map_quote(symbol: str, raw: dict) -> Quote:
    """Convert one raw Twelve Data quote response dict to a :class:`Quote`.

    Parameters
    ----------
    symbol:
        The compact instrument key, e.g. ``"USDJPY"`` — used only in error
        messages; not stored on the returned Quote.
    raw:
        The parsed JSON body returned by the Twelve Data ``/quote`` endpoint.

    Field mapping
    -------------
    ``close``            → ``level`` (float; required)
    ``percent_change``   → ``change`` (float or None if absent/unparseable)
    ``datetime``         → ``asof``  (tz-aware UTC datetime or None if absent/unparseable)

    All numeric fields are expected as strings ("157.20") and coerced to float.
    """
    # --- level (required) ---
    level: float = float(raw["close"])

    # --- change (optional) ---
    change: Optional[float] = None
    raw_change = raw.get("percent_change")
    if raw_change is not None:
        try:
            change = float(raw_change)
        except (ValueError, TypeError):
            _log.debug("Could not parse percent_change=%r for %s", raw_change, symbol)

    # --- asof (optional) ---
    asof: Optional[datetime] = None
    raw_dt = raw.get("datetime")
    if raw_dt is not None:
        try:
            parsed = datetime.fromisoformat(str(raw_dt))
            # Attach UTC if the string carried no timezone information.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            asof = parsed
        except (ValueError, TypeError):
            _log.debug("Could not parse datetime=%r for %s", raw_dt, symbol)

    return Quote(level=level, change=change, asof=asof)


def fetch_market_context(
    instruments: Optional[list[str]] = None,
) -> MarketContext:
    """Fetch current FX quotes and return a populated :class:`MarketContext`.

    Parameters
    ----------
    instruments:
        Symbols in Twelve Data notation, e.g. ``["USD/JPY", "EUR/USD", "DXY"]``.
        Defaults to :data:`DEFAULT_INSTRUMENTS` when ``None``.

    Each instrument is fetched individually via the Twelve Data ``/quote``
    endpoint.  A per-symbol :class:`httpx.HTTPStatusError` or any other
    exception is caught and logged; the symbol is omitted from the result rather
    than aborting the whole batch.  This makes DXY (often unavailable on the
    free tier) non-fatal.

    Raises :class:`httpx.HTTPStatusError` only if *every* symbol fails (the
    errors are swallowed per-symbol; the method itself never raises for partial
    failures).
    """
    if instruments is None:
        instruments = DEFAULT_INSTRUMENTS

    quotes: dict[str, Quote] = {}

    for raw_symbol in instruments:
        compact_key = _normalise_symbol(raw_symbol)
        try:
            response = httpx.get(
                _BASE_URL,
                params={
                    "symbol": raw_symbol,
                    "apikey": settings.market_data_api_key,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            raw = response.json()
            quotes[compact_key] = map_quote(compact_key, raw)
        except Exception as exc:
            _log.warning(
                "Skipping instrument %s (%s): %s",
                raw_symbol,
                compact_key,
                exc,
            )

    return MarketContext(quotes=quotes)
