"""Pure business-logic helpers for the review UI.

These functions contain no Streamlit imports, so they can be unit-tested
directly without launching a Streamlit server.
"""

from __future__ import annotations

from datetime import datetime, timezone

from news_flashes.models.schema import Flash, FlashStatus


# ---------------------------------------------------------------------------
# Default actionable statuses for the sidebar filter
# ---------------------------------------------------------------------------

def actionable_statuses() -> set[FlashStatus]:
    """Return the default set of statuses the analyst wants to see."""
    return {FlashStatus.CANDIDATE, FlashStatus.DRAFT, FlashStatus.APPROVED}


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------

def can_approve(
    flash: Flash,
    edited_text: str,
    approver: str,
) -> tuple[bool, str | None]:
    """Check whether an approval action may proceed.

    Parameters
    ----------
    flash:
        The Flash about to be approved (must be fetched fresh from DB).
    edited_text:
        The text currently in the editor text-area.
    approver:
        The name / initials entered by the analyst.

    Returns
    -------
    tuple[bool, str | None]
        ``(True, None)`` when approval is permitted.
        ``(False, reason)`` when it should be blocked, where *reason* is a
        user-visible French message explaining why.
    """
    if flash.status != FlashStatus.DRAFT:
        return False, (
            f"Le flash doit être en statut DRAFT pour être approuvé "
            f"(statut actuel : {FlashStatus(flash.status).value})."
        )

    if not approver or not approver.strip():
        return False, "Le champ « Approuvé par » est obligatoire."

    # Compliance mandatory-edit rule: the analyst MUST have changed the text.
    original = flash.draft_text or ""
    if edited_text.strip() == original.strip():
        return False, (
            "Vous devez éditer le texte avant d'approuver. "
            "Le contenu ne peut pas être identique au brouillon généré."
        )

    return True, None


# ---------------------------------------------------------------------------
# Apply approval
# ---------------------------------------------------------------------------

def apply_approval(
    flash: Flash,
    edited_text: str,
    edited_subject: str,
    approver: str,
) -> None:
    """Mutate *flash* in-place to record an approval.

    Sets ``edited_text``, ``subject``, ``approved_by``, ``approved_at``, and
    advances the status to APPROVED via :meth:`Flash.advance_to`.

    The caller is responsible for committing the session.

    Raises
    ------
    ValueError
        If :func:`can_approve` returns ``False`` (compliance gate).
    """
    ok, reason = can_approve(flash, edited_text, approver)
    if not ok:
        raise ValueError(reason)

    flash.edited_text = edited_text
    flash.subject = edited_subject or flash.subject
    flash.approved_by = approver.strip()
    flash.approved_at = datetime.now(timezone.utc)
    flash.advance_to(FlashStatus.APPROVED)
