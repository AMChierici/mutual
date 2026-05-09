"""First-run setup wizard service layer.

The wizard runs once, with no prior auth, before any pool exists. Its job:

    1. Create the Pool (name, currency, policy template + edited text,
       governance tiers).
    2. Create members. The first ``role=admin`` member becomes the
       bootstrapping admin — they're created ``status=active`` so the
       wizard runner is logged in immediately. Other members are
       ``invited`` and need their magic link to come online.
    3. Record the starting balance as a ``LedgerKind.opening_balance``
       entry (skipped if balance is zero).
    4. Audit each step.
    5. Return a fresh ``AuthSession`` cookie value plus a backup magic
       link the admin can use from another device.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import LOGIN_TOKEN_TTL, SESSION_TTL, mint_token
from api.orm import (
    AuditEvent,
    AuthSession,
    LedgerEntry,
    LedgerKind,
    LoginToken,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
)


SchemeName = Literal["auto_approve", "majority", "unanimous"]


class SetupAlreadyComplete(Exception):
    """Raised when the wizard is called against a DB that already has a pool."""


class MemberSpec(BaseModel):
    display_name: str = Field(min_length=1)
    email: str | None = None
    role: Literal["member", "admin", "observer"] = "member"


class GovernanceTier(BaseModel):
    """A claim-amount band routed to a single voting scheme.

    ``max_amount_cents=None`` means "no upper bound" (catch-all). Tiers are
    evaluated in list order at vote time (step 6).
    """

    max_amount_cents: int | None = Field(default=None, ge=0)
    scheme: SchemeName


class SetupRequest(BaseModel):
    pool_name: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    starting_balance_cents: int = Field(default=0, ge=0)
    members: list[MemberSpec] = Field(min_length=1)
    policy_template_id: str | None = None
    policy_text: str = ""
    governance_tiers: list[GovernanceTier] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("members")
    @classmethod
    def _at_least_one_admin(cls, members: list[MemberSpec]) -> list[MemberSpec]:
        if not any(m.role == "admin" for m in members):
            raise ValueError("at least one member must have role 'admin'")
        return members


class SetupResult(BaseModel):
    pool_id: int
    admin_member_id: int
    admin_login_url: str
    admin_session_token: str


def is_first_run(db: Session) -> bool:
    return db.scalars(select(Pool).limit(1)).first() is None


def complete_setup(db: Session, req: SetupRequest) -> SetupResult:
    if not is_first_run(db):
        raise SetupAlreadyComplete()

    now = datetime.now(timezone.utc)

    pool = Pool(
        name=req.pool_name,
        currency=req.currency,
        policy_template_id=req.policy_template_id,
        policy_text=req.policy_text,
        governance_config={"tiers": [t.model_dump() for t in req.governance_tiers]},
        created_at=now,
    )
    db.add(pool)
    db.flush()

    admin_id: int | None = None
    member_rows: list[Member] = []
    for spec in req.members:
        role = MemberRole(spec.role)
        m = Member(
            pool_id=pool.id,
            display_name=spec.display_name,
            email=spec.email,
            role=role,
            status=MemberStatus.active if role == MemberRole.admin else MemberStatus.invited,
            joined_at=now,
        )
        db.add(m)
        member_rows.append(m)
    db.flush()

    for m in member_rows:
        if m.role == MemberRole.admin:
            admin_id = m.id
            break
    assert admin_id is not None  # SetupRequest guarantees at least one admin

    if req.starting_balance_cents > 0:
        db.add(
            LedgerEntry(
                pool_id=pool.id,
                kind=LedgerKind.opening_balance,
                ref_id=pool.id,  # opening balance has no source row; point at the pool
                delta=req.starting_balance_cents,
                balance_after=req.starting_balance_cents,
                recorded_at=now,
            )
        )

    db.add(
        AuditEvent(
            pool_id=pool.id,
            actor_member_id=admin_id,
            kind="pool.created",
            payload_json={"name": pool.name, "currency": pool.currency},
            recorded_at=now,
        )
    )
    for m in member_rows:
        db.add(
            AuditEvent(
                pool_id=pool.id,
                actor_member_id=admin_id,
                kind="member.added",
                payload_json={
                    "member_id": m.id,
                    "display_name": m.display_name,
                    "role": m.role.value,
                },
                recorded_at=now,
            )
        )
    if req.starting_balance_cents > 0:
        db.add(
            AuditEvent(
                pool_id=pool.id,
                actor_member_id=admin_id,
                kind="ledger.opening_balance",
                payload_json={"amount_cents": req.starting_balance_cents},
                recorded_at=now,
            )
        )

    login = LoginToken(
        member_id=admin_id,
        token=mint_token(),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_TTL,
    )
    db.add(login)

    auth_session = AuthSession(
        member_id=admin_id,
        token=mint_token(),
        created_at=now,
        expires_at=now + SESSION_TTL,
        last_seen_at=now,
    )
    db.add(auth_session)

    db.commit()

    return SetupResult(
        pool_id=pool.id,
        admin_member_id=admin_id,
        admin_login_url=f"/auth/login/{login.token}",
        admin_session_token=auth_session.token,
    )
