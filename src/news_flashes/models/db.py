"""Database engine, session factory, and table initialisation."""

from contextlib import contextmanager
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from news_flashes.config import settings

# Import schema so SQLModel.metadata knows about all tables before create_all.
import news_flashes.models.schema as _schema  # noqa: F401

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new :class:`~sqlmodel.Session` bound to the shared engine.

    Callers should use it as a context manager::

        with get_session() as session:
            session.add(flash)
            session.commit()
    """
    return Session(engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager that commits on success and rolls back on error."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
