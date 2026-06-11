"""Seed script — insert one realistic CANDIDATE Flash for a DXY/USD-JPY scenario.

Usage:
    python seed_candidate.py

Idempotent: if a Flash with the same dedup_key already exists, the script
prints a message and exits without inserting a duplicate.
"""

import sys
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from news_flashes.models.db import get_session, init_db
from news_flashes.models.schema import (
    Event,
    Flash,
    FlashStatus,
    HistoryPoint,
    MarketContext,
    Quote,
)

# ---------------------------------------------------------------------------
# Event & dedup key
# ---------------------------------------------------------------------------

DEDUP_KEY = "calendar:US_CPI_YoY:2025-06-11"

event_time = datetime(2025, 6, 11, 12, 30, tzinfo=timezone.utc)  # 08:30 ET

event = Event(
    source="calendar",
    title="US CPI (YoY)",
    currency="USD",
    country="US",
    impact="High",
    event_time=event_time,
    actual="3.3%",
    forecast="3.4%",
    previous="3.5%",
    dedup_key=DEDUP_KEY,
)

# ---------------------------------------------------------------------------
# Market quotes (realistic levels as of mid-2025)
# ---------------------------------------------------------------------------

asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)

quotes = {
    "DXY":    Quote(level=104.20, change=-0.35, asof=asof),
    "USDJPY": Quote(level=157.30, change=-0.82, asof=asof),
    "EURUSD": Quote(level=1.0730, change=+0.0041, asof=asof),
}

# ---------------------------------------------------------------------------
# History — ~30 daily points generated with slight drift
# ---------------------------------------------------------------------------

def _make_series(
    start_value: float,
    drift: float,
    noise_step: float,
    days: int = 30,
    end_date: datetime | None = None,
) -> list[HistoryPoint]:
    """Generate a plausible daily price series ending on *end_date*."""
    if end_date is None:
        end_date = datetime(2025, 6, 11, tzinfo=timezone.utc)
    start_date = end_date - timedelta(days=days - 1)
    points: list[HistoryPoint] = []
    value = start_value
    for i in range(days):
        t = start_date + timedelta(days=i)
        # Simple deterministic drift + alternating noise (no random seed needed)
        noise = noise_step * (1 if i % 2 == 0 else -1) * ((i % 5) * 0.2 + 0.1)
        value = value + drift + noise
        points.append(HistoryPoint(t=t, value=round(value, 4)))
    return points


history = {
    "DXY": _make_series(
        start_value=106.10,  # 30 days prior
        drift=-0.063,        # gentle downtrend → ~104.2 today
        noise_step=0.12,
    ),
    "USDJPY": _make_series(
        start_value=160.50,
        drift=-0.107,        # → ~157.3 today
        noise_step=0.25,
    ),
    "EURUSD": _make_series(
        start_value=1.0520,
        drift=0.0007,        # → ~1.073 today
        noise_step=0.0015,
    ),
}

market_context = MarketContext(quotes=quotes, history=history)

# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()

    with get_session() as session:
        existing = session.exec(
            select(Flash).where(Flash.dedup_key == DEDUP_KEY)
        ).first()

        if existing is not None:
            print(
                f"Flash with dedup_key={DEDUP_KEY!r} already exists "
                f"(id={existing.id}). Skipping insertion."
            )
            return

        flash = Flash(status=FlashStatus.CANDIDATE, dedup_key=DEDUP_KEY)
        flash.set_event(event)
        flash.set_market_context(market_context)

        session.add(flash)
        session.commit()
        session.refresh(flash)

        print(f"Inserted Flash id={flash.id} (status={flash.status})")


if __name__ == "__main__":
    main()
