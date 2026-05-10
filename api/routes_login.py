"""Public ``/login`` paste-the-magic-link page.

This is the friendly landing page shown to anonymous visitors. The
exception handler in ``api.main`` redirects 401s on HTML-accepting
requests here, so a member who arrives at the dashboard with no session
sees this instead of raw JSON. The page accepts either the full magic-link
URL the admin gave them, or just the bare token.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from api.orm import Pool

router = APIRouter(tags=["login"])

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LOGIN_URL_RE = re.compile(r"/auth/login/([A-Za-z0-9_-]+)/?$")


def _extract_token(raw: str) -> str | None:
    """Pull the token out of either a full ``/auth/login/<tok>`` URL or a
    bare token. Returns ``None`` if the input doesn't match either shape.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _LOGIN_URL_RE.search(raw)
    if m:
        return m.group(1)
    if _TOKEN_RE.fullmatch(raw):
        return raw
    return None


def _pool_or_setup_redirect(db: Session) -> Pool | RedirectResponse:
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    return pool


def _render_login(
    request: Request,
    *,
    error: str | None = None,
    link_value: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "auth/login_paste.html",
        {"error": error, "link_value": link_value},
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    pool_or_redirect = _pool_or_setup_redirect(db)
    if isinstance(pool_or_redirect, RedirectResponse):
        return pool_or_redirect
    return _render_login(request)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    link: str = Form(""),
    db: Session = Depends(get_db),
):
    pool_or_redirect = _pool_or_setup_redirect(db)
    if isinstance(pool_or_redirect, RedirectResponse):
        return pool_or_redirect

    token = _extract_token(link)
    if token is None:
        return _render_login(
            request,
            error="That doesn't look like a magic link or token. Paste the full URL the admin gave you.",
            link_value=link,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        f"/auth/login/{token}", status_code=status.HTTP_303_SEE_OTHER
    )
