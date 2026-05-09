"""Database engine, session factory, and declarative base.

SQLite-first per docs/architecture.md. Path is read from MUTUAL_DB_PATH; if
unset, defaults to ``<repo>/data/db/mutual.sqlite``. Foreign-key enforcement
is enabled on every connection.
"""
from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """DateTime that always reads/writes timezone-aware UTC datetimes.

    SQLite (the default backend) stores datetimes as ISO strings without
    timezone metadata, so a tz-aware ``datetime`` written to the DB comes
    back naive on the next load. This decorator re-attaches UTC tzinfo on
    load and rejects naive datetimes on insert.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime is not allowed: {value!r}")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models in this app."""


def _default_db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "db" / "mutual.sqlite"


def get_database_url() -> str:
    env = os.environ.get("MUTUAL_DB_PATH")
    path = Path(env) if env else _default_db_path()
    return f"sqlite:///{path}"


def make_engine(url: str | None = None) -> Engine:
    if url is None:
        url = get_database_url()

    if url.startswith("sqlite:///"):
        db_path = Path(url.removeprefix("sqlite:///"))
        if db_path.parts and not str(db_path).startswith(":memory:"):
            db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _conn_rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False, future=True)
