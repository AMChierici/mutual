"""HTTP routes for recording contributions.

In v0 the pool admin is the treasurer (the architecture role enum doesn't
include 'treasurer'); both endpoints are admin-only.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import require_admin
from api.contributions import (
    BulkContributionRow,
    record_bulk,
    record_contribution,
)
from api.deps import get_db
from api.orm import Contribution, Member, MemberStatus, Pool

router = APIRouter(prefix="/contributions", tags=["contributions"])


class ContributionCreate(BaseModel):
    member_id: int
    amount_cents: int = Field(ge=1)
    period: str


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _dollars_to_cents(raw: str | None) -> int:
    if raw is None:
        return 0
    raw = raw.strip()
    if not raw:
        return 0
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return 0


def _the_pool(db: Session) -> Pool:
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "pool not initialized")
    return pool


@router.post("", status_code=status.HTTP_201_CREATED)
def post_contribution(
    payload: ContributionCreate,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> JSONResponse:
    pool = _the_pool(db)
    try:
        c = record_contribution(
            db,
            pool_id=pool.id,
            member_id=payload.member_id,
            amount_cents=payload.amount_cents,
            period=payload.period,
            recorded_by=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "id": c.id,
            "member_id": c.member_id,
            "amount_cents": c.amount,
            "period": c.period,
        },
    )


@router.get("/bulk", response_class=HTMLResponse)
def get_bulk(
    request: Request,
    period: str | None = None,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> HTMLResponse:
    pool = _the_pool(db)
    period = period or _current_period()

    members = (
        db.query(Member)
        .filter(Member.pool_id == pool.id, Member.status != MemberStatus.inactive)
        .order_by(Member.display_name)
        .all()
    )
    existing_by_member: dict[int, int] = {}
    for c in (
        db.query(Contribution)
        .filter_by(pool_id=pool.id, period=period)
        .all()
    ):
        existing_by_member[c.member_id] = (
            existing_by_member.get(c.member_id, 0) + c.amount
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "contributions/bulk.html",
        {
            "period": period,
            "members": members,
            "existing_by_member": existing_by_member,
            "currency": pool.currency,
        },
    )


@router.post("/bulk", response_class=HTMLResponse)
async def post_bulk(
    request: Request,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> HTMLResponse:
    pool = _the_pool(db)
    form = await request.form()
    period = (form.get("period") or "").strip()

    rows: list[BulkContributionRow] = []
    for key, value in form.items():
        if not key.startswith("amount_"):
            continue
        suffix = key.removeprefix("amount_")
        try:
            member_id = int(suffix)
        except ValueError:
            continue
        cents = _dollars_to_cents(value)
        if cents > 0:
            rows.append(BulkContributionRow(member_id=member_id, amount_cents=cents))

    try:
        summary = record_bulk(
            db,
            pool_id=pool.id,
            period=period,
            rows=rows,
            recorded_by=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "contributions/bulk_done.html",
        {
            "period": period,
            "created_count": len(summary.created_contribution_ids),
            "skipped_count": len(summary.skipped_member_ids),
        },
    )
