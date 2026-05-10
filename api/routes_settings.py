"""Admin settings + the manual monthly-close webhook trigger."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import require_admin
from api.deps import get_db
from api.orm import Member, Pool
from api.webhooks import (
    InvalidWebhookURL,
    dispatch_event,
    get_webhook_url,
    set_webhook_url,
)

router = APIRouter(tags=["settings"])


def _the_pool(db: Session) -> Pool:
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "pool not initialized")
    return pool


@router.get("/settings", response_class=HTMLResponse)
def get_settings(
    request: Request,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
):
    pool = _the_pool(db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "pool": pool,
            "webhook_url": get_webhook_url(db, pool.id) or "",
            "errors": [],
        },
    )


@router.post("/settings/webhook", response_class=HTMLResponse)
def post_webhook_url(
    request: Request,
    webhook_url: str = Form(""),
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> RedirectResponse:
    pool = _the_pool(db)
    raw = (webhook_url or "").strip()
    try:
        set_webhook_url(db, pool.id, raw or None)
    except InvalidWebhookURL as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/webhooks/monthly-close", response_class=HTMLResponse)
def trigger_monthly_close(
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> RedirectResponse:
    pool = _the_pool(db)
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    dispatch_event(
        db,
        pool.id,
        "monthly_close.due",
        {"pool_id": pool.id, "period": period},
    )
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)
