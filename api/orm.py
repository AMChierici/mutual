"""SQLAlchemy ORM models — schema mirrors docs/architecture.md.

Money fields are stored as integers in the smallest currency unit (cents),
which keeps arithmetic exact across the ledger. Enums are stored as VARCHAR
via SQLAlchemy's non-native ``Enum`` so the schema stays portable to Postgres
later without migration churn.

LedgerEntry and AuditEvent are append-only by convention; the database does
not enforce that — the application layer must.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base, UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
# Tables
# ---------------------------------------------------------------------------
class Pool(Base):
    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
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
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    recorded_by: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("pools.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
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
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    decision: Mapped[VoteDecision] = _enum_col(VoteDecision, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cast_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )


class Payout(Base):
    __tablename__ = "payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    amount_paid: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    recorded_by: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
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
        ForeignKey("members.id"), nullable=True
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
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
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
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
