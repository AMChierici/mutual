"""Tests for the root + health endpoints (also exercises app wiring)."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from api.db import Base
from api.main import run_migrations


async def test_health_returns_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_root_returns_metadata(client):
    r = await client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Mutual"
    assert body["version"] == "0.1.0"


def test_run_migrations_brings_fresh_db_to_head(tmp_path):
    db_file = tmp_path / "fresh.sqlite"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    try:
        run_migrations(engine)
        actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert actual == set(Base.metadata.tables.keys())
    finally:
        engine.dispose()


def test_run_migrations_is_idempotent(tmp_path):
    db_file = tmp_path / "idempotent.sqlite"
    engine = create_engine(f"sqlite:///{db_file}", future=True)
    try:
        run_migrations(engine)
        run_migrations(engine)  # must not raise
        with engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert head is not None
    finally:
        engine.dispose()
