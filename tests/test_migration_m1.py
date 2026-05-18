"""Tests for the M1 multi-account migration (5a7e1c2b9d3f).

Verifies that an existing single-pool install (anything stamped at the
previous head) upgrades cleanly: pool gets a slug, members get a user,
auth tokens flip from member_id to user_id, votes/payouts get a pool_id,
and downgrade restores the original shape.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_M1 = "92cf60c38d82"
M1 = "5a7e1c2b9d3f"


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _seed_pre_m1(db_path: Path) -> dict:
    """Populate a pre-M1 schema with one pool, three members (one with a
    null email), a login token, an auth session, and a claim that already
    has a vote + payout. Returns the inserted ids for later assertions."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    c = conn.cursor()
    c.execute(
        "INSERT INTO pools (name, currency, created_at, governance_config) "
        "VALUES ('Acme Pool', 'USD', '2026-01-01T00:00:00Z', '{}')"
    )
    pid = c.lastrowid
    members = []
    for name, email, role, status in [
        ("Alice", "alice@example.com", "admin", "active"),
        ("Bob", None, "member", "invited"),
        ("Carol", "carol@example.com", "member", "active"),
    ]:
        c.execute(
            "INSERT INTO members (pool_id, display_name, email, joined_at, status, role) "
            "VALUES (?, ?, ?, '2026-01-01T00:00:00Z', ?, ?)",
            (pid, name, email, status, role),
        )
        members.append(c.lastrowid)
    c.execute(
        "INSERT INTO login_tokens (member_id, token, created_at, expires_at) "
        "VALUES (?, 'tok_bob', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')",
        (members[1],),
    )
    c.execute(
        "INSERT INTO auth_sessions (member_id, token, created_at, expires_at, last_seen_at) "
        "VALUES (?, 'sess_alice', '2026-01-01T00:00:00Z', '2026-02-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')",
        (members[0],),
    )
    c.execute(
        "INSERT INTO claims (pool_id, member_id, amount_requested, category, description, "
        "evidence_uris, occurred_at, submitted_at, status) "
        "VALUES (?, ?, 1000, 'misc', 'thing', '[]', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', 'paid')",
        (pid, members[0]),
    )
    cid = c.lastrowid
    c.execute(
        "INSERT INTO votes (claim_id, member_id, decision, cast_at) "
        "VALUES (?, ?, 'approve', '2026-01-01T00:00:00Z')",
        (cid, members[1]),
    )
    c.execute(
        "INSERT INTO payouts (claim_id, amount_paid, paid_at, recorded_by) "
        "VALUES (?, 1000, '2026-01-01T00:00:00Z', ?)",
        (cid, members[0]),
    )
    conn.commit()
    conn.close()
    return {"pool_id": pid, "members": members, "claim_id": cid}


def test_m1_upgrade_backfills_users_and_memberships(tmp_path):
    db_file = tmp_path / "m1.sqlite"
    cfg = _alembic_config(f"sqlite:///{db_file}")
    command.upgrade(cfg, PRE_M1)
    ids = _seed_pre_m1(db_file)

    command.upgrade(cfg, M1)

    conn = sqlite3.connect(db_file)
    c = conn.cursor()

    # Pool got a slug derived from its name.
    slug = c.execute("SELECT slug FROM pools WHERE id = ?", (ids["pool_id"],)).fetchone()[0]
    assert slug == "acme-pool"

    # One user per distinct member email; Bob got a synthetic email.
    emails = {row[0] for row in c.execute("SELECT email FROM users").fetchall()}
    assert "alice@example.com" in emails
    assert "carol@example.com" in emails
    assert any(e.endswith("@local.invalid") for e in emails)

    # memberships.user_id is set for every row.
    nulls = c.execute(
        "SELECT COUNT(*) FROM memberships WHERE user_id IS NULL"
    ).fetchone()[0]
    assert nulls == 0

    # The old members table is gone.
    tables = {
        r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memberships" in tables
    assert "members" not in tables

    # Bob's login token was rewired to his user.
    bob_user_id = c.execute(
        "SELECT user_id FROM memberships WHERE display_name = 'Bob'"
    ).fetchone()[0]
    assert c.execute(
        "SELECT user_id FROM login_tokens WHERE token = 'tok_bob'"
    ).fetchone()[0] == bob_user_id

    # Alice's auth session was rewired to her user.
    alice_user_id = c.execute(
        "SELECT user_id FROM memberships WHERE display_name = 'Alice'"
    ).fetchone()[0]
    assert c.execute(
        "SELECT user_id FROM auth_sessions WHERE token = 'sess_alice'"
    ).fetchone()[0] == alice_user_id

    # votes / payouts got their pool_id backfilled.
    assert c.execute("SELECT pool_id FROM votes").fetchone()[0] == ids["pool_id"]
    assert c.execute("SELECT pool_id FROM payouts").fetchone()[0] == ids["pool_id"]

    # member_id is gone from auth tables.
    cols = {row[1] for row in c.execute("PRAGMA table_info(login_tokens)").fetchall()}
    assert "member_id" not in cols and "user_id" in cols
    cols = {row[1] for row in c.execute("PRAGMA table_info(auth_sessions)").fetchall()}
    assert "member_id" not in cols and "user_id" in cols
    conn.close()


def test_m1_downgrade_restores_pre_m1_shape(tmp_path):
    db_file = tmp_path / "m1.sqlite"
    cfg = _alembic_config(f"sqlite:///{db_file}")
    command.upgrade(cfg, PRE_M1)
    _seed_pre_m1(db_file)
    command.upgrade(cfg, M1)

    command.downgrade(cfg, PRE_M1)

    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    tables = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "members" in tables
    assert "users" not in tables
    assert "memberships" not in tables

    cols = {row[1] for row in c.execute("PRAGMA table_info(members)").fetchall()}
    assert "email" in cols
    assert "user_id" not in cols

    cols = {row[1] for row in c.execute("PRAGMA table_info(pools)").fetchall()}
    assert "slug" not in cols

    cols = {row[1] for row in c.execute("PRAGMA table_info(votes)").fetchall()}
    assert "pool_id" not in cols
    cols = {row[1] for row in c.execute("PRAGMA table_info(payouts)").fetchall()}
    assert "pool_id" not in cols

    # Bob's email is restored to NULL (synthetic emails not carried over).
    bob_email = c.execute(
        "SELECT email FROM members WHERE display_name = 'Bob'"
    ).fetchone()[0]
    assert bob_email is None
    # Alice's auth session is back on member_id.
    alice_member_id = c.execute(
        "SELECT id FROM members WHERE display_name = 'Alice'"
    ).fetchone()[0]
    assert c.execute(
        "SELECT member_id FROM auth_sessions WHERE token = 'sess_alice'"
    ).fetchone()[0] == alice_member_id
    conn.close()
