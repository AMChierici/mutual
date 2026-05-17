"""Admin settings + the manual monthly-close webhook trigger.

Pool-scoped: mounted under ``/pools/{pool_slug}``. Every action here
requires the caller to be an admin in *this specific pool*.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import require_pool_admin
from api.deps import get_db, get_pool_from_slug
from api.orm import AuditEvent, Membership, Pool
from api.setup import slugify
from api.webhooks import (
    InvalidWebhookURL,
    dispatch_event,
    get_webhook_url,
    set_webhook_url,
)

router = APIRouter(prefix="/pools/{pool_slug}", tags=["settings"])


def _audit(
    db: Session,
    *,
    pool_id: int,
    actor_id: int,
    kind: str,
    payload: dict,
) -> None:
    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=actor_id,
            kind=kind,
            payload_json=payload,
            recorded_at=datetime.now(timezone.utc),
        )
    )


@router.get("/settings", response_class=HTMLResponse)
def get_settings(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
    flash: str | None = None,
    error: str | None = None,
):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "pool": pool,
            "webhook_url": get_webhook_url(db, pool.id) or "",
            "flash": flash,
            "error": error,
        },
    )


@router.post("/settings/identity", response_class=HTMLResponse)
def post_identity(
    request: Request,
    pool_name: str = Form(""),
    slug: str = Form(""),
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> RedirectResponse:
    new_name = (pool_name or "").strip()
    new_slug = slugify((slug or "").strip() or new_name)
    if not new_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pool name is required")

    if new_slug != pool.slug:
        clash = db.scalars(
            select(Pool).where(Pool.slug == new_slug).where(Pool.id != pool.id)
        ).one_or_none()
        if clash is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"slug {new_slug!r} is already used by another pool",
            )

    old_name = pool.name
    old_slug = pool.slug
    pool.name = new_name
    pool.slug = new_slug

    if old_name != new_name or old_slug != new_slug:
        _audit(
            db,
            pool_id=pool.id,
            actor_id=admin.id,
            kind="pool.renamed",
            payload={
                "old_name": old_name,
                "new_name": new_name,
                "old_slug": old_slug,
                "new_slug": new_slug,
            },
        )
    db.commit()
    return RedirectResponse(
        f"/pools/{pool.slug}/settings?flash=identity+saved",
        status_code=status.HTTP_303_SEE_OTHER,
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
