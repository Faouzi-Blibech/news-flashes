"""Scheduler worker — orchestrates one ingestion cycle and persists candidate Flashes.

Public API
----------
run_ingestion_cycle(session) -> int
    One atomic pass: fetch events from all feeds, filter for flash-worthiness,
    attach market context, persist new candidate Flash rows (skipping any whose
    dedup_key already exists).  Returns the count of newly-written candidates.

start_scheduler()
    APScheduler entry point.  Initialises the DB, then runs run_ingestion_cycle
    every ``settings.poll_interval_minutes`` minutes using a BlockingScheduler.
    Call from ``if __name__ == "__main__"`` or a CLI entry point.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from news_flashes.config import settings
from news_flashes.ingestion.calendar import fetch_calendar_events
from news_flashes.ingestion.market_data import fetch_market_context
from news_flashes.ingestion.news import fetch_news_events
from news_flashes.models.db import get_session, init_db
from news_flashes.models.schema import Event, Flash, MarketContext
from news_flashes.triggers.rules import filter_events

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core orchestration — decoupled from APScheduler for testability
# ---------------------------------------------------------------------------

def run_ingestion_cycle(session: Session) -> int:
    """Execute one ingestion cycle and persist new candidate Flash rows.

    Steps
    -----
    1. Gather events from each feed (guarded individually — one feed failing
       does not abort the cycle).
    2. Filter combined events via ``filter_events``.
    3. Fetch market context once (guarded — failure yields ``None`` context).
    4. For each surviving event, check whether a Flash row with the same
       ``dedup_key`` already exists; skip if so.  Otherwise build and persist
       a new ``Flash(status="candidate")``.

    Parameters
    ----------
    session:
        An open SQLModel Session.  The caller is responsible for lifecycle
        (open/close/commit).

    Returns
    -------
    int
        Number of new candidate rows written in this cycle.
    """
    # ------------------------------------------------------------------
    # 1. Gather events — each feed guarded individually
    # ------------------------------------------------------------------
    all_events: list[Event] = []

    try:
        cal_events = fetch_calendar_events()
        all_events.extend(cal_events)
        _log.debug("Calendar feed: %d events", len(cal_events))
    except Exception as exc:
        _log.warning("Calendar feed failed — skipping: %s", exc)

    try:
        news_events = fetch_news_events()
        all_events.extend(news_events)
        _log.debug("News feed: %d events", len(news_events))
    except Exception as exc:
        _log.warning("News feed failed — skipping: %s", exc)

    # ------------------------------------------------------------------
    # 2. Filter for flash-worthiness
    # ------------------------------------------------------------------
    candidates = filter_events(all_events)
    _log.debug("After filter_events: %d candidates", len(candidates))

    # ------------------------------------------------------------------
    # 3. Market context (guarded — failure is non-fatal)
    # ------------------------------------------------------------------
    market_context_dict: Optional[dict] = None
    try:
        ctx: MarketContext = fetch_market_context()
        market_context_dict = ctx.model_dump(mode="json")
    except Exception as exc:
        _log.warning("Market context fetch failed — candidates will have no context: %s", exc)

    # ------------------------------------------------------------------
    # 4. Persist new candidates (dedup by dedup_key)
    # ------------------------------------------------------------------
    written = 0
    for event in candidates:
        # Check whether this dedup_key already has a Flash row.
        existing = session.exec(
            select(Flash).where(Flash.dedup_key == event.dedup_key)
        ).first()
        if existing is not None:
            _log.debug("Skipping already-persisted dedup_key=%s", event.dedup_key)
            continue

        flash = Flash(
            status="candidate",
            event=event.model_dump(mode="json"),
            market_context=market_context_dict,
            dedup_key=event.dedup_key,
        )
        session.add(flash)
        written += 1

    session.commit()
    _log.info("Ingestion cycle complete: %d new candidates written", written)
    return written


# ---------------------------------------------------------------------------
# APScheduler entry point
# ---------------------------------------------------------------------------

def _scheduled_job() -> None:
    """Wrapper used by APScheduler: opens its own session, runs one cycle."""
    with get_session() as session:
        run_ingestion_cycle(session)


def start_scheduler() -> None:
    """Initialise the DB then start the blocking scheduler loop.

    Schedules ``run_ingestion_cycle`` every ``settings.poll_interval_minutes``
    minutes.  Blocks until interrupted (KeyboardInterrupt / SIGTERM).
    """
    from apscheduler.schedulers.blocking import BlockingScheduler  # lazy import

    init_db()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        _scheduled_job,
        trigger="interval",
        minutes=settings.poll_interval_minutes,
        id="ingestion_cycle",
        name="Ingest → filter → write candidates",
        replace_existing=True,
    )

    _log.info(
        "Scheduler starting — poll interval: %d minutes",
        settings.poll_interval_minutes,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        _log.info("Scheduler stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start_scheduler()
