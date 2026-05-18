"""multi-account: users, memberships rename, pool slug, pool_id on vote/payout

Revision ID: 5a7e1c2b9d3f
Revises: 92cf60c38d82
Create Date: 2026-05-17 00:00:00.000000+00:00

This migration is the data-shape half of M1 (multi-pool-per-user).

What it does:
  * Adds the global ``users`` table (one row per real person, keyed by email).
  * Backfills one ``User`` per distinct ``members.email``; rows with no email
    get a synthetic ``user+{member_id}@local.invalid`` placeholder so the
    NOT NULL/UNIQUE constraint can hold.
  * Adds ``pools.slug`` (UNIQUE NOT NULL) so multi-pool URLs become possible.
  * Renames ``members`` → ``memberships`` (the per-pool role for a User),
    adds ``user_id`` FK, drops the now-redundant ``email`` column, and adds
    a UNIQUE(user_id, pool_id) constraint.
  * Switches ``login_tokens`` and ``auth_sessions`` from ``member_id`` to
    ``user_id`` so a single login can serve a user across pools.
  * Adds explicit ``pool_id`` columns to ``votes`` and ``payouts`` (backfilled
    from the Claim → Pool join) so future platform-admin / cross-pool queries
    don't need a 3-table join.
"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5a7e1c2b9d3f"
down_revision: Union[str, None] = "92cf60c38d82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SYNTHETIC_EMAIL_DOMAIN = "local.invalid"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "pool"


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. users table
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    # ------------------------------------------------------------------
    # 2. pools.slug — add nullable, backfill, then make NOT NULL UNIQUE
    # ------------------------------------------------------------------
    with op.batch_alter_table("pools", schema=None) as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(), nullable=True))

    pools = bind.execute(sa.text("SELECT id, name FROM pools ORDER BY id")).fetchall()
    used_slugs: set[str] = set()
    for row in pools:
        base = _slugify(row.name)
        slug = base
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        bind.execute(
            sa.text("UPDATE pools SET slug = :s WHERE id = :id"),
            {"s": slug, "id": row.id},
        )

    with op.batch_alter_table("pools", schema=None) as batch_op:
        batch_op.alter_column("slug", existing_type=sa.String(), nullable=False)
        batch_op.create_index(batch_op.f("ix_pools_slug"), ["slug"], unique=True)

    # ------------------------------------------------------------------
    # 3. backfill users from members.email (synthetic for nulls)
    # ------------------------------------------------------------------
    members = bind.execute(
        sa.text("SELECT id, display_name, email FROM members ORDER BY id")
    ).fetchall()

    email_to_user_id: dict[str, int] = {}
    member_email_map: dict[int, str] = {}
    for m in members:
        email = m.email or f"user+{m.id}@{SYNTHETIC_EMAIL_DOMAIN}"
        member_email_map[m.id] = email
        if email not in email_to_user_id:
            res = bind.execute(
                sa.text(
                    "INSERT INTO users (email, display_name, is_platform_admin, created_at) "
                    "VALUES (:email, :name, 0, CURRENT_TIMESTAMP)"
                ),
                {"email": email, "name": m.display_name},
            )
            email_to_user_id[email] = int(res.lastrowid)

    # ------------------------------------------------------------------
    # 4. members.user_id — add nullable, backfill, then NOT NULL +
    #    drop members.email, add UNIQUE(user_id, pool_id)
    # ------------------------------------------------------------------
    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_members_user_id", "users", ["user_id"], ["id"])

    for m_id, email in member_email_map.items():
        bind.execute(
            sa.text("UPDATE members SET user_id = :uid WHERE id = :id"),
            {"uid": email_to_user_id[email], "id": m_id},
        )

    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("email")
        batch_op.create_unique_constraint(
            "uq_membership_user_pool", ["user_id", "pool_id"]
        )
        batch_op.create_index("ix_memberships_user_id", ["user_id"])
        batch_op.create_index("ix_memberships_pool_id", ["pool_id"])

    # ------------------------------------------------------------------
    # 5. Rename members → memberships. Modern SQLite (>=3.26, default
    #    legacy_alter_table=OFF) updates FK references in other tables.
    # ------------------------------------------------------------------
    op.rename_table("members", "memberships")

    # ------------------------------------------------------------------
    # 6. votes.pool_id and payouts.pool_id — backfill via Claim join
    # ------------------------------------------------------------------
    with op.batch_alter_table("votes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pool_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE votes SET pool_id = "
            "(SELECT pool_id FROM claims WHERE claims.id = votes.claim_id)"
        )
    )
    with op.batch_alter_table("votes", schema=None) as batch_op:
        batch_op.alter_column("pool_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key("fk_votes_pool_id", "pools", ["pool_id"], ["id"])
        batch_op.create_index("ix_votes_pool_id", ["pool_id"])

    with op.batch_alter_table("payouts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pool_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE payouts SET pool_id = "
            "(SELECT pool_id FROM claims WHERE claims.id = payouts.claim_id)"
        )
    )
    with op.batch_alter_table("payouts", schema=None) as batch_op:
        batch_op.alter_column("pool_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key("fk_payouts_pool_id", "pools", ["pool_id"], ["id"])
        batch_op.create_index("ix_payouts_pool_id", ["pool_id"])

    # ------------------------------------------------------------------
    # 7. Auth tables — member_id → user_id
    # ------------------------------------------------------------------
    mem_user = {
        row.id: row.user_id
        for row in bind.execute(sa.text("SELECT id, user_id FROM memberships")).fetchall()
    }

    for table in ("login_tokens", "auth_sessions"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        for m_id, u_id in mem_user.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE member_id = :mid"),
                {"uid": u_id, "mid": m_id},
            )
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                f"fk_{table}_user_id", "users", ["user_id"], ["id"]
            )
            batch_op.create_index(f"ix_{table}_user_id", ["user_id"])
            batch_op.drop_column("member_id")


def downgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Auth tables: user_id → member_id. Pick one membership per user
    # (only one existed in v0 single-pool installs).
    # ------------------------------------------------------------------
    user_mem = {
        row.user_id: row.id
        for row in bind.execute(sa.text("SELECT id, user_id FROM memberships")).fetchall()
    }
    for table in ("auth_sessions", "login_tokens"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("member_id", sa.Integer(), nullable=True))
        for u_id, m_id in user_mem.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET member_id = :mid WHERE user_id = :uid"),
                {"mid": m_id, "uid": u_id},
            )
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("member_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                f"fk_{table}_member_id", "memberships", ["member_id"], ["id"]
            )
            batch_op.drop_index(f"ix_{table}_user_id")
            batch_op.drop_column("user_id")

    # votes / payouts: drop pool_id
    for table in ("payouts", "votes"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(f"ix_{table}_pool_id")
            batch_op.drop_column("pool_id")

    # Rename memberships → members
    op.rename_table("memberships", "members")

    # Restore members.email from users.email (skip synthetic)
    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(), nullable=True))
        batch_op.drop_constraint("uq_membership_user_pool", type_="unique")
        batch_op.drop_index("ix_memberships_user_id")
        batch_op.drop_index("ix_memberships_pool_id")

    bind.execute(
        sa.text(
            "UPDATE members SET email = ("
            "  SELECT email FROM users WHERE users.id = members.user_id"
            ") WHERE EXISTS ("
            "  SELECT 1 FROM users WHERE users.id = members.user_id"
            "    AND users.email NOT LIKE :synth"
            ")"
        ),
        {"synth": f"%@{SYNTHETIC_EMAIL_DOMAIN}"},
    )

    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.drop_column("user_id")

    # Drop pools.slug
    with op.batch_alter_table("pools", schema=None) as batch_op:
        batch_op.drop_index("ix_pools_slug")
        batch_op.drop_column("slug")

    # Drop users
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("ix_users_email")
    op.drop_table("users")
