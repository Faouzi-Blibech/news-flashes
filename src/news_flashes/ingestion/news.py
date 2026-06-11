"""FX news ingestion from NewsAPI.org.

Public API
----------
infer_currency(text: str) -> str | None
    Pure function: scan *text* for currency keywords and return a basket
    currency code, or None if no match.

map_article(raw: dict) -> Event
    Pure function: converts one raw NewsAPI article dict to an Event.
    Testable without any network access.

fetch_news_events() -> list[Event]
    Fetches FX-relevant headlines from NewsAPI.org, maps them to Events, and
    filters to only those whose currency is in the basket.  Uses httpx.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from news_flashes.config import settings
from news_flashes.models.schema import Event

# Default endpoint — overridden implicitly via settings.news_api_key at runtime.
_DEFAULT_URL = "https://newsapi.org/v2/everything"

# ---------------------------------------------------------------------------
# Currency-keyword map
# Keys are lowercase substrings to scan for; values are ISO-4217 codes.
# The first match (in iteration order — Python 3.7+ dicts preserve insertion
# order) is returned, so more specific keywords should come before shorter ones.
# ---------------------------------------------------------------------------

_KEYWORD_MAP: dict[str, str] = {
    # USD
    "dollar": "USD",
    "greenback": "USD",
    "usd": "USD",
    # EUR
    "euro": "EUR",
    "eur": "EUR",
    # JPY
    "yen": "JPY",
    "jpy": "JPY",
    # TND
    "dinar": "TND",
    "tnd": "TND",
}


def infer_currency(text: str) -> Optional[str]:
    """Return the first basket currency code found in *text*, or ``None``.

    The scan is case-insensitive.  Only currencies present in
    ``settings.basket_currencies`` are eligible; any keyword whose mapped code
    is not in the basket is skipped.

    Matching is word-boundary-aware: a keyword must appear as a standalone
    token (surrounded by non-alpha characters or at the string edge) to avoid
    false positives like "jpy" inside a longer word.  The scan iterates the
    keyword map in insertion order so more-specific keywords (e.g. "dollar")
    take precedence over shorter ones (e.g. "usd") within the same currency.
    """
    lower = text.lower()
    basket = set(settings.basket_currencies)
    for keyword, code in _KEYWORD_MAP.items():
        if code not in basket:
            continue
        # Use word boundaries so "usd" in "lausd" doesn't match, and
        # "yen" in "yen's" does match.
        if re.search(r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])", lower):
            return code
    return None


def _build_dedup_key(url: str) -> str:
    """Return a stable, collision-resistant dedup key derived from the article URL.

    Uses an MD5 hex-digest so the key is compact and safe as a DB column value.
    The ``news:`` prefix mirrors the ``calendar:`` prefix used in calendar.py.
    """
    return "news:" + hashlib.md5(url.encode()).hexdigest()


def map_article(raw: dict) -> Event:
    """Convert one raw NewsAPI article dict to an :class:`Event`.

    ``publishedAt`` is an ISO-8601 string in UTC (e.g.
    ``"2026-06-10T14:00:00Z"``); it is parsed to a timezone-aware
    :class:`datetime`.

    Currency is inferred by scanning ``title`` + ``description`` for known
    keywords.  ``impact`` is not provided by news APIs and is left as ``None``.
    """
    title: str = raw.get("title", "") or ""
    description: str = raw.get("description", "") or ""
    url: str = raw.get("url", "") or ""

    # Parse publishedAt — NewsAPI always emits "Z" suffix (UTC).
    published_at: str = raw.get("publishedAt", "")
    # Python 3.11+ handles "Z" natively; for older runtimes replace it.
    event_time: datetime = datetime.fromisoformat(
        published_at.replace("Z", "+00:00")
    )
    # Guard: ensure tz-aware even if feed ever omits offset.
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)

    # Scan the title first — it is the primary signal.  Fall back to the
    # description only when the title yields no match.  This prevents a
    # currency mentioned incidentally in the body (e.g. "sold USD reserves")
    # from overriding the article's actual subject (e.g. the Tunisian dinar).
    currency = infer_currency(title) or infer_currency(description)
    dedup_key = _build_dedup_key(url)

    return Event(
        source="news",
        title=title,
        currency=currency,
        country=None,
        impact=None,
        event_time=event_time,
        headline=title,
        url=url,
        summary=description if description else None,
        dedup_key=dedup_key,
    )


def fetch_news_events() -> list[Event]:
    """Fetch FX-relevant headlines from NewsAPI.org and return mapped Events.

    Uses ``settings.news_api_key`` for authentication.  Only articles whose
    inferred currency is in ``settings.basket_currencies`` are returned.
    Raises :class:`httpx.HTTPStatusError` on 4xx/5xx responses.
    """
    response = httpx.get(
        _DEFAULT_URL,
        params={
            "q": "forex OR currency OR FX",
            "apiKey": settings.news_api_key,
            "language": "en",
            "pageSize": 100,
        },
        timeout=10.0,
    )
    response.raise_for_status()

    raw_articles: list[dict] = response.json().get("articles", [])

    # Map all articles then filter to FX-basket currencies only.
    events = [map_article(article) for article in raw_articles]
    return [ev for ev in events if ev.currency is not None]
