"""Flash-worthiness rules.

This module is the "editorial gate" between raw ingestion and candidate
creation.  It answers one question: *is this event worth sending a flash?*

Design principles
-----------------
- Pure logic only — no I/O, no DB, no network.
- All thresholds and rule constants live at module level so the team can
  tune editorial judgment in one place.
- Each predicate is individually testable.
- The scheduler calls ``filter_events`` between ingestion and writing
  candidate rows.

Editorial decisions (flagged for team review)
---------------------------------------------
**News events are treated as unconditionally high-impact (for the impact
predicate).** Rationale: ``news.py`` already applies an FX-relevance filter
— only articles whose text matches a basket-currency keyword reach this
module.  There is no numeric "impact" field in the NewsAPI response, but an
article that explicitly mentions the dollar, euro, yen, or dinar is already
signal-filtered.  The basket-relevance check (``is_basket_relevant``) acts as
the second gate.  If the team wants finer-grained news filtering in future
(e.g. require sentiment score > X, or only certain sources), add those
predicates here and compose them in ``filter_events``.
"""

from __future__ import annotations

from news_flashes.config import settings
from news_flashes.models.schema import Event

# ---------------------------------------------------------------------------
# Module-level constants — tune editorial thresholds here
# ---------------------------------------------------------------------------

# Calendar impact levels (as emitted by Forex Factory) that are considered
# flash-worthy.  Add "Medium" here to widen the net.
_HIGH_IMPACT_LEVELS: frozenset[str] = frozenset({"high"})


# ---------------------------------------------------------------------------
# Individual predicates
# ---------------------------------------------------------------------------

def is_high_impact(event: Event) -> bool:
    """Return True when the event is considered high-impact.

    Calendar events
    ~~~~~~~~~~~~~~~
    Uses ``event.impact`` (populated from Forex Factory's ``impact`` field).
    Only events whose impact level appears in ``_HIGH_IMPACT_LEVELS`` pass.
    Comparison is case-insensitive so both "High" and "high" are accepted.
    "Medium", "Low", and "Holiday" all return False.

    News events
    ~~~~~~~~~~~
    ``event.impact`` is always ``None`` for news because NewsAPI does not
    provide an impact rating.  However, the upstream ``news.py`` ingestion
    already filters articles to only those that mention a basket-currency
    keyword — so every news event that reaches this module has already passed
    an FX-relevance gate.  We therefore treat all news events as high-impact
    here and rely on ``is_basket_relevant`` to apply the second editorial
    gate.  See module-level docstring for the rationale and how to extend
    this in future.
    """
    if event.source == "news":
        return True
    if event.impact is None:
        return False
    return event.impact.lower() in _HIGH_IMPACT_LEVELS


def is_basket_relevant(event: Event, basket: list[str] | None = None) -> bool:
    """Return True when ``event.currency`` is in the watched currency basket.

    Parameters
    ----------
    event:
        The event to evaluate.
    basket:
        Optional explicit basket.  Defaults to ``settings.basket_currencies``
        when omitted or ``None``.  Comparison is case-insensitive on both
        sides so ``"usd"`` matches a basket containing ``"USD"``.

    Returns False when ``event.currency`` is ``None`` (i.e. the event has no
    associated currency code).
    """
    if event.currency is None:
        return False
    resolved_basket = basket if basket is not None else settings.basket_currencies
    upper_basket = {code.upper() for code in resolved_basket}
    return event.currency.upper() in upper_basket


# ---------------------------------------------------------------------------
# Top-level filter
# ---------------------------------------------------------------------------

def filter_events(
    events: list[Event],
    basket: list[str] | None = None,
) -> list[Event]:
    """Keep only flash-worthy events from *events*, then deduplicate.

    An event survives if and only if:
      1. ``is_high_impact(event)`` is True, **and**
      2. ``is_basket_relevant(event, basket)`` is True.

    After filtering, duplicates are removed by ``dedup_key``: the first
    occurrence in the original list is kept; subsequent events with the same
    key are discarded.  The relative order of surviving events is preserved.

    Parameters
    ----------
    events:
        Heterogeneous list of calendar and/or news events (e.g. the combined
        output of ``fetch_calendar_events`` + ``fetch_news_events``).
    basket:
        Optional explicit currency basket; forwarded to
        ``is_basket_relevant``.  Defaults to ``settings.basket_currencies``.

    Returns
    -------
    list[Event]
        Filtered, deduplicated list, order preserved.
    """
    seen_keys: set[str] = set()
    result: list[Event] = []

    for event in events:
        if not is_high_impact(event):
            continue
        if not is_basket_relevant(event, basket=basket):
            continue
        if event.dedup_key in seen_keys:
            continue
        seen_keys.add(event.dedup_key)
        result.append(event)

    return result
