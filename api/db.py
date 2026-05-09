"""Database engine, session factory, and declarative base.

SQLite-first per docs/architecture.md. Path is read from MUTUAL_DB_PATH; if
unset, defaults to ``<repo>/data/db/mutual.sqlite``. Foreign-key enforcement
is enabled on every connection.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


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
