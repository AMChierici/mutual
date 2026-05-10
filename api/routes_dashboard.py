"""Dashboard HTTP routes — the post-login landing page (`/`) plus the
actuarial-output tab (`/models`).

Both routes redirect first-run installs to ``/setup``. Past first run, both
require an active member's session cookie.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import SESSION_COOKIE, refresh_session, resolve_session
from api.dashboard import (
    member_contribution_status,
    monthly_buckets,
    overview_summary,
    pending_claims,
)
from api.dashboard_models import compute_pricing, compute_reserving
from api.deps import get_db
from api.orm import Member, MemberStatus, Pool

router = APIRouter(tags=["dashboard"])


def _get_pool_or_redirect(db: Session) -> Pool | RedirectResponse:
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return pool


def _current_member_strict(request: Request, db: Session) -> Member:
    """Like ``api.auth.current_member`` but used by routes that handle the
    no-pool redirect themselves before authenticating."""
    cookie = request.cookies.get(SESSION_COOKIE)
    auth_session = resolve_session(db, cookie)
    if auth_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    refresh_session(db, auth_session)
    member = db.get(Member, auth_session.member_id)
    if member is None or member.status != MemberStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "member not active")
    return member


@router.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_db)):
    pool_or_redirect = _get_pool_or_redirect(db)
    if isinstance(pool_or_redirect, RedirectResponse):
        return pool_or_redirect
    pool = pool_or_redirect

    member = _current_member_strict(request, db)

    summary = overview_summary(db, pool.id)
    buckets = monthly_buckets(db, pool.id)
    members_status = member_contribution_status(db, pool.id)
    pendings = pending_claims(db, pool.id)

    chart_max_cents = max(
        (b.contributions_cents for b in buckets), default=0
    )
    chart_max_cents = max(
        chart_max_cents, max((b.payouts_cents for b in buckets), default=0)
    )
    chart_max_cents = chart_max_cents or 1  # avoid divide-by-zero in template

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard/overview.html",
        {
            "active_tab": "overview",
            "is_admin": member.role.value == "admin",
            "summary": summary,
            "buckets": buckets,
            "chart_max_cents": chart_max_cents,
            "members_status": members_status,
            "pending": pendings,
        },
    )


@router.get("/models", response_class=HTMLResponse)
def models_tab(request: Request, db: Session = Depends(get_db)):
    pool_or_redirect = _get_pool_or_redirect(db)
    if isinstance(pool_or_redirect, RedirectResponse):
        return pool_or_redirect
    pool = pool_or_redirect

    member = _current_member_strict(request, db)

    pricing = compute_pricing(db, pool.id)
    reserving = compute_reserving(db, pool.id, simulations=1000, seed=0)

    # Map premium ids back to member display names for friendlier output.
    members_by_id = {
        str(m.id): m
        for m in db.query(Member).filter(Member.pool_id == pool.id).all()
    }
    summary = overview_summary(db, pool.id)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard/models.html",
        {
            "active_tab": "models",
            "is_admin": member.role.value == "admin",
            "summary": summary,
            "pricing": pricing,
            "reserving": reserving,
            "members_by_id": members_by_id,
        },
    )
