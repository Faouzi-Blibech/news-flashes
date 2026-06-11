"""Forex Factory economic calendar ingestion.

Public API
----------
map_item(raw: dict) -> Event
    Pure function: converts one raw Forex Factory JSON item to an Event.
    Testable without any network access.

fetch_calendar_events() -> list[Event]
    Fetches the weekly calendar from Forex Factory and returns all items
    mapped to Events.  Uses httpx for the HTTP call.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import httpx

from news_flashes.config import settings
from news_flashes.models.schema import Event

# Default URL — overridden by settings.forex_factory_url at runtime.
_DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Network timeout (seconds) — matches news.py / market_data.py so a slow or
# unresponsive feed cannot block the scheduler thread indefinitely.
_TIMEOUT = 10.0


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    """Return None if *value* is None or an empty / whitespace-only string."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _build_dedup_key(currency: str, title: str, event_time: datetime) -> str:
    """Return a stable, collision-resistant dedup key for a calendar event.

    Uses an MD5 hex-digest of ``calendar:<currency>:<title>:<iso_utc>`` so the
    key is compact and safe as a DB column value.
    """
    canonical = f"calendar:{currency}:{title}:{event_time.isoformat()}"
    return "calendar:" + hashlib.md5(canonical.encode()).hexdigest()


def map_item(raw: dict) -> Event:
    """Convert one raw Forex Factory calendar item to an :class:`Event`.

    The Forex Factory JSON field ``country`` carries the *currency* code
    (e.g. ``"USD"``, ``"EUR"``).  There is no separate ISO-3166 country code
    in the feed, so ``Event.country`` is left as ``None``.

    ``date`` is an ISO-8601 string with UTC offset (e.g.
    ``"2026-06-10T12:30:00-04:00"``); it is parsed to a timezone-aware
    :class:`datetime` and stored in ``event_time``.

    Missing or empty ``actual``/``forecast``/``previous`` are normalised to
    ``None``.
    """
    currency: str = raw.get("country", "") or ""
    title: str = raw.get("title", "") or ""

    # Parse the ISO-8601 date string — Python 3.11+ handles offsets natively.
    date_str: str = raw.get("date", "")
    event_time: datetime = datetime.fromisoformat(date_str)
    # Ensure the datetime is timezone-aware (the feed always includes an offset,
    # but guard defensively).
    if event_time.tzinfo is None:
        from datetime import timezone
        event_time = event_time.replace(tzinfo=timezone.utc)

    dedup_key = _build_dedup_key(currency, title, event_time)

    return Event(
        source="calendar",
        title=title,
        currency=currency if currency else None,
        country=None,           # FF reuses this field for the currency code
        impact=raw.get("impact") or None,
        event_time=event_time,
        actual=_empty_to_none(raw.get("actual")),
        forecast=_empty_to_none(raw.get("forecast")),
        previous=_empty_to_none(raw.get("previous")),
        dedup_key=dedup_key,
    )


def fetch_calendar_events() -> list[Event]:
    """Fetch the Forex Factory weekly calendar and return all events as a list.

    Uses ``settings.forex_factory_url`` (falls back to the hard-coded default
    if the setting is empty).  Raises :class:`httpx.HTTPStatusError` on 4xx/5xx.
    """
    url: str = settings.forex_factory_url or _DEFAULT_URL
    response = httpx.get(url, timeout=_TIMEOUT)
    response.raise_for_status()
    raw_items: list[dict] = response.json()
    return [map_item(item) for item in raw_items]
