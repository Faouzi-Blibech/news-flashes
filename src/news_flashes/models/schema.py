"""Shared data contract for the news-flashes pipeline.

Person A (ingestion / triggers / scheduler) writes rows; Person B
(generation / delivery / review) reads them. Neither imports the other's
modules — this file is the only bridge.

This is the *unified* contract reconciled at the A/B integration sync. It keeps
Person A's persistence semantics (UTC-aware datetimes, validated status, unique
``dedup_key``, ``None``-default ``market_context``) AND Person B's behavioural
API (price ``history`` for charts, the status-transition compliance guard, and
typed JSON accessors). ``FlashStatus`` exposes both lowercase (A) and uppercase
(B) member names as aliases so neither side's code needs to change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlalchemy import DateTime as _SADateTime
from sqlalchemy import TypeDecorator
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# SQLAlchemy type: stores UTC datetimes, re-attaches tzinfo on read-back
# ---------------------------------------------------------------------------

class _UTCDateTime(TypeDecorator):
    """DATETIME column that always round-trips as UTC-aware datetimes.

    SQLite stores datetimes as naive strings; this decorator re-attaches
    ``timezone.utc`` when reading so callers always receive an aware object.
    """

    impl = _SADateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=timezone.utc)
        return value


# ---------------------------------------------------------------------------
# Status enum & transition graph
# ---------------------------------------------------------------------------

class FlashStatus(str, Enum):
    """Lifecycle states of a Flash row.

    Both lowercase (Person A) and UPPERCASE (Person B) names are defined. Since
    the uppercase entries share the same *value* as the lowercase ones, Python
    treats them as aliases — e.g. ``FlashStatus.CANDIDATE is FlashStatus.candidate``
    — so code written against either naming style works unchanged.
    """

    # Canonical lowercase members
    candidate = "candidate"
    draft = "draft"
    approved = "approved"
    sent = "sent"
    rejected = "rejected"

    # Uppercase aliases (same values => aliases of the members above)
    CANDIDATE = "candidate"
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    REJECTED = "rejected"


#: Permitted forward transitions. Terminal states map to empty sets.
#: NOTE: draft -> sent is intentionally absent (financial-compliance rule:
#: nothing may be sent without passing through ``approved``).
ALLOWED_TRANSITIONS: dict[FlashStatus, set[FlashStatus]] = {
    FlashStatus.candidate: {FlashStatus.draft, FlashStatus.rejected},
    FlashStatus.draft: {FlashStatus.approved, FlashStatus.rejected},
    FlashStatus.approved: {FlashStatus.sent, FlashStatus.rejected},
    FlashStatus.sent: set(),
    FlashStatus.rejected: set(),
}


class InvalidTransition(Exception):
    """Raised when an illegal Flash status transition is attempted."""


# ---------------------------------------------------------------------------
# Pure Pydantic value objects (serialised to JSON columns on Flash)
# ---------------------------------------------------------------------------

class Quote(BaseModel):
    """A single market quote snapshot. ``change`` / ``asof`` are optional so an
    early candidate can carry just a level."""

    level: float
    change: Optional[float] = None
    asof: Optional[datetime] = None


class HistoryPoint(BaseModel):
    """One point in a price time-series (used for chart generation)."""

    t: datetime
    value: float


class Event(BaseModel):
    """An economic-calendar entry or news item that triggered a flash."""

    # Common fields
    source: str          # "calendar" | "news"
    title: str
    currency: Optional[str] = None
    country: Optional[str] = None
    impact: Optional[str] = None
    event_time: Optional[datetime] = None

    # Calendar extras
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

    # News extras
    headline: Optional[str] = None
    url: Optional[str] = None
    summary: Optional[str] = None

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
    """Persistent flash row — one potential client communication.

    Lifecycle: candidate -> draft -> approved -> sent (or -> rejected).
    """

    __tablename__ = "flash"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Stored as the string value; validated in __init__ (SQLModel table models
    # skip field validation, so the explicit check below is required).
    status: str = Field(default=FlashStatus.candidate.value)

    # Structured payloads stored as JSON; use the typed accessors below.
    event: dict = Field(default_factory=dict, sa_column=Column(JSON))
    market_context: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # Mirrors Event.dedup_key; unique so duplicate candidates are rejected at
    # the DB layer.
    dedup_key: str = Field(index=True, unique=True)

    draft_text: Optional[str] = None
    subject: Optional[str] = None
    edited_text: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(_UTCDateTime, nullable=False),
    )
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = Field(
        default=None, sa_column=Column(_UTCDateTime, nullable=True)
    )
    sent_at: Optional[datetime] = Field(
        default=None, sa_column=Column(_UTCDateTime, nullable=True)
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.status is not None:
            FlashStatus(self.status)  # raises ValueError for unrecognised values

    # ------------------------------------------------------------------
    # Typed accessors for JSON columns
    # ------------------------------------------------------------------

    def get_event(self) -> Event:
        """Return the ``event`` column deserialised as an :class:`Event`."""
        return Event.model_validate(self.event or {})

    def set_event(self, e: Event) -> None:
        """Serialise *e* and store it in the ``event`` column."""
        self.event = e.model_dump(mode="json")

    def get_market_context(self) -> MarketContext:
        """Return ``market_context`` as a :class:`MarketContext`.

        Tolerates ``None``/empty (early candidates may have no context) by
        returning an empty :class:`MarketContext`.
        """
        if not self.market_context:
            return MarketContext()
        return MarketContext.model_validate(self.market_context)

    def set_market_context(self, mc: MarketContext) -> None:
        """Serialise *mc* and store it in the ``market_context`` column."""
        self.market_context = mc.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Status-transition guard (compliance safety invariant)
    # ------------------------------------------------------------------

    def advance_to(self, new_status: FlashStatus) -> None:
        """Move this flash to *new_status*, enforcing the legal lifecycle.

        Raises :class:`InvalidTransition` if the move is not permitted by
        :data:`ALLOWED_TRANSITIONS` (notably ``draft -> sent`` is forbidden).
        Callers set any accompanying timestamps / ``approved_by`` themselves.
        """
        current = FlashStatus(self.status)
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransition(
                f"Cannot transition Flash from {current!r} to {new_status!r}. "
                f"Allowed: {ALLOWED_TRANSITIONS[current]}"
            )
        self.status = new_status


class Client(SQLModel, table=True):
    """A client who receives flash emails."""

    __tablename__ = "client"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str
    name: Optional[str] = None
    segment: Optional[str] = None
    lang: str = Field(default="fr")
    active: bool = Field(default=True)
