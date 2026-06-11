"""seed_candidate.py — Insert one realistic candidate Flash so Person B can
build the generation/review pipeline without live feeds.

Scenario: A High-impact USD calendar event (DXY/USD-JPY context).
  - Event:  "US Non-Farm Payrolls" — the canonical FX flash trigger.
  - Market: DXY at 104.5, USD/JPY at 157.2, EUR/USD at 1.085.

Idempotent: if a Flash row with the same dedup_key already exists, the script
prints a notice and exits without inserting a duplicate.

Usage:
    python seed_candidate.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlmodel import select

from news_flashes.models.db import get_session, init_db
from news_flashes.models.schema import Event, Flash, MarketContext, Quote

# ---------------------------------------------------------------------------
# Seed data — edit this section to change the scenario
# ---------------------------------------------------------------------------

_DEDUP_KEY = "calendar:seed:USD:NFP:2026-06-06T12:30:00+00:00"

_EVENT = Event(
    source="calendar",
    title="US Non-Farm Payrolls",
    currency="USD",
    country=None,
    impact="High",
    event_time=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
    actual="272K",
    forecast="185K",
    previous="165K",
    dedup_key=_DEDUP_KEY,
)

_MARKET_CONTEXT = MarketContext(
    quotes={
        "DXY": Quote(level=104.5, change=-0.3),
        "USDJPY": Quote(level=157.2, change=0.5),
        "EURUSD": Quote(level=1.085, change=0.002),
    }
)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def seed(engine=None) -> bool:
    """Insert the seed candidate Flash.

    Parameters
    ----------
    engine:
        Optional SQLAlchemy engine (used by tests to target an in-memory DB).
        Defaults to the configured production engine.

    Returns
    -------
    bool
        ``True`` if a new row was inserted, ``False`` if it already existed.
    """
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        existing = session.exec(
            select(Flash).where(Flash.dedup_key == _DEDUP_KEY)
        ).first()

        if existing is not None:
            print(
                f"[seed_candidate] Row already exists (id={existing.id}, "
                f"dedup_key={_DEDUP_KEY!r}) — nothing inserted."
            )
            return False

        flash = Flash(
            status="candidate",
            event=_EVENT.model_dump(mode="json"),
            market_context=_MARKET_CONTEXT.model_dump(mode="json"),
            dedup_key=_DEDUP_KEY,
        )
        session.add(flash)
        session.commit()
        session.refresh(flash)
        print(
            f"[seed_candidate] Inserted candidate Flash id={flash.id} "
            f"(dedup_key={_DEDUP_KEY!r})"
        )
        print(
            f"  event title   : {_EVENT.title}"
        )
        print(
            f"  event_time    : {_EVENT.event_time.isoformat()}"
        )
        print(
            f"  impact        : {_EVENT.impact}"
        )
        print(
            f"  market quotes : "
            + ", ".join(
                f"{k}={v.level}"
                for k, v in _MARKET_CONTEXT.quotes.items()
            )
        )
        return True


if __name__ == "__main__":
    inserted = seed()
    sys.exit(0)
