"""Tests for delivery/clients.py and delivery/sender.py.

All tests use an in-memory SQLite engine — no files on disk, no shared state
with the project's production database.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from news_flashes.models.schema import Client, Flash, FlashStatus, InvalidTransition
from news_flashes.delivery.clients import import_clients_from_csv, load_clients
from news_flashes.delivery.sender import StubSender, send_flash


# ---------------------------------------------------------------------------
# Shared in-memory DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    """Yield a fresh in-memory SQLite session per test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[str]) -> Path:
    """Write a CSV file with a header row followed by *rows*."""
    content = "\n".join(rows) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def _make_approved_flash() -> Flash:
    """Return a Flash in APPROVED state via the legal transition path."""
    flash = Flash(
        dedup_key="test:delivery:1",
        subject="Test Flash FX",
        draft_text="Corps du flash de test.",
    )
    # candidate → draft → approved  (the only legal path to APPROVED)
    flash.advance_to(FlashStatus.DRAFT)
    flash.advance_to(FlashStatus.APPROVED)
    return flash


def _make_clients(n: int = 3) -> list[Client]:
    """Return *n* in-memory Client objects (not persisted)."""
    return [
        Client(email=f"client{i}@example.com", name=f"Client {i}", segment="default")
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# import_clients_from_csv + load_clients
# ---------------------------------------------------------------------------


class TestImportClientsFromCsv:
    def test_basic_insert(self, session, tmp_path):
        csv_file = _write_csv(
            tmp_path / "clients.csv",
            [
                "email,name,segment,lang,active",
                "alice@example.com,Alice,vip,fr,true",
                "bob@example.com,Bob,default,en,false",
            ],
        )
        count = import_clients_from_csv(session, csv_file)
        assert count == 2

        clients = session.exec(select(Client)).all()
        assert len(clients) == 2

    def test_load_clients_active_only(self, session, tmp_path):
        csv_file = _write_csv(
            tmp_path / "clients.csv",
            [
                "email,name,segment,lang,active",
                "alice@example.com,Alice,default,fr,true",
                "inactive@example.com,Inactive,default,fr,false",
            ],
        )
        import_clients_from_csv(session, csv_file)

        active = load_clients(session, active_only=True)
        assert len(active) == 1
        assert active[0].email == "alice@example.com"

    def test_load_clients_all(self, session, tmp_path):
        csv_file = _write_csv(
            tmp_path / "clients.csv",
            [
                "email,name,segment,lang,active",
                "alice@example.com,Alice,default,fr,true",
                "inactive@example.com,Inactive,default,fr,false",
            ],
        )
        import_clients_from_csv(session, csv_file)

        all_clients = load_clients(session, active_only=False)
        assert len(all_clients) == 2

    def test_segment_filter(self, session, tmp_path):
        csv_file = _write_csv(
            tmp_path / "clients.csv",
            [
                "email,name,segment,lang,active",
                "vip1@example.com,VIP 1,vip,fr,true",
                "std1@example.com,Std 1,default,fr,true",
                "vip2@example.com,VIP 2,vip,fr,true",
            ],
        )
        import_clients_from_csv(session, csv_file)

        vips = load_clients(session, active_only=True, segment="vip")
        assert len(vips) == 2
        assert all(c.segment == "vip" for c in vips)

        defaults = load_clients(session, active_only=True, segment="default")
        assert len(defaults) == 1

    def test_upsert_updates_existing_email(self, session, tmp_path):
        """Re-importing the same email must update, not duplicate."""
        csv1 = _write_csv(
            tmp_path / "v1.csv",
            [
                "email,name,segment,lang,active",
                "alice@example.com,Alice Original,default,fr,true",
            ],
        )
        import_clients_from_csv(session, csv1)

        csv2 = _write_csv(
            tmp_path / "v2.csv",
            [
                "email,name,segment,lang,active",
                "alice@example.com,Alice Updated,vip,en,false",
            ],
        )
        count = import_clients_from_csv(session, csv2)
        assert count == 1

        # Only one row in the DB
        all_clients = session.exec(select(Client)).all()
        assert len(all_clients) == 1

        alice = all_clients[0]
        assert alice.name == "Alice Updated"
        assert alice.segment == "vip"
        assert alice.lang == "en"
        assert alice.active is False

    def test_optional_columns_default_correctly(self, session, tmp_path):
        """CSV with only email column: defaults kick in."""
        csv_file = _write_csv(
            tmp_path / "minimal.csv",
            [
                "email",
                "minimal@example.com",
            ],
        )
        import_clients_from_csv(session, csv_file)

        client = session.exec(select(Client)).first()
        assert client is not None
        assert client.email == "minimal@example.com"
        assert client.name is None
        assert client.segment == "default"
        assert client.lang == "fr"
        assert client.active is True

    def test_active_parsed_leniently(self, session, tmp_path):
        """Various truthy/falsy strings for active."""
        csv_file = _write_csv(
            tmp_path / "active.csv",
            [
                "email,active",
                "a@example.com,yes",
                "b@example.com,1",
                "c@example.com,no",
                "d@example.com,0",
                "e@example.com,True",
                "f@example.com,False",
            ],
        )
        import_clients_from_csv(session, csv_file)

        clients = {
            c.email: c.active
            for c in session.exec(select(Client)).all()
        }
        assert clients["a@example.com"] is True
        assert clients["b@example.com"] is True
        assert clients["c@example.com"] is False
        assert clients["d@example.com"] is False
        assert clients["e@example.com"] is True
        assert clients["f@example.com"] is False

    def test_whitespace_stripped(self, session, tmp_path):
        """Leading/trailing whitespace in email and fields should be stripped."""
        csv_file = _write_csv(
            tmp_path / "ws.csv",
            [
                "email,name,segment",
                "  spaced@example.com  , Spaced Name , vip ",
            ],
        )
        import_clients_from_csv(session, csv_file)

        client = session.exec(select(Client)).first()
        assert client is not None
        assert client.email == "spaced@example.com"
        assert client.name == "Spaced Name"
        assert client.segment == "vip"

    def test_returns_correct_count(self, session, tmp_path):
        csv_file = _write_csv(
            tmp_path / "count.csv",
            [
                "email,name",
                "a@example.com,A",
                "b@example.com,B",
                "c@example.com,C",
            ],
        )
        count = import_clients_from_csv(session, csv_file)
        assert count == 3


# ---------------------------------------------------------------------------
# StubSender
# ---------------------------------------------------------------------------


class TestStubSender:
    def test_send_writes_html_file(self, tmp_path):
        sender = StubSender(outdir=tmp_path / "outbox")
        sender.send(
            to_email="test@example.com",
            to_name="Test User",
            subject="Test Subject",
            html="<html><body>Test</body></html>",
        )
        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "<html><body>Test</body></html>" == content

    def test_send_records_in_sent_list(self, tmp_path):
        sender = StubSender(outdir=tmp_path / "outbox")
        sender.send(
            to_email="test@example.com",
            to_name="Test User",
            subject="My Subject",
            html="<p>hello</p>",
        )
        assert len(sender.sent) == 1
        record = sender.sent[0]
        assert record["to_email"] == "test@example.com"
        assert record["to_name"] == "Test User"
        assert record["subject"] == "My Subject"
        assert "path" in record

    def test_multiple_sends_accumulate(self, tmp_path):
        sender = StubSender(outdir=tmp_path / "outbox")
        for i in range(4):
            sender.send(
                to_email=f"user{i}@example.com",
                to_name=f"User {i}",
                subject="Subject",
                html=f"<p>body {i}</p>",
            )
        assert len(sender.sent) == 4
        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 4

    def test_outdir_created_automatically(self, tmp_path):
        new_dir = tmp_path / "nested" / "outbox"
        assert not new_dir.exists()
        sender = StubSender(outdir=new_dir)
        assert new_dir.exists()

    def test_filename_contains_sanitized_email(self, tmp_path):
        sender = StubSender(outdir=tmp_path / "outbox")
        sender.send(
            to_email="user+tag@my-domain.com",
            to_name=None,
            subject="S",
            html="<p>x</p>",
        )
        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 1
        # The filename should not contain the raw @, +, or - characters
        name = files[0].name
        assert "@" not in name
        assert "+" not in name


# ---------------------------------------------------------------------------
# send_flash
# ---------------------------------------------------------------------------


class TestSendFlash:
    def test_approved_flash_sends_to_all_clients(self, tmp_path):
        flash = _make_approved_flash()
        clients = _make_clients(3)
        sender = StubSender(outdir=tmp_path / "outbox")

        result = send_flash(flash, clients, sender)

        assert result == 3
        assert len(sender.sent) == 3

    def test_approved_flash_writes_html_files(self, tmp_path):
        flash = _make_approved_flash()
        clients = _make_clients(2)
        sender = StubSender(outdir=tmp_path / "outbox")

        send_flash(flash, clients, sender)

        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 2

    def test_flash_status_becomes_sent(self, tmp_path):
        flash = _make_approved_flash()
        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        send_flash(flash, clients, sender)

        assert flash.status == FlashStatus.SENT

    def test_flash_sent_at_is_set(self, tmp_path):
        flash = _make_approved_flash()
        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        assert flash.sent_at is None
        send_flash(flash, clients, sender)
        assert flash.sent_at is not None

    def test_flash_sent_at_is_utc(self, tmp_path):
        flash = _make_approved_flash()
        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(1), sender)
        assert flash.sent_at.tzinfo is not None
        assert flash.sent_at.tzinfo == timezone.utc

    def test_session_persists_flash(self, session, tmp_path):
        """When a session is passed, the flash is committed to the database."""
        flash = _make_approved_flash()
        session.add(flash)
        session.commit()
        session.refresh(flash)

        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        send_flash(flash, clients, sender, session=session)

        # Re-fetch from DB
        fetched = session.get(Flash, flash.id)
        assert fetched is not None
        assert fetched.status == FlashStatus.SENT
        assert fetched.sent_at is not None

    # ---- SAFETY GATE TESTS --------------------------------------------------

    def test_draft_flash_raises_value_error(self, tmp_path):
        """CRITICAL: A DRAFT flash must never be sent."""
        flash = Flash(
            dedup_key="test:safety:draft",
            subject="Draft Flash",
            draft_text="Draft body.",
        )
        flash.advance_to(FlashStatus.DRAFT)
        assert flash.status == FlashStatus.DRAFT

        clients = _make_clients(2)
        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError, match="APPROVED"):
            send_flash(flash, clients, sender)

        # Nothing should have been sent
        assert len(sender.sent) == 0
        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 0
        # Status must be unchanged
        assert flash.status == FlashStatus.DRAFT

    def test_candidate_flash_raises_value_error(self, tmp_path):
        """CRITICAL: A CANDIDATE flash must never be sent."""
        flash = Flash(
            dedup_key="test:safety:candidate",
            subject="Candidate Flash",
        )
        assert flash.status == FlashStatus.CANDIDATE

        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError, match="APPROVED"):
            send_flash(flash, _make_clients(1), sender)

        assert len(sender.sent) == 0
        assert flash.status == FlashStatus.CANDIDATE

    def test_rejected_flash_raises_value_error(self, tmp_path):
        """CRITICAL: A REJECTED flash must never be sent."""
        flash = Flash(dedup_key="test:safety:rejected")
        flash.advance_to(FlashStatus.DRAFT)
        flash.advance_to(FlashStatus.REJECTED)

        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError, match="APPROVED"):
            send_flash(flash, _make_clients(1), sender)

        assert len(sender.sent) == 0
        assert flash.status == FlashStatus.REJECTED

    def test_sent_flash_raises_value_error(self, tmp_path):
        """CRITICAL: Cannot re-send an already-SENT flash."""
        flash = _make_approved_flash()
        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        # First send succeeds
        send_flash(flash, clients, sender)
        assert flash.status == FlashStatus.SENT

        # Second send must be refused by the APPROVED gate
        with pytest.raises(ValueError, match="APPROVED"):
            send_flash(flash, clients, sender)

    def test_no_direct_path_from_draft_to_sent(self):
        """Verify the transition graph itself forbids draft -> sent."""
        flash = Flash(dedup_key="test:graph:draft_to_sent")
        flash.advance_to(FlashStatus.DRAFT)

        with pytest.raises(InvalidTransition):
            flash.advance_to(FlashStatus.SENT)

    def test_empty_client_list_returns_zero(self, tmp_path):
        """Sending to no clients still advances status and returns 0."""
        flash = _make_approved_flash()
        sender = StubSender(outdir=tmp_path / "outbox")

        result = send_flash(flash, [], sender)

        assert result == 0
        assert flash.status == FlashStatus.SENT
        assert len(sender.sent) == 0

    def test_subject_used_in_send_call(self, tmp_path):
        flash = _make_approved_flash()
        flash.subject = "Custom Subject Line"
        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        send_flash(flash, clients, sender)

        assert sender.sent[0]["subject"] == "Custom Subject Line"

    def test_default_subject_when_none(self, tmp_path):
        flash = _make_approved_flash()
        flash.subject = None
        clients = _make_clients(1)
        sender = StubSender(outdir=tmp_path / "outbox")

        send_flash(flash, clients, sender)

        assert sender.sent[0]["subject"] == "Flash FX"
