"""Dashboard HTTP routes — pool overview (`/pools/{slug}/`) plus the
actuarial-output tab (`/pools/{slug}/models`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from api.auth import current_membership_for_pool
from api.dashboard import (
    DEFAULT_BUCKET_BY,
    member_contribution_status,
    monthly_buckets,
    overview_summary,
    pending_claims,
)
from api.dashboard_models import compute_pricing, compute_reserving
from api.deps import get_db, get_pool_from_slug
from api.orm import Member, Membership, Pool

router = APIRouter(prefix="/pools/{pool_slug}", tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
@router.get("")
def overview(
    request: Request,
    bucket: str = DEFAULT_BUCKET_BY,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    member: Membership = Depends(current_membership_for_pool),
):
    bucket_by = bucket if bucket in ("period", "recorded_at") else DEFAULT_BUCKET_BY
    summary = overview_summary(db, pool.id)
    buckets = monthly_buckets(db, pool.id, bucket_by=bucket_by)
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
            "pool": pool,
            "active_tab": "overview",
            "is_admin": member.role.value == "admin",
            "summary": summary,
            "buckets": buckets,
            "chart_max_cents": chart_max_cents,
            "members_status": members_status,
            "pending": pendings,
            "bucket_by": bucket_by,
        },
    )


@router.get("/models", response_class=HTMLResponse)
def models_tab(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    member: Membership = Depends(current_membership_for_pool),
):
    pricing = compute_pricing(db, pool.id)
    reserving = compute_reserving(db, pool.id, simulations=1000, seed=0)

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
            "pool": pool,
            "active_tab": "models",
            "is_admin": member.role.value == "admin",
            "summary": summary,
            "pricing": pricing,
            "reserving": reserving,
            "members_by_id": members_by_id,
        },
    )
