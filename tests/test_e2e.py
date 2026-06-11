"""End-to-end Phase-1 walking-skeleton test.

Exercises the full pipeline:
    CANDIDATE → (generate) → DRAFT → (approve) → APPROVED → (send) → SENT

Constraints:
- NO network calls — Anthropic client is a fake stub.
- NO real project DB file touched — uses an in-memory SQLite engine.
- Covers the critical safety assertion: a DRAFT flash must never reach SENT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from news_flashes.models.schema import (
    Client,
    Event,
    Flash,
    FlashStatus,
    HistoryPoint,
    MarketContext,
    Quote,
)
from news_flashes.generation.generator import generate_draft
from news_flashes.review.logic import apply_approval
from news_flashes.delivery.sender import StubSender, send_flash
from news_flashes.delivery.render import render_email


# ---------------------------------------------------------------------------
# Fake Anthropic client (same pattern as test_generator.py)
# ---------------------------------------------------------------------------

FAKE_DRAFT_BODY = (
    "Bonsoir chers clients,\n\n"
    "L'IPC américain est ressorti à **3,3 %**, sous les attentes (3,4 %).\n\n"
    "**1. Niveaux techniques à surveiller**\n"
    "DXY : 104,20. Support : 103,80. Résistance : 104,80.\n\n"
    "**2. Contexte de marché**\n"
    "La Fed pourrait assouplir sa politique monétaire plus tôt que prévu.\n\n"
    "**3. Impact sur les cotations TND**\n"
    "Pression baissière attendue sur USD/TND à court terme.\n\n"
    "**Synthèse**\n"
    "Le dollar reste sous pression à court terme après un CPI inférieur aux attentes.\n\n"
    "Voir graphique ci-dessous.\n\n"
    "Bien cordialement,\nLa Desk FX"
)


def _make_fake_client(text: str = FAKE_DRAFT_BODY):
    """Minimal stub that mimics anthropic.Anthropic.messages.create."""
    content_block = SimpleNamespace(text=text, type="text")
    fake_message = SimpleNamespace(content=[content_block])

    class FakeMessages:
        def create(self, **kwargs):  # noqa: ANN001
            return fake_message

    class FakeClient:
        messages = FakeMessages()

    return FakeClient()


# ---------------------------------------------------------------------------
# Shared DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    """Yield a fresh in-memory SQLite session per test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Seed data helpers  (mirrors seed_candidate.py shape)
# ---------------------------------------------------------------------------


def _make_history(symbol: str, start_value: float, drift: float) -> list[HistoryPoint]:
    """Generate a 30-point deterministic daily series."""
    base = datetime(2025, 5, 12, tzinfo=timezone.utc)
    return [
        HistoryPoint(t=base + timedelta(days=i), value=round(start_value + i * drift, 4))
        for i in range(30)
    ]


def _build_candidate_flash() -> Flash:
    """Return a realistic CANDIDATE Flash (US CPI scenario, seed_candidate shape)."""
    asof = datetime(2025, 6, 11, 13, 0, tzinfo=timezone.utc)
    event = Event(
        source="calendar",
        title="US CPI (YoY)",
        currency="USD",
        country="US",
        impact="High",
        event_time=datetime(2025, 6, 11, 12, 30, tzinfo=timezone.utc),
        actual="3.3%",
        forecast="3.4%",
        previous="3.5%",
        dedup_key="e2e:US_CPI_YoY:2025-06-11",
    )
    quotes = {
        "DXY":    Quote(level=104.20, change=-0.35, asof=asof),
        "USDJPY": Quote(level=157.30, change=-0.82, asof=asof),
        "EURUSD": Quote(level=1.0730, change=+0.0041, asof=asof),
    }
    history = {
        "DXY":    _make_history("DXY", start_value=106.10, drift=-0.063),
        "USDJPY": _make_history("USDJPY", start_value=160.50, drift=-0.107),
        "EURUSD": _make_history("EURUSD", start_value=1.0520, drift=0.0007),
    }
    mc = MarketContext(quotes=quotes, history=history)
    flash = Flash(status=FlashStatus.CANDIDATE, dedup_key=event.dedup_key)
    flash.set_event(event)
    flash.set_market_context(mc)
    return flash


