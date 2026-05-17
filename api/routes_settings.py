"""Admin settings + the manual monthly-close webhook trigger.

Pool-scoped: mounted under ``/pools/{pool_slug}``. Both routes require the
caller to be an admin in *this specific pool*.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from api.auth import require_pool_admin
from api.deps import get_db, get_pool_from_slug
from api.orm import Membership, Pool
from api.webhooks import (
    InvalidWebhookURL,
    dispatch_event,
    get_webhook_url,
    set_webhook_url,
)

router = APIRouter(prefix="/pools/{pool_slug}", tags=["settings"])


@router.get("/settings", response_class=HTMLResponse)
def get_settings(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
):
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
    webhook_url: str = Form(""),
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> RedirectResponse:
    raw = (webhook_url or "").strip()
    try:
        set_webhook_url(db, pool.id, raw or None)
    except InvalidWebhookURL as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/pools/{pool.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/webhooks/monthly-close", response_class=HTMLResponse)
def trigger_monthly_close(
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> RedirectResponse:
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    dispatch_event(
        db,
        pool.id,
        "monthly_close.due",
        {"pool_id": pool.id, "period": period},
    )
    return RedirectResponse(
        f"/pools/{pool.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )
