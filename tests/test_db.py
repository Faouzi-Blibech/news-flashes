"""Tests for DB initialisation and CRUD round-trips via SQLite."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import create_engine, Session, select

from news_flashes.models.schema import Client, Event, Flash, FlashStatus, MarketContext, Quote
from news_flashes.models.db import init_db, get_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_engine():
    """In-memory SQLite engine, schema created fresh for each test."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine=eng)
    return eng


@pytest.fixture()
def sample_event() -> dict:
    ev = Event(
        source="calendar",
        title="US Non-Farm Payrolls",
        currency="USD",
        country="US",
        impact="High",
        event_time=datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc),
        actual="272K",
        forecast="185K",
        previous="165K",
        dedup_key="calendar:USD:NFP:2026-06-06",
    )
    return ev.model_dump(mode="json")


@pytest.fixture()
def sample_market_context() -> dict:
    mc = MarketContext(
        quotes={
            "DXY": Quote(level=104.5, change=-0.3),
            "EURUSD": Quote(level=1.085, change=0.002),
            "USDJPY": Quote(level=157.2, change=0.5),
        }
    )
    return mc.model_dump(mode="json")


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_flash_table(self, tmp_engine):
        """After init_db the flash table must exist and accept inserts."""
        with Session(tmp_engine) as session:
            flash = Flash(
                event={"source": "calendar", "title": "test", "dedup_key": "k"},
                dedup_key="k",
            )
            session.add(flash)
            session.commit()
            session.refresh(flash)
            assert flash.id is not None

    def test_creates_client_table(self, tmp_engine):
        """After init_db the client table must exist and accept inserts."""
        with Session(tmp_engine) as session:
            client = Client(email="a@b.com", name="Test User")
            session.add(client)
            session.commit()
            session.refresh(client)
            assert client.id is not None


# ---------------------------------------------------------------------------
# Flash round-trip
# ---------------------------------------------------------------------------

class TestFlashRoundTrip:
    def test_insert_and_read_back(self, tmp_engine, sample_event, sample_market_context):
        """A Flash row written with event+market_context JSON must read back intact."""
        with Session(tmp_engine) as session:
            flash = Flash(
                event=sample_event,
                market_context=sample_market_context,
                dedup_key=sample_event["dedup_key"],
            )
            session.add(flash)
            session.commit()
            row_id = flash.id

        with Session(tmp_engine) as session:
            row = session.get(Flash, row_id)
            assert row is not None
            assert row.status == FlashStatus.candidate.value
            assert row.event["title"] == "US Non-Farm Payrolls"
            assert row.event["impact"] == "High"
            assert row.market_context["quotes"]["DXY"]["level"] == pytest.approx(104.5)

    def test_status_transitions(self, tmp_engine, sample_event):
        """Status field must be mutable (simulates the lifecycle)."""
        with Session(tmp_engine) as session:
            flash = Flash(event=sample_event, dedup_key=sample_event["dedup_key"])
            session.add(flash)
            session.commit()
            row_id = flash.id

        for next_status in (
            FlashStatus.draft,
            FlashStatus.approved,
            FlashStatus.sent,
        ):
            with Session(tmp_engine) as session:
                row = session.get(Flash, row_id)
                row.status = next_status.value
                session.add(row)
                session.commit()

            with Session(tmp_engine) as session:
                row = session.get(Flash, row_id)
                assert row.status == next_status.value

    def test_dedup_key_indexed(self, tmp_engine, sample_event):
        """Querying by dedup_key returns the correct row."""
        with Session(tmp_engine) as session:
            flash = Flash(event=sample_event, dedup_key=sample_event["dedup_key"])
            session.add(flash)
            session.commit()

        with Session(tmp_engine) as session:
            results = session.exec(
                select(Flash).where(Flash.dedup_key == sample_event["dedup_key"])
            ).all()
            assert len(results) == 1
            assert results[0].event["title"] == "US Non-Farm Payrolls"

    def test_duplicate_dedup_key_raises_integrity_error(self, tmp_engine, sample_event):
        """Inserting two Flash rows with the same dedup_key must raise IntegrityError."""
        with Session(tmp_engine) as session:
            session.add(Flash(event=sample_event, dedup_key=sample_event["dedup_key"]))
            session.commit()

        with pytest.raises(IntegrityError):
            with Session(tmp_engine) as session:
                session.add(Flash(event=sample_event, dedup_key=sample_event["dedup_key"]))
                session.commit()

    def test_created_at_tzinfo_preserved(self, tmp_engine, sample_event):
        """created_at read back from DB must be timezone-aware (tzinfo not None)."""
        with Session(tmp_engine) as session:
            flash = Flash(event=sample_event, dedup_key=sample_event["dedup_key"])
            session.add(flash)
            session.commit()
            row_id = flash.id

        with Session(tmp_engine) as session:
            row = session.get(Flash, row_id)
            assert row is not None
            assert row.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Client round-trip
# ---------------------------------------------------------------------------

class TestClientRoundTrip:
    def test_defaults_persist(self, tmp_engine):
        """Client.lang defaults to 'fr' and active to True after DB round-trip."""
        with Session(tmp_engine) as session:
            client = Client(email="default@example.com")
            session.add(client)
            session.commit()
            row_id = client.id

        with Session(tmp_engine) as session:
            row = session.get(Client, row_id)
            assert row is not None
            assert row.lang == "fr"
            assert row.active is True
            assert row.name is None
            assert row.segment is None

    def test_full_client_persists(self, tmp_engine):
        with Session(tmp_engine) as session:
            client = Client(
                email="faouzi@bank.tn",
                name="Faouzi Blibech",
                segment="corporate",
                lang="fr",
                active=True,
            )
            session.add(client)
            session.commit()
            row_id = client.id

        with Session(tmp_engine) as session:
            row = session.get(Client, row_id)
            assert row.email == "faouzi@bank.tn"
            assert row.name == "Faouzi Blibech"
            assert row.segment == "corporate"


# ---------------------------------------------------------------------------
# get_session context manager
# ---------------------------------------------------------------------------

class TestGetSession:
    def test_get_session_yields_session(self, tmp_engine):
        """get_session() must yield a Session that can execute queries."""
        with get_session(engine=tmp_engine) as session:
            assert isinstance(session, Session)
            flash = Flash(
                event={"dedup_key": "ctx-test"},
                dedup_key="ctx-test",
            )
            session.add(flash)
            session.commit()
            session.refresh(flash)
            assert flash.id is not None
