"""Database engine + session helpers (SQLite by default)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import settings as settings_mod
from .models import Application, Base, Event

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - driver hook
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_engine(url: str | None = None) -> Engine:
    global _engine, _Session
    if _engine is not None and url is None:
        return _engine
    st = settings_mod.load()
    st.ensure_dirs()
    resolved = url or f"sqlite:///{st.db_path}"
    engine = create_engine(resolved, future=True, echo=False)
    if resolved.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    if url is None:
        _engine = engine
        _Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def init_db(url: str | None = None) -> Engine:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def session_factory() -> sessionmaker[Session]:
    global _Session
    if _Session is None:
        init_db()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on error."""
    sess = session_factory()()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def reset_for_tests(url: str) -> Engine:
    """Point the module at a throwaway database (used by the test suite)."""
    global _engine, _Session
    _engine = create_engine(url, future=True, echo=False)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    return _engine


def log_event(
    sess: Session,
    application: Application | int | None,
    kind: str,
    message: str,
    **data: Any,
) -> Event:
    """Append to the audit trail. Safe to call with an id or an instance."""
    app_id = application.id if isinstance(application, Application) else application
    ev = Event(
        application_id=app_id,
        ts=datetime.now(UTC),
        kind=kind,
        message=message,
        data=data or {},
    )
    sess.add(ev)
    return ev
