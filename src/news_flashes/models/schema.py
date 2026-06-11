"""Shared data contract for the news-flashes pipeline.

This module is the single source of truth imported by both the Signals half
(event ingestion) and the Voice & Delivery half (drafting, review, sending).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Status enum & transition graph
# ---------------------------------------------------------------------------

class FlashStatus(str, Enum):
    """Lifecycle states of a Flash row."""

    CANDIDATE = "candidate"
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"


#: Permitted forward transitions.  Terminal states map to empty sets.
#: NOTE: draft -> sent is intentionally absent (compliance rule).
ALLOWED_TRANSITIONS: dict[FlashStatus, set[FlashStatus]] = {
    FlashStatus.CANDIDATE: {FlashStatus.DRAFT, FlashStatus.REJECTED},
    FlashStatus.DRAFT: {FlashStatus.APPROVED, FlashStatus.REJECTED},
    FlashStatus.APPROVED: {FlashStatus.SENT, FlashStatus.REJECTED},
    FlashStatus.SENT: set(),
    FlashStatus.REJECTED: set(),
}


class InvalidTransition(Exception):
    """Raised when an illegal Flash status transition is attempted."""


# ---------------------------------------------------------------------------
# Pure Pydantic value objects (serialised to JSON columns on Flash)
# ---------------------------------------------------------------------------

class Quote(BaseModel):
    """A single market quote snapshot."""

    level: float
    change: float
    asof: datetime


class HistoryPoint(BaseModel):
    """One point in a price time-series."""

    t: datetime
    value: float


class Event(BaseModel):
    """An economic-calendar entry or news item that triggered a flash."""

    # Common fields
    source: str          # "calendar" | "news"
    title: str
    currency: str | None = None
    country: str | None = None
    impact: str | None = None
    event_time: datetime | None = None

    # Calendar extras
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None

    # News extras
    headline: str | None = None
    url: str | None = None
    summary: str | None = None

    dedup_key: str


class MarketContext(BaseModel):
    """Market data snapshot attached to a Flash at ingestion time.

    ``history`` maps instrument symbol to a time-series used for chart
    generation (e.g. "DXY", "USDJPY", "EURUSD").
    """

    quotes: dict[str, Quote] = {}
    history: dict[str, list[HistoryPoint]] = {}


# ---------------------------------------------------------------------------
# SQLModel table models
# ---------------------------------------------------------------------------

class Flash(SQLModel, table=True):
    """Persistent flash row — one potential client communication."""

    id: Optional[int] = Field(default=None, primary_key=True)
    status: FlashStatus = Field(default=FlashStatus.CANDIDATE)

    # Structured payloads stored as JSON blobs; use the typed accessors below.
    event: dict = Field(default_factory=dict, sa_column=Column(JSON))
    market_context: dict = Field(default_factory=dict, sa_column=Column(JSON))

    dedup_key: str = Field(default="", index=True)

    draft_text: Optional[str] = None
    edited_text: Optional[str] = None
    subject: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Typed accessors for JSON columns
    # ------------------------------------------------------------------

    def get_event(self) -> Event:
        """Return the ``event`` column deserialised as an :class:`Event`."""
        return Event.model_validate(self.event)

    def set_event(self, e: Event) -> None:
        """Serialise *e* and store it in the ``event`` column."""
        self.event = e.model_dump(mode="json")

    def get_market_context(self) -> MarketContext:
        """Return ``market_context`` deserialised as a :class:`MarketContext`."""
        return MarketContext.model_validate(self.market_context)

    def set_market_context(self, mc: MarketContext) -> None:
        """Serialise *mc* and store it in the ``market_context`` column."""
        self.market_context = mc.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Status-transition guard (compliance safety invariant)
    # ------------------------------------------------------------------

    def advance_to(self, new_status: FlashStatus) -> None:
        """Attempt to move this flash to *new_status*.

        Raises :class:`InvalidTransition` if the transition is not permitted
        by :data:`ALLOWED_TRANSITIONS`.  Callers are responsible for setting
        any accompanying timestamps or ``approved_by`` fields.
        """
        if new_status not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(
                f"Cannot transition Flash from {self.status!r} to {new_status!r}. "
                f"Allowed: {ALLOWED_TRANSITIONS[self.status]}"
            )
        self.status = new_status


class Client(SQLModel, table=True):
    """A client who receives flash emails."""

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    name: Optional[str] = None
    segment: str = "default"
    lang: str = "fr"
    active: bool = True
