"""SQLAlchemy ORM models — schema mirrors docs/architecture.md.

Money fields are stored as integers in the smallest currency unit (cents),
which keeps arithmetic exact across the ledger. Enums are stored as VARCHAR
via SQLAlchemy's non-native ``Enum`` so the schema stays portable to Postgres
later without migration churn.

LedgerEntry and AuditEvent are append-only by convention; the database does
not enforce that — the application layer must.

Identity model (M1): ``User`` is the global identity (one row per real
person, keyed by email). ``Membership`` is the per-pool role for a user —
one user can have many memberships (one per pool they belong to). The
class was called ``Member`` in v0 when one install meant one pool.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base, UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Synthetic email sentinel for legacy members that had no email in v0.
# A row with this email is treated as "no real email" — display logic
# should hide it.
SYNTHETIC_EMAIL_DOMAIN = "local.invalid"


def is_synthetic_email(email: str | None) -> bool:
    return bool(email) and email.endswith(f"@{SYNTHETIC_EMAIL_DOMAIN}")


# ---------------------------------------------------------------------------
# Enums (closed sets — open-ended kinds like AuditEvent.kind stay String)
# ---------------------------------------------------------------------------
class MemberRole(enum.Enum):
    member = "member"
    admin = "admin"
    observer = "observer"


class MemberStatus(enum.Enum):
    active = "active"
    invited = "invited"
    inactive = "inactive"


class ClaimStatus(enum.Enum):
    submitted = "submitted"
    voting = "voting"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"
    withdrawn = "withdrawn"


class VoteDecision(enum.Enum):
    approve = "approve"
    reject = "reject"
    abstain = "abstain"


class LedgerKind(enum.Enum):
    contribution = "contribution"
    payout = "payout"
    opening_balance = "opening_balance"


def _enum_col(py_enum: type[enum.Enum], **kwargs):
    return mapped_column(
        Enum(py_enum, native_enum=False, length=16, validate_strings=True),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base):
    """Global account identity. One row per real person across all pools."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class Pool(Base):
    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    policy_template_id: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    governance_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    webhook_url: Mapped[str | None] = mapped_column(String, nullable=True)


class Membership(Base):
    """A user's role inside a single pool. One row per (user, pool) pair."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "pool_id", name="uq_membership_user_pool"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    status: Mapped[MemberStatus] = _enum_col(
        MemberStatus, default=MemberStatus.invited, nullable=False
    )
    role: Mapped[MemberRole] = _enum_col(
        MemberRole, default=MemberRole.member, nullable=False
    )


class Contribution(Base):
    __tablename__ = "contributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    recorded_by: Mapped[int] = mapped_column(ForeignKey("memberships.id"), nullable=False)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    amount_requested: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_uris: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    status: Mapped[ClaimStatus] = _enum_col(
        ClaimStatus, default=ClaimStatus.submitted, nullable=False
    )


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    decision: Mapped[VoteDecision] = _enum_col(VoteDecision, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cast_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    amount_paid: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    recorded_by: Mapped[int] = mapped_column(ForeignKey("memberships.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    kind: Mapped[LedgerKind] = _enum_col(LedgerKind, nullable=False)
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    actor_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("memberships.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Auth (added in step 2; not in docs/architecture.md data model section but
# implied by "Sessions are server-side, stored in DB. No passwords.")
# ---------------------------------------------------------------------------
class LoginToken(Base):
    """Single-use magic-link token for activating an auth session."""

    __tablename__ = "login_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class AuthSession(Base):
    """Server-side session keyed by an opaque cookie token."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


# Back-compat alias: lots of v0 code still imports ``Member``. The class is
# now ``Membership``; this alias lets the import keep working for one release
# while routers and tests migrate. Drop in a follow-up cleanup.
Member = Membership
