"""Tests for engine / session factory / configuration."""
from __future__ import annotations

from sqlalchemy import text

from api.db import (
    Base,
    get_database_url,
    make_engine,
    make_session_factory,
)


def test_default_database_url_is_local_sqlite(monkeypatch):
    monkeypatch.delenv("MUTUAL_DB_PATH", raising=False)
    url = get_database_url()
    assert url.startswith("sqlite:///")
    assert url.endswith("mutual.sqlite")


def test_database_url_honors_env_var(monkeypatch, tmp_path):
    db_file = tmp_path / "x.sqlite"
    monkeypatch.setenv("MUTUAL_DB_PATH", str(db_file))
    assert get_database_url() == f"sqlite:///{db_file}"


def test_make_engine_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("MUTUAL_DB_PATH", str(tmp_path / "t.sqlite"))
    engine = make_engine()
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_make_engine_enables_foreign_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MUTUAL_DB_PATH", str(tmp_path / "t.sqlite"))
    engine = make_engine()
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_make_engine_creates_parent_dir(tmp_path, monkeypatch):
    db_file = tmp_path / "nested" / "deeper" / "t.sqlite"
    monkeypatch.setenv("MUTUAL_DB_PATH", str(db_file))
    engine = make_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        assert db_file.parent.exists()
    finally:
        engine.dispose()


def test_session_factory_yields_working_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MUTUAL_DB_PATH", str(tmp_path / "t.sqlite"))
    engine = make_engine()
    try:
        Base.metadata.create_all(engine)
        factory = make_session_factory(engine)
        with factory() as s:
            assert s.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()
