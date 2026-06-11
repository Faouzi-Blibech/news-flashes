"""Client-list loading and CSV import for the news-flashes delivery pipeline.

This module is intentionally session-injected: every public function accepts a
``sqlmodel.Session`` from the caller so it can be tested against an in-memory
SQLite database without touching the production file.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import select

from news_flashes.models.schema import Client

if TYPE_CHECKING:
    from sqlmodel import Session


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_clients(
    session: "Session",
    *,
    active_only: bool = True,
    segment: str | None = None,
) -> list[Client]:
    """Return clients from the database, optionally filtered.

    Parameters
    ----------
    session:
        An open ``sqlmodel.Session``.
    active_only:
        When ``True`` (the default), only clients with ``active == True`` are
        returned.
    segment:
        If given, restrict results to clients in that segment.

    Returns
    -------
    list[Client]
        Matching clients, ordered by database insertion order (id).
    """
    stmt = select(Client)
    if active_only:
        stmt = stmt.where(Client.active == True)  # noqa: E712 — SQLAlchemy needs ==
    if segment is not None:
        stmt = stmt.where(Client.segment == segment)
    return list(session.exec(stmt).all())


def import_clients_from_csv(session: "Session", csv_path: str | Path) -> int:
    """Import or update clients from a CSV file.

    Expected header (all columns after ``email`` are optional)::

        email,name,segment,lang,active

    Column defaults when absent:

    * ``name``    → ``None``
    * ``segment`` → ``"default"``
    * ``lang``    → ``"fr"``
    * ``active``  → ``True``

    ``active`` is parsed leniently: ``"true"``, ``"1"``, ``"yes"`` → ``True``;
    ``"false"``, ``"0"``, ``"no"`` → ``False``; missing or unrecognised → ``True``.

    Rows are *upserted* by ``email``: existing clients are updated in-place;
    new emails are inserted.

    Parameters
    ----------
    session:
        An open ``sqlmodel.Session``.  The caller is responsible for the
        session's lifecycle; this function calls ``session.commit()`` once at
        the end.
    csv_path:
        Path to the CSV file.

    Returns
    -------
    int
        Number of rows imported or updated.
    """
    csv_path = Path(csv_path)

    _TRUTHY = {"true", "1", "yes"}
    _FALSY = {"false", "0", "no"}

    def _parse_active(raw: str | None) -> bool:
        if raw is None:
            return True
        normalised = raw.strip().lower()
        if normalised in _TRUTHY:
            return True
        if normalised in _FALSY:
            return False
        return True  # unrecognised → default True

    count = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Strip whitespace from all values
            row = {k.strip(): (v.strip() if v is not None else v) for k, v in row.items()}

            email = row.get("email", "").strip()
            if not email:
                continue  # skip rows without an email address

            name = row.get("name") or None  # empty string → None
            segment = row.get("segment") or "default"
            lang = row.get("lang") or "fr"
            active = _parse_active(row.get("active"))

            # Upsert: look up by email
            existing = session.exec(
                select(Client).where(Client.email == email)
            ).first()

            if existing is not None:
                existing.name = name
                existing.segment = segment
                existing.lang = lang
                existing.active = active
                session.add(existing)
            else:
                client = Client(
                    email=email,
                    name=name,
                    segment=segment,
                    lang=lang,
                    active=active,
                )
                session.add(client)

            count += 1

    session.commit()
    return count
