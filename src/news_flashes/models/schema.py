"""Shared data contract.

Person A (ingestion/triggers) writes rows; Person B (generation/delivery/review)
reads them.  Neither imports the other's modules — this file is the only bridge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

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
# Status enumeration
# ---------------------------------------------------------------------------

class FlashStatus(str, Enum):
    """Valid lifecycle states for a Flash row."""

    candidate = "candidate"
    draft = "draft"
    approved = "approved"
    sent = "sent"
    rejected = "rejected"


# ---------------------------------------------------------------------------
# Pure-Pydantic value objects (not DB tables)
# ---------------------------------------------------------------------------

class Quote(SQLModel):
    """A single FX instrument snapshot."""

    level: float
    change: Optional[float] = None
    asof: Optional[datetime] = None


class MarketContext(SQLModel):
    """Live FX levels attached to a Flash at the moment of candidate creation.

    Keys are instrument names, e.g. "DXY", "USDJPY", "EURUSD", "USDTND".
    """

    quotes: dict[str, Quote] = {}


class Event(SQLModel):
    """A market-moving event from either the economic calendar or a news feed.

    Serialised into Flash.event (JSON column) — not a DB table itself.
    """

    # Common fields
    source: Literal["calendar", "news"]
    title: str
    currency: Optional[str] = None
    country: Optional[str] = None
    impact: Optional[str] = None          # "High" | "Medium" | "Low" | None
    event_time: Optional[datetime] = None

    # Calendar-specific extras
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

    # News-specific extras
    headline: Optional[str] = None
    url: Optional[str] = None
    summary: Optional[str] = None

    # Deduplication key (e.g. "<source>:<currency>:<title>:<date>")
    dedup_key: str


# ---------------------------------------------------------------------------
# DB tables
# ---------------------------------------------------------------------------

class Flash(SQLModel, table=True):
    """One news-flash row.  Lifecycle: candidate → draft → approved → sent."""

    __tablename__ = "flash"

    id: Optional[int] = Field(default=None, primary_key=True)

    status: str = Field(default=FlashStatus.candidate.value)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if "status" in kwargs:
            FlashStatus(self.status)  # raises ValueError for unrecognised values

    # Serialised Event dict
    event: dict = Field(sa_column=Column(JSON, nullable=False))

    # Serialised MarketContext dict (may be absent for very early candidates)
    market_context: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # Dedup key mirrors Event.dedup_key for fast DB-side lookups
    dedup_key: str = Field(index=True, unique=True)

    # AI-generated draft
    draft_text: Optional[str] = None
    subject: Optional[str] = None

    # Analyst-edited text (mandatory before approval)
    edited_text: Optional[str] = None

    # Timestamps
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


class Client(SQLModel, table=True):
    """A recipient on the distribution list."""

    __tablename__ = "client"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str
    name: Optional[str] = None
    segment: Optional[str] = None
    lang: str = Field(default="fr")
    active: bool = Field(default=True)
