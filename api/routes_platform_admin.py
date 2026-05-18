"""Platform-admin (cross-pool operator) view (M4).

Read-only by design. A platform admin can see every pool, every user,
balances, and recent activity, but cannot mutate. The flag is set via
the ``MUTUAL_PLATFORM_ADMIN_EMAIL`` env var at startup
(see ``api.main.ensure_platform_admin``).

PII safety: by default ``Claim.description``, evidence URIs, and member
emails are redacted in these views. Set
``MUTUAL_PLATFORM_ADMIN_SEES_PII=1`` to unredact for forensic / support
work — the env var is the off-by-default kill switch.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.auth import require_platform_admin
from api.deps import get_db
from api.orm import (
    AuditEvent,
    AuthSession,
    Claim,
    LedgerEntry,
    LoginToken,
    Membership,
    Pool,
    User,
    is_synthetic_email,
)

router = APIRouter(prefix="/admin", tags=["platform-admin"])

PLATFORM_ADMIN_PII_ENV = "MUTUAL_PLATFORM_ADMIN_SEES_PII"


def _sees_pii() -> bool:
    return os.environ.get(PLATFORM_ADMIN_PII_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _redact_email(email: str | None, *, allow_pii: bool) -> str:
    """Show the email as-is when PII is allowed; otherwise show
    only the domain so a synthetic placeholder is still recognisable.
    """
    if not email:
        return "—"
    if is_synthetic_email(email):
        return "(no email on file)"
    if allow_pii:
        return email
    if "@" not in email:
        return "***"
    _, _, domain = email.partition("@")
    return f"***@{domain}"


def _redact_text(text: str | None, *, allow_pii: bool, fallback: str = "[redacted]") -> str:
    if not text:
        return "—"
    if allow_pii:
        return text
    return fallback


def _pool_balance(db: Session, pool_id: int) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.delta), 0))
            .where(LedgerEntry.pool_id == pool_id)
        )
        or 0
    )


@router.get("", response_class=HTMLResponse)
def overview(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
) -> HTMLResponse:
    pool_count = db.scalar(select(func.count(Pool.id))) or 0
    user_count = db.scalar(select(func.count(User.id))) or 0
    pools = list(
        db.scalars(select(Pool).order_by(Pool.created_at.desc()))
    )
    rows = [
        {
            "pool": p,
            "balance_cents": _pool_balance(db, p.id),
            "member_count": db.scalar(
                select(func.count(Membership.id)).where(Membership.pool_id == p.id)
            )
            or 0,
        }
        for p in pools
    ]
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "platform_admin/overview.html",
        {
            "admin": admin,
            "pool_count": pool_count,
            "user_count": user_count,
            "rows": rows,
            "allow_pii": _sees_pii(),
        },
    )


@router.get("/pools", response_class=HTMLResponse)
def list_pools(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
) -> HTMLResponse:
    pools = list(db.scalars(select(Pool).order_by(Pool.created_at.desc())))
    rows = [
        {
            "pool": p,
            "balance_cents": _pool_balance(db, p.id),
            "member_count": db.scalar(
                select(func.count(Membership.id)).where(Membership.pool_id == p.id)
            )
            or 0,
            "claim_count": db.scalar(
                select(func.count(Claim.id)).where(Claim.pool_id == p.id)
            )
            or 0,
        }
        for p in pools
    ]
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "platform_admin/pools.html",
        {"rows": rows, "allow_pii": _sees_pii()},
    )


@router.get("/pools/{pool_id}", response_class=HTMLResponse)
def pool_detail(
    pool_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
) -> HTMLResponse:
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool not found")

    allow_pii = _sees_pii()
    memberships = list(
        db.scalars(
            select(Membership).where(Membership.pool_id == pool.id)
            .order_by(Membership.role.desc(), Membership.display_name)
        )
    )
    users_by_id = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_({m.user_id for m in memberships}))
        )
    }

    member_rows = [
        {
            "membership": m,
            "email": _redact_email(
                users_by_id.get(m.user_id).email if m.user_id in users_by_id else None,
                allow_pii=allow_pii,
            ),
        }
        for m in memberships
    ]

    recent_claims = list(
        db.scalars(
            select(Claim)
            .where(Claim.pool_id == pool.id)
            .order_by(Claim.submitted_at.desc())
            .limit(20)
        )
    )
    recent_audit = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.pool_id == pool.id)
            .order_by(AuditEvent.recorded_at.desc())
            .limit(20)
        )
    )

    claim_rows = [
        {
            "id": c.id,
            "status": c.status.value,
            "amount_cents": c.amount_requested,
            "category": c.category,
            "description": _redact_text(c.description, allow_pii=allow_pii),
            "occurred_at": c.occurred_at,
            "evidence_count": len(c.evidence_uris or []),
        }
        for c in recent_claims
    ]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "platform_admin/pool_detail.html",
        {
            "pool": pool,
            "balance_cents": _pool_balance(db, pool.id),
            "member_rows": member_rows,
            "claim_rows": claim_rows,
            "audit_events": recent_audit,
            "allow_pii": allow_pii,
        },
    )


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
) -> HTMLResponse:
    allow_pii = _sees_pii()
    users = list(db.scalars(select(User).order_by(User.created_at.desc())))
    rows = [
        {
            "user": u,
            "email": _redact_email(u.email, allow_pii=allow_pii),
            "membership_count": db.scalar(
                select(func.count(Membership.id)).where(Membership.user_id == u.id)
            )
            or 0,
        }
        for u in users
    ]
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "platform_admin/users.html",
        {"rows": rows, "allow_pii": allow_pii},
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_admin),
) -> HTMLResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    allow_pii = _sees_pii()
    memberships = list(
        db.scalars(select(Membership).where(Membership.user_id == user.id))
    )
    pools_by_id = {
        p.id: p
        for p in db.scalars(
            select(Pool).where(Pool.id.in_({m.pool_id for m in memberships}))
        )
    }
    membership_rows = [
        {"membership": m, "pool": pools_by_id.get(m.pool_id)}
        for m in memberships
    ]

    recent_sessions = list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.created_at.desc())
            .limit(10)
        )
    )
    recent_tokens = list(
        db.scalars(
            select(LoginToken)
            .where(LoginToken.user_id == user.id)
            .order_by(LoginToken.created_at.desc())
            .limit(10)
        )
    )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "platform_admin/user_detail.html",
        {
            "subject": user,
            "subject_email": _redact_email(user.email, allow_pii=allow_pii),
            "membership_rows": membership_rows,
            "recent_sessions": recent_sessions,
            "recent_tokens": recent_tokens,
            "allow_pii": allow_pii,
        },
    )
