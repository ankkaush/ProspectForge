"""Database engine and session management.

Uses SQLAlchemy as a separate persistence layer from the Step 4 pydantic
models. This is a deliberate split, not duplication for its own sake:
pydantic models (prospectforge/models/) are the domain contracts pipeline
code reads and writes; SQLAlchemy models (app/orm.py) are what actually gets
stored as rows. Keeping them separate means the business logic doesn't need
to know anything about SQL, and the storage schema doesn't need to be
identical in shape to the objects business logic works with. For Step 5
only Run and ExternalCallAttempt have real read/write logic wired up
(that's all this step needs); the rest of the schema exists so the tables
are there when steps 7+ start using them.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def reset_db_cache() -> None:
    """Test-only: forces a new engine/sessionmaker on the next call, so
    tests can point at a fresh database URL."""

    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency - yields a session, closes it after the request."""

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-manager form for non-request code (the CLI, background
    processing) - commits on success, rolls back on exception."""

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
