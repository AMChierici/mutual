"""Account-level routes — not pool-scoped.

These are the user's home outside any single pool:

* ``GET /`` — landing page. Redirect to ``/login`` if not signed in,
  otherwise to ``/pools/{slug}/`` (the pool they last touched / the first
  one they belong to) or ``/pools/`` if they're in more than one.
* ``GET /pools/`` — the user's pool picker.
* ``GET /pools/new`` / ``POST /pools/new`` — wizard for creating an
  additional pool under the current account.

The first-run install wizard at ``/setup`` is a separate concern; it only
runs when zero pools exist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import current_user
from api.deps import get_db
from api.orm import Membership, MemberStatus, Pool, User
from api.policies import list_policy_templates, read_policy_template
from api.setup import (
    AdditionalPoolRequest,
    GovernanceTier,
    create_additional_pool,
    is_first_run,
)

router = APIRouter(tags=["account"])

_SCHEMES = ["auto_approve", "majority", "unanimous"]


def _dollars_to_cents(raw: str | None) -> int:
    if raw is None:
        return 0
    raw = raw.strip()
    if not raw:
        return 0
    return int(round(float(raw) * 100))


def _parse_tiers(form) -> list[GovernanceTier]:
    indices = sorted(
        {k.removeprefix("tier_scheme_") for k in form if k.startswith("tier_scheme_")},
        key=lambda s: int(s) if s.isdigit() else s,
    )
    tiers: list[GovernanceTier] = []
    for idx in indices:
        scheme = form.get(f"tier_scheme_{idx}")
        if not scheme:
            continue
        max_raw = (form.get(f"tier_max_{idx}") or "").strip()
        max_cents = _dollars_to_cents(max_raw) if max_raw else None
        tiers.append(GovernanceTier(max_amount_cents=max_cents, scheme=scheme))
    return tiers


def _user_pools(db: Session, user: User) -> list[Pool]:
    return list(
        db.scalars(
            select(Pool)
            .join(Membership, Membership.pool_id == Pool.id)
            .where(Membership.user_id == user.id)
            .where(Membership.status == MemberStatus.active)
            .order_by(Pool.created_at)
        )
    )


@router.get("/", response_class=HTMLResponse)
def account_home(request: Request, db: Session = Depends(get_db)):
    if is_first_run(db):
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    # Defer auth to the listing page; that page redirects to /login on 401
    # via the HTML-aware exception handler in main.py.
    return RedirectResponse("/pools/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/pools/", response_class=HTMLResponse)
def list_pools(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    pools = _user_pools(db, user)
    if len(pools) == 1:
        # If a user only belongs to one pool, skip the picker.
        return RedirectResponse(
            f"/pools/{pools[0].slug}/", status_code=status.HTTP_303_SEE_OTHER
        )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "account/pools.html",
        {"user": user, "pools": pools},
    )


@router.get("/pools/new", response_class=HTMLResponse)
def new_pool_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    errors: list[str] | None = None,
    values: dict | None = None,
):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "account/new_pool.html",
        {
            "user": user,
            "policy_templates": list_policy_templates(),
            "schemes": _SCHEMES,
            "errors": errors or [],
            "values": values or {},
        },
    )


@router.post("/pools/new", response_class=HTMLResponse)
async def post_new_pool(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    form = await request.form()
    try:
        req = AdditionalPoolRequest(
            pool_name=(form.get("pool_name") or "").strip(),
            currency=(form.get("currency") or "").strip(),
            starting_balance_cents=_dollars_to_cents(form.get("starting_balance_dollars")),
            policy_template_id=(form.get("policy_template_id") or "").strip() or None,
            policy_text=form.get("policy_text") or "",
            governance_tiers=_parse_tiers(form),
        )
    except ValidationError as exc:
        errors = [e["msg"] for e in exc.errors()]
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "account/new_pool.html",
            {
                "user": user,
                "policy_templates": list_policy_templates(),
                "schemes": _SCHEMES,
                "errors": errors,
                "values": dict(form),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    result = create_additional_pool(db, user, req)
    return RedirectResponse(
        f"/pools/{result.pool_slug}/", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/pools/new/policy", response_class=HTMLResponse)
def new_pool_policy_preview(
    request: Request,
    policy_template_id: str = "",
    user: User = Depends(current_user),
):
    """HTMX policy-preview swap for the new-pool wizard.

    Mirrors ``/setup/policy`` but lives on the authenticated side so we
    don't expose it on the no-auth setup route.
    """
    if not policy_template_id:
        text = ""
    else:
        try:
            text = read_policy_template(policy_template_id)
        except FileNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "policy template not found")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "setup/_policy_textarea.html", {"content": text}
    )