def _make_clients(n: int = 2) -> list[Client]:
    return [
        Client(email=f"analyst{i}@bank.com", name=f"Analyst {i}", segment="vip")
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# E2E: happy path
# ---------------------------------------------------------------------------


class TestPhase1WalkingSkeleton:
    """Full pipeline end-to-end with no network."""

    # --- Step 1: Generate ---------------------------------------------------

    def test_generate_advances_to_draft(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        assert flash.status == FlashStatus.DRAFT

    def test_generate_sets_draft_text(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        assert flash.draft_text == FAKE_DRAFT_BODY

    def test_generate_sets_non_empty_subject(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        assert flash.subject
        assert "US CPI (YoY)" in flash.subject

    # --- Step 2: Approve ----------------------------------------------------

    def test_approve_advances_to_approved(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        edited = FAKE_DRAFT_BODY + "\n[édité par l'analyste]"
        apply_approval(flash, edited, "Flash FX — US CPI | Approved", "Marie")

        assert flash.status == FlashStatus.APPROVED

    def test_approve_sets_approved_by(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        edited = FAKE_DRAFT_BODY + "\n[mark de l'analyste]"
        apply_approval(flash, edited, "Subject", "Jean-Pierre")

        assert flash.approved_by == "Jean-Pierre"

    def test_approve_sets_approved_at(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        edited = FAKE_DRAFT_BODY + "\n[modifié]"
        apply_approval(flash, edited, "Subject", "Analyst X")

        assert flash.approved_at is not None
        assert flash.approved_at.tzinfo == timezone.utc

    def test_approve_sets_edited_text(self):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        edited = FAKE_DRAFT_BODY + "\n[edit marker unique]"
        apply_approval(flash, edited, "Subject", "Analyst Y")

        assert flash.edited_text == edited

    # --- Step 3: Send -------------------------------------------------------

    def test_send_returns_correct_count(self, tmp_path):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [edit]", "Subject", "Analyst A")

        clients = _make_clients(2)
        sender = StubSender(outdir=tmp_path / "outbox")
        result = send_flash(flash, clients, sender)

        assert result == 2

    def test_send_creates_two_html_files(self, tmp_path):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [unique edit]", "Subject", "Analyst B")

        clients = _make_clients(2)
        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, clients, sender)

        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 2

    def test_send_advances_to_sent(self, tmp_path):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [send-test edit]", "Subject", "Analyst C")

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(2), sender)

        assert flash.status == FlashStatus.SENT

    def test_send_sets_sent_at(self, tmp_path):
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [sent-at edit]", "Subject", "Analyst D")

        sender = StubSender(outdir=tmp_path / "outbox")
        assert flash.sent_at is None
        send_flash(flash, _make_clients(2), sender)
        assert flash.sent_at is not None
        assert flash.sent_at.tzinfo == timezone.utc

    # --- Step 3b: DB persistence --------------------------------------------

    def test_send_persists_to_db(self, session, tmp_path):
        """When a session is passed, the SENT flash is committed to the DB."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [db-persist]", "Subject", "Analyst E")

        session.add(flash)
        session.commit()
        session.refresh(flash)

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(2), sender, session=session)

        fetched = session.get(Flash, flash.id)
        assert fetched is not None
        assert fetched.status == FlashStatus.SENT
        assert fetched.sent_at is not None

    # --- Step 4: HTML content assertions ------------------------------------

    def test_rendered_html_contains_disclaimer_body(self, tmp_path):
        """The outbox HTML must contain legal disclaimer text."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [disclaimer-check]", "Subject", "Analyst F")

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(1), sender)

        html_file = next((tmp_path / "outbox").glob("*.html"))
        html = html_file.read_text(encoding="utf-8")

        # Must contain disclaimer text (one of these key fragments)
        assert "informatif" in html or "instrument financier" in html

    def test_rendered_html_contains_chart_data_uri(self, tmp_path):
        """The outbox HTML must include an embedded PNG chart (base64 data URI)."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [chart-check]", "Subject", "Analyst G")

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(1), sender)

        html_file = next((tmp_path / "outbox").glob("*.html"))
        html = html_file.read_text(encoding="utf-8")

        assert "data:image/png;base64," in html

    def test_rendered_html_contains_edited_body(self, tmp_path):
        """The outbox HTML must include the analyst's edit marker."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        unique_edit_marker = "[ANALYST_EDIT_MARKER_UNIQUE_12345]"
        edited = FAKE_DRAFT_BODY + f"\n{unique_edit_marker}"
        apply_approval(flash, edited, "Subject", "Analyst H")

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(1), sender)

        html_file = next((tmp_path / "outbox").glob("*.html"))
        html = html_file.read_text(encoding="utf-8")

        assert unique_edit_marker in html

    def test_rendered_html_does_not_contain_brouillon_comment(self, tmp_path):
        """Internal # BROUILLON compliance note must NOT appear in sent HTML."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [brouillon-check]", "Subject", "Analyst I")

        sender = StubSender(outdir=tmp_path / "outbox")
        send_flash(flash, _make_clients(1), sender)

        html_file = next((tmp_path / "outbox").glob("*.html"))
        html = html_file.read_text(encoding="utf-8")

        assert "BROUILLON" not in html
        for line in html.splitlines():
            assert not line.strip().startswith("# BROUILLON"), (
                f"Internal comment leaked into HTML: {line!r}"
            )

    def test_render_email_direct_has_chart_and_disclaimer(self):
        """Call render_email() directly on an APPROVED flash and verify key elements."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        apply_approval(flash, FAKE_DRAFT_BODY + " [direct-render]", "Subject", "Analyst J")

        html = render_email(flash)
        assert "data:image/png;base64," in html
        assert "informatif" in html or "instrument financier" in html
        assert "BROUILLON" not in html


# ---------------------------------------------------------------------------
# CRITICAL SAFETY ASSERTION: No DRAFT → SENT path end-to-end
# ---------------------------------------------------------------------------


class TestDraftCannotBeSent:
    """End-to-end safety gate: a DRAFT flash must never reach SENT."""

    def test_send_flash_raises_for_draft_status(self, tmp_path):
        """send_flash() must raise ValueError when flash is DRAFT."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())
        assert flash.status == FlashStatus.DRAFT

        clients = _make_clients(2)
        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError, match="APPROVED"):
            send_flash(flash, clients, sender)

    def test_draft_send_writes_no_files(self, tmp_path):
        """No HTML files must be written when the safety gate fires on DRAFT."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError):
            send_flash(flash, _make_clients(2), sender)

        files = list((tmp_path / "outbox").glob("*.html"))
        assert len(files) == 0

    def test_draft_send_leaves_status_draft(self, tmp_path):
        """The flash status must remain DRAFT after a failed send attempt."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError):
            send_flash(flash, _make_clients(2), sender)

        assert flash.status == FlashStatus.DRAFT

    def test_draft_send_records_nothing_in_sender(self, tmp_path):
        """The sender.sent list must be empty after a failed DRAFT send."""
        flash = _build_candidate_flash()
        generate_draft(flash, client=_make_fake_client())

        sender = StubSender(outdir=tmp_path / "outbox")

        with pytest.raises(ValueError):
            send_flash(flash, _make_clients(2), sender)

        assert len(sender.sent) == 0
