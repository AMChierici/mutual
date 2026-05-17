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

M1 identity split: each member spec creates one :class:`User` (global,
keyed by email) plus one :class:`Membership` (role inside this pool).
Magic-link tokens and auth sessions bind to ``user_id``, not membership.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import LOGIN_TOKEN_TTL, SESSION_TTL, mint_token
from api.orm import (
    SYNTHETIC_EMAIL_DOMAIN,
    AuditEvent,
    AuthSession,
    LedgerEntry,
    LedgerKind,
    LoginToken,
    Membership,
    MemberRole,
    MemberStatus,
    Pool,
    User,
)


SchemeName = Literal["auto_approve", "majority", "unanimous"]


class SetupAlreadyComplete(Exception):
    """Raised when the wizard is called against a DB that already has a pool."""


def slugify(name: str) -> str:
    """Lowercase, hyphenated, ASCII slug. Caller handles collision suffixing."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "pool"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    n = 2
    while db.scalars(select(Pool).where(Pool.slug == slug)).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


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


class AdditionalPoolRequest(BaseModel):
    """Inputs for creating a second-or-later pool under an existing user."""

    pool_name: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    starting_balance_cents: int = Field(default=0, ge=0)
    policy_template_id: str | None = None
    policy_text: str = ""
    governance_tiers: list[GovernanceTier] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class AdditionalPoolResult(BaseModel):
    pool_id: int
    pool_slug: str
    admin_member_id: int


def create_additional_pool(
    db: Session, user: User, req: AdditionalPoolRequest
) -> AdditionalPoolResult:
    """Create a new pool under an existing :class:`User`.

    The user becomes the sole admin of the new pool. Reuses the same
    audit-event taxonomy as ``complete_setup`` so platform-admin views
    don't need to special-case how a pool was born.
    """
    now = datetime.now(timezone.utc)
    pool = Pool(
        slug=_unique_slug(db, slugify(req.pool_name)),
        name=req.pool_name,
        currency=req.currency,
        policy_template_id=req.policy_template_id,
        policy_text=req.policy_text,
        governance_config={"tiers": [t.model_dump() for t in req.governance_tiers]},
        created_at=now,
    )
    db.add(pool)
    db.flush()

    admin = Membership(
        user_id=user.id,
        pool_id=pool.id,
        display_name=user.display_name,
        role=MemberRole.admin,
        status=MemberStatus.active,
        joined_at=now,
    )
    db.add(admin)
    db.flush()

    if req.starting_balance_cents > 0:
        db.add(
            LedgerEntry(
                pool_id=pool.id,
                kind=LedgerKind.opening_balance,
                ref_id=pool.id,
                delta=req.starting_balance_cents,
                balance_after=req.starting_balance_cents,
                recorded_at=now,
            )
        )

    db.add(
        AuditEvent(
            pool_id=pool.id,
            actor_member_id=admin.id,
            kind="pool.created",
            payload_json={"name": pool.name, "currency": pool.currency, "slug": pool.slug},
            recorded_at=now,
        )
    )
    db.add(
        AuditEvent(
            pool_id=pool.id,
            actor_member_id=admin.id,
            kind="member.added",
            payload_json={
                "member_id": admin.id,
                "display_name": admin.display_name,
                "role": "admin",
            },
            recorded_at=now,
        )
    )
    if req.starting_balance_cents > 0:
        db.add(
            AuditEvent(
                pool_id=pool.id,
                actor_member_id=admin.id,
                kind="ledger.opening_balance",
                payload_json={"amount_cents": req.starting_balance_cents},
                recorded_at=now,
            )
        )

    db.commit()
    return AdditionalPoolResult(
        pool_id=pool.id, pool_slug=pool.slug, admin_member_id=admin.id
    )


def _get_or_create_user(
    db: Session, *, email: str | None, display_name: str, member_seq: int, now: datetime
) -> User:
    """Look up a User by email, or create one. ``member_seq`` is only used
    to synthesise an email for specs that didn't provide one (legacy
    behaviour mirrored on the migration path)."""
    real_email = email or f"user+seq{member_seq}@{SYNTHETIC_EMAIL_DOMAIN}"
    user = db.scalars(select(User).where(User.email == real_email)).one_or_none()
    if user is None:
        user = User(
            email=real_email,
            display_name=display_name,
            created_at=now,
        )
        db.add(user)
        db.flush()
    return user


def complete_setup(db: Session, req: SetupRequest) -> SetupResult:
    if not is_first_run(db):
        raise SetupAlreadyComplete()

    now = datetime.now(timezone.utc)

    pool = Pool(
        slug=_unique_slug(db, slugify(req.pool_name)),
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
    admin_user_id: int | None = None
    membership_rows: list[Membership] = []
    for seq, spec in enumerate(req.members, start=1):
        role = MemberRole(spec.role)
        user = _get_or_create_user(
            db,
            email=spec.email,
            display_name=spec.display_name,
            member_seq=seq,
            now=now,
        )
        m = Membership(
            user_id=user.id,
            pool_id=pool.id,
            display_name=spec.display_name,
            role=role,
            status=MemberStatus.active if role == MemberRole.admin else MemberStatus.invited,
            joined_at=now,
        )
        db.add(m)
        membership_rows.append(m)
        if admin_id is None and role == MemberRole.admin:
            # Capture the first admin so we can mint their session below.
            db.flush()
            admin_id = m.id
            admin_user_id = user.id
    db.flush()

    assert admin_id is not None and admin_user_id is not None
    # SetupRequest guarantees at least one admin; mypy/typing nudge.

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
            payload_json={"name": pool.name, "currency": pool.currency, "slug": pool.slug},
            recorded_at=now,
        )
    )
    for m in membership_rows:
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
        user_id=admin_user_id,
        token=mint_token(),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_TTL,
    )
    db.add(login)

    auth_session = AuthSession(
        user_id=admin_user_id,
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
