"""HTTP routes for magic-link auth."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    consume_login_token,
    create_login_token,
    require_admin,
    revoke_session,
)
from api.deps import get_db
from api.orm import Member

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    member_id: int


@router.get("/login/{token}", response_class=HTMLResponse)
def login(token: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        auth_session = consume_login_token(db, token)
    except AuthError as exc:
        templates = request.app.state.templates
        body = templates.TemplateResponse(
            request, "auth/login_error.html", {"reason": str(exc)}
        ).body
        return HTMLResponse(content=body, status_code=status.HTTP_400_BAD_REQUEST)

    member = db.get(Member, auth_session.member_id)
    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(
        request, "auth/login_success.html", {"member": member}
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=auth_session.token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=False,  # caller deploys behind HTTPS terminator; flip when wired
        path="/",
    )
    return response


@router.post("/logout", response_class=HTMLResponse)
def logout(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        revoke_session(db, cookie)
    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(request, "auth/logged_out.html", {})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/magic-link")
def create_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin: Member = Depends(require_admin),
) -> JSONResponse:
    target = db.get(Member, payload.member_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    tok = create_login_token(db, target.id)
    return JSONResponse(
        {
            "member_id": target.id,
            "url": str(request.url_for("login", token=tok.token).path),
            "expires_at": tok.expires_at.isoformat(),
        }
    )
