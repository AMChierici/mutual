"""HTTP routes for the first-run setup wizard."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from api.auth import SESSION_COOKIE, SESSION_TTL, mint_token
from api.deps import get_db
from api.policies import list_policy_templates, read_policy_template
from api.setup import (
    GovernanceTier,
    MemberSpec,
    SetupAlreadyComplete,
    SetupRequest,
    complete_setup,
    is_first_run,
)

router = APIRouter(prefix="/setup", tags=["setup"])

_SCHEMES = ["auto_approve", "majority", "unanimous"]


def _dollars_to_cents(raw: str | None) -> int:
    if raw is None:
        return 0
    raw = raw.strip()
    if not raw:
        return 0
    return int(round(float(raw) * 100))


def _parse_members(form) -> list[MemberSpec]:
    indices = sorted(
        {k.removeprefix("member_name_") for k in form if k.startswith("member_name_")}
    )
    members: list[MemberSpec] = []
    for idx in indices:
        name = (form.get(f"member_name_{idx}") or "").strip()
        if not name:
            continue
        members.append(
            MemberSpec(
                display_name=name,
                email=(form.get(f"member_email_{idx}") or "").strip() or None,
                role=form.get(f"member_role_{idx}", "member"),
            )
        )
    return members


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


def _render_wizard(
    request: Request,
    *,
    errors: list[str] | None = None,
    values: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "setup/wizard.html",
        {
            "policy_templates": list_policy_templates(),
            "schemes": _SCHEMES,
            "errors": errors or [],
            "values": values or {},
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def get_wizard(request: Request, db: Session = Depends(get_db)):
    if not is_first_run(db):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return _render_wizard(request)


@router.post("", response_class=HTMLResponse)
async def post_wizard(request: Request, db: Session = Depends(get_db)):
    if not is_first_run(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "setup already completed")

    form = await request.form()

    try:
        req = SetupRequest(
            pool_name=(form.get("pool_name") or "").strip(),
            currency=(form.get("currency") or "").strip(),
            starting_balance_cents=_dollars_to_cents(form.get("starting_balance_dollars")),
            members=_parse_members(form),
            policy_template_id=(form.get("policy_template_id") or "").strip() or None,
            policy_text=form.get("policy_text") or "",
            governance_tiers=_parse_tiers(form),
        )
    except ValidationError as exc:
        errors = [e["msg"] for e in exc.errors()]
        return _render_wizard(request, errors=errors, values=dict(form), status_code=400)

    try:
        result = complete_setup(db, req)
    except SetupAlreadyComplete:
        # Race: someone else completed setup between our check and commit.
        raise HTTPException(status.HTTP_409_CONFLICT, "setup already completed")

    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(
        request,
        "setup/done.html",
        {
            "login_url": result.admin_login_url,
            "pool_name": req.pool_name,
        },
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=result.admin_session_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@router.get("/member-row", response_class=HTMLResponse)
def member_row(request: Request) -> HTMLResponse:
    """HTMX append-row endpoint. Generates a unique suffix per row."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "setup/_member_row.html",
        {"i": mint_token()[:8]},
    )


@router.get("/policy", response_class=HTMLResponse)
def policy_preview(
    request: Request, policy_template_id: str = ""
) -> HTMLResponse:
    """HTMX policy-textarea swap. Returns the template's markdown wrapped in
    a fresh textarea (replaces the prior one via ``hx-swap=outerHTML``)."""
    if not policy_template_id:
        text = ""
    else:
        try:
            text = read_policy_template(policy_template_id)
        except FileNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "policy template not found")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "setup/_policy_textarea.html",
        {"content": text},
    )
