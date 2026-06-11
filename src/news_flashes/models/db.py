"""SQLite engine, session factory, and DB initialisation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from news_flashes.config import get_settings


def _make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    # connect_args is only needed for SQLite to allow multi-threaded access
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


# Module-level engine — created once at import time using the configured URL.
# Tests override this by calling init_db(engine=...) with a temp engine.
engine = _make_engine()


def init_db(engine=None) -> None:
    """Create all tables that are not yet present.

    Pass an explicit *engine* to target a different database (e.g. tests).
    """
    import news_flashes.models.db as _self  # local import avoids shadowing the param
    target = engine or _self.engine
    SQLModel.metadata.create_all(target)


@contextmanager
def get_session(engine=None) -> Generator[Session, None, None]:
    """Yield a SQLModel Session and close it afterwards.

    Usage::

        with get_session() as session:
            session.add(flash)
            session.commit()
    """
    import news_flashes.models.db as _self  # local import avoids shadowing the param
    target = engine or _self.engine
    with Session(target) as session:
        yield session
