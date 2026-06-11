"""Tests for the non-UI logic in news_flashes.review.logic.

All tests are pure Python — no Streamlit import, no network, no database.
"""

from __future__ import annotations

import pytest

from news_flashes.models.schema import Flash, FlashStatus
from news_flashes.review.logic import actionable_statuses, apply_approval, can_approve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draft_flash(draft_text: str = "Original draft text.") -> Flash:
    """Return a Flash in DRAFT status with the given draft_text."""
    flash = Flash(
        dedup_key="test:review:1",
        subject="Test Flash FX",
        draft_text=draft_text,
    )
    flash.advance_to(FlashStatus.DRAFT)
    return flash


# ---------------------------------------------------------------------------
# actionable_statuses
# ---------------------------------------------------------------------------


class TestActionableStatuses:
    def test_contains_candidate_draft_approved(self):
        statuses = actionable_statuses()
        assert FlashStatus.CANDIDATE in statuses
        assert FlashStatus.DRAFT in statuses
        assert FlashStatus.APPROVED in statuses

    def test_excludes_sent_and_rejected(self):
        statuses = actionable_statuses()
        assert FlashStatus.SENT not in statuses
        assert FlashStatus.REJECTED not in statuses


# ---------------------------------------------------------------------------
# can_approve — rejection cases
# ---------------------------------------------------------------------------


class TestCanApproveRejections:
    def test_rejects_non_draft_candidate(self):
        flash = Flash(dedup_key="test:review:candidate", draft_text="Some text.")
        # status is CANDIDATE by default
        assert flash.status == FlashStatus.CANDIDATE

        ok, reason = can_approve(flash, "Edited text here.", "Analyst A")
        assert ok is False
        assert reason is not None
        assert "DRAFT" in reason or "draft" in reason.lower()

    def test_rejects_non_draft_approved(self):
        flash = _draft_flash()
        flash.advance_to(FlashStatus.APPROVED)

        ok, reason = can_approve(flash, "Edited text here.", "Analyst A")
        assert ok is False
        assert reason is not None

    def test_rejects_non_draft_rejected(self):
        flash = _draft_flash()
        flash.advance_to(FlashStatus.REJECTED)

        ok, reason = can_approve(flash, "Edited text here.", "Analyst A")
        assert ok is False
        assert reason is not None

    def test_rejects_unchanged_text_exact_match(self):
        original = "Original draft text."
        flash = _draft_flash(draft_text=original)

        # Pass exactly the same text — must be blocked
        ok, reason = can_approve(flash, original, "Analyst B")
        assert ok is False
        assert reason is not None
        assert "éditer" in reason.lower() or "edit" in reason.lower() or "identique" in reason.lower()

    def test_rejects_unchanged_text_whitespace_only_diff(self):
        original = "Original draft text."
        flash = _draft_flash(draft_text=original)

        # Only whitespace difference — should still be blocked (strip comparison)
        ok, reason = can_approve(flash, "  " + original + "  ", "Analyst B")
        assert ok is False
        assert reason is not None

    def test_rejects_empty_approver(self):
        flash = _draft_flash()
        ok, reason = can_approve(flash, "Edited and changed text.", "")
        assert ok is False
        assert reason is not None

    def test_rejects_whitespace_only_approver(self):
        flash = _draft_flash()
        ok, reason = can_approve(flash, "Edited and changed text.", "   ")
        assert ok is False
        assert reason is not None


# ---------------------------------------------------------------------------
# can_approve — acceptance case
# ---------------------------------------------------------------------------


class TestCanApproveAccepts:
    def test_accepts_edited_text_with_approver(self):
        flash = _draft_flash(draft_text="Original text for approval test.")
        edited = "Edited and meaningfully changed text for approval."

        ok, reason = can_approve(flash, edited, "Jean-Pierre")
        assert ok is True
        assert reason is None

    def test_accepts_minimal_edit(self):
        flash = _draft_flash(draft_text="Hello world.")
        ok, reason = can_approve(flash, "Hello world!", "JP")
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# apply_approval — happy path
# ---------------------------------------------------------------------------


class TestApplyApproval:
    def test_sets_edited_text(self):
        flash = _draft_flash(draft_text="Draft text here.")
        apply_approval(flash, "Edited text here.", "New Subject", "Analyst C")
        assert flash.edited_text == "Edited text here."

    def test_sets_subject(self):
        flash = _draft_flash()
        apply_approval(flash, "Edited text.", "My New Subject", "Analyst C")
        assert flash.subject == "My New Subject"

    def test_sets_approved_by(self):
        flash = _draft_flash()
        apply_approval(flash, "Edited text.", "Subject", "  Marie  ")
        assert flash.approved_by == "Marie"

    def test_sets_approved_at_not_none(self):
        flash = _draft_flash()
        assert flash.approved_at is None
        apply_approval(flash, "Edited text.", "Subject", "Analyst D")
        assert flash.approved_at is not None

    def test_approved_at_is_utc(self):
        from datetime import timezone
        flash = _draft_flash()
        apply_approval(flash, "Edited text.", "Subject", "Analyst E")
        assert flash.approved_at.tzinfo is not None
        assert flash.approved_at.tzinfo == timezone.utc

    def test_status_becomes_approved(self):
        flash = _draft_flash()
        assert flash.status == FlashStatus.DRAFT
        apply_approval(flash, "Edited text.", "Subject", "Analyst F")
        assert flash.status == FlashStatus.APPROVED


# ---------------------------------------------------------------------------
# apply_approval — compliance gate: unchanged text must raise
# ---------------------------------------------------------------------------


class TestApplyApprovalRefusesUnchangedText:
    def test_raises_on_unchanged_text(self):
        original = "Original draft."
        flash = _draft_flash(draft_text=original)

        with pytest.raises((ValueError, Exception)) as exc_info:
            apply_approval(flash, original, "Subject", "Analyst G")

        # Status must remain DRAFT — no partial state change
        assert flash.status == FlashStatus.DRAFT
        assert flash.approved_by is None
        assert flash.approved_at is None

    def test_raises_on_empty_approver(self):
        flash = _draft_flash()
        with pytest.raises((ValueError, Exception)):
            apply_approval(flash, "Edited text.", "Subject", "")
        assert flash.status == FlashStatus.DRAFT
