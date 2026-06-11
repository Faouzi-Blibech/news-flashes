"""Email sending interface and stub implementation for the news-flashes pipeline.

Architecture note — plugging in a real provider
------------------------------------------------
``EmailSender`` is a ``typing.Protocol``.  To add Brevo, SES, or any other
provider, create a class that implements ``send(*, to_email, to_name, subject,
html) -> None`` and pass an instance of it to :func:`send_flash`.  No changes
to this module are needed.

Example skeleton for a future Brevo implementation::

    class BrevoSender:
        def __init__(self, api_key: str) -> None:
            self._client = sib_api_v3_sdk.TransactionalEmailsApi(...)

        def send(self, *, to_email, to_name, subject, html) -> None:
            ...  # call Brevo transactional API
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from news_flashes.models.schema import Client, Flash, FlashStatus
from news_flashes.delivery.render import render_email

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public protocol — the clean seam for provider injection
# ---------------------------------------------------------------------------


class EmailSender(Protocol):
    """Minimal interface every email-sending backend must satisfy."""

    def send(
        self,
        *,
        to_email: str,
        to_name: str | None,
        subject: str,
        html: str,
    ) -> None:
        """Send one HTML email.

        Parameters
        ----------
        to_email:
            Recipient email address.
        to_name:
            Recipient display name (may be ``None``).
        subject:
            Email subject line.
        html:
            Full HTML document to use as the email body.
        """
        ...


# ---------------------------------------------------------------------------
# Stub implementation (no network; writes HTML to disk for inspection)
# ---------------------------------------------------------------------------


def _sanitize_for_filename(value: str) -> str:
    """Replace non-alphanumeric characters with underscores for safe filenames."""
    return re.sub(r"[^a-zA-Z0-9]", "_", value)


class StubSender:
    """A no-network ``EmailSender`` that writes rendered HTML to disk.

    Useful for development, testing, and QA review of rendered emails.

    Each call to :meth:`send` writes the HTML to
    ``<outdir>/<sanitized_email>-<timestamp>.html`` and appends a record to
    :attr:`sent`.

    Parameters
    ----------
    outdir:
        Directory where HTML files are written.  Created automatically if it
        does not exist.  Defaults to ``"outbox"`` relative to the current
        working directory.
    """

    def __init__(self, outdir: str | Path = "outbox") -> None:
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.sent: list[dict] = []

    def send(
        self,
        *,
        to_email: str,
        to_name: str | None,
        subject: str,
        html: str,
    ) -> None:
        """Write *html* to a file in :attr:`outdir` and record the send.

        The filename is ``<sanitized_email>-<ISO-timestamp>.html``.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe_email = _sanitize_for_filename(to_email)
        filename = f"{safe_email}-{timestamp}.html"
        path = self.outdir / filename

        path.write_text(html, encoding="utf-8")

        record = {
            "to_email": to_email,
            "to_name": to_name,
            "subject": subject,
            "path": str(path),
        }
        self.sent.append(record)

        logger.info(
            "StubSender: email written to %s (to=%s, subject=%r)",
            path,
            to_email,
            subject,
        )


# ---------------------------------------------------------------------------
# Orchestration: send a flash to a list of clients
# ---------------------------------------------------------------------------


def send_flash(
    flash: Flash,
    clients: list[Client],
    sender: EmailSender,
    session=None,
) -> int:
    """Send an approved flash to every client in *clients*.

    Compliance safety gate
    ----------------------
    This function **refuses to send** unless ``flash.status`` is
    :attr:`~FlashStatus.APPROVED`.  A ``ValueError`` is raised immediately if
    the flash is in any other state.  After all emails are sent, the flash is
    advanced to SENT via :meth:`~Flash.advance_to` (which enforces the same
    invariant at the model layer), and ``flash.sent_at`` is set.

    Order of operations
    -------------------
    1. Verify ``flash.status == APPROVED`` (raise ``ValueError`` if not).
    2. Render the HTML once via :func:`~news_flashes.delivery.render.render_email`.
    3. Call ``sender.send(...)`` for each client; count successes.
    4. ``flash.advance_to(FlashStatus.SENT)`` + set ``flash.sent_at``.
    5. Optionally persist: if *session* is provided, ``session.add(flash)``
       and ``session.commit()``.  If *session* is ``None``, the flash object is
       mutated in-memory and the caller is responsible for persistence.

    Parameters
    ----------
    flash:
        The flash to send.  Must be in APPROVED state.
    clients:
        List of :class:`~news_flashes.models.schema.Client` objects to send to.
    sender:
        An :class:`EmailSender` implementation.
    session:
        Optional ``sqlmodel.Session``.  When provided, the updated flash is
        committed to the database.

    Returns
    -------
    int
        Number of emails successfully sent.

    Raises
    ------
    ValueError
        If ``flash.status`` is not ``APPROVED``.
    """
    # ---- SAFETY GATE: explicit pre-condition check (compliance invariant) ----
    if flash.status != FlashStatus.APPROVED:
        raise ValueError(
            f"Flash must be APPROVED before sending; got {flash.status!r}. "
            "Transition path: candidate -> draft -> approved -> sent."
        )

    # ---- Render once --------------------------------------------------------
    html = render_email(flash)

    # ---- Send to all clients ------------------------------------------------
    subject = flash.subject or "Flash FX"
    sent_count = 0
    for client in clients:
        sender.send(
            to_email=client.email,
            to_name=client.name,
            subject=subject,
            html=html,
        )
        sent_count += 1

    # ---- Advance status (also enforced by advance_to's own guard) -----------
    flash.advance_to(FlashStatus.SENT)
    flash.sent_at = datetime.now(timezone.utc)

    # ---- Optional persistence -----------------------------------------------
    if session is not None:
        session.add(flash)
        session.commit()

    return sent_count
