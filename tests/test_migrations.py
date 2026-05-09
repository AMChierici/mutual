"""Verify Alembic migrations produce the same schema as Base.metadata."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from api.db import Base

REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_alembic_upgrade_head_creates_all_orm_tables(tmp_path):
    db_file = tmp_path / "m.sqlite"
    db_url = f"sqlite:///{db_file}"
    command.upgrade(_alembic_config(db_url), "head")

    engine = create_engine(db_url, future=True)
    try:
        actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert actual == set(Base.metadata.tables.keys())
    finally:
        engine.dispose()


def test_alembic_downgrade_to_base_drops_orm_tables(tmp_path):
    db_file = tmp_path / "m.sqlite"
    db_url = f"sqlite:///{db_file}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(db_url, future=True)
    try:
        actual = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert actual == set()
    finally:
        engine.dispose()
