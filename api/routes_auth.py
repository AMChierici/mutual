"""HTTP routes for magic-link auth.

Each successful state change here also writes one ``AuditEvent`` so the
read-only viewer at ``/audit`` (step 9) has a complete picture:

* ``auth.magic_link_minted`` — admin minted a login URL for someone.
* ``auth.login`` — a member consumed a token and got a session.
* ``auth.logout`` — a member's session was revoked.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    AuthError,
    consume_login_token,
    create_login_token,
    require_admin,
    resolve_session,
    revoke_session,
)
from api.deps import get_db
from api.orm import AuditEvent, Membership

router = APIRouter(prefix="/auth", tags=["auth"])


class MagicLinkRequest(BaseModel):
    member_id: int


def _audit(db: Session, *, pool_id: int, actor_id: int | None, kind: str, payload: dict) -> None:
    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=actor_id,
            kind=kind,
            payload_json=payload,
            recorded_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


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

    # Pick the user's first active membership for the audit + welcome view.
    # Single-pool installs (M1) have exactly one. M2 will instead show a
    # pool picker for users in multiple pools.
    membership = db.scalars(
        select(Membership).where(Membership.user_id == auth_session.user_id).order_by(Membership.id)
    ).first()
    if membership is not None:
        _audit(
            db,
            pool_id=membership.pool_id,
            actor_id=membership.id,
            kind="auth.login",
            payload={"auth_session_id": auth_session.id},
        )

    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(
        request, "auth/login_success.html", {"member": membership}
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
        # Resolve the session before we revoke it so we still know who the
        # actor was. ``resolve_session`` returns None for an already-revoked
        # or expired session — we audit only on a live revoke.
        live = resolve_session(db, cookie)
        if live is not None:
            membership = db.scalars(
                select(Membership)
                .where(Membership.user_id == live.user_id)
                .order_by(Membership.id)
            ).first()
            revoke_session(db, cookie)
            if membership is not None:
                _audit(
                    db,
                    pool_id=membership.pool_id,
                    actor_id=membership.id,
                    kind="auth.logout",
                    payload={"auth_session_id": live.id},
                )

    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(request, "auth/logged_out.html", {})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/magic-link")
def create_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_admin),
) -> JSONResponse:
    target = db.get(Membership, payload.member_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    tok = create_login_token(db, target.user_id)
    _audit(
        db,
        pool_id=admin.pool_id,
        actor_id=admin.id,
        kind="auth.magic_link_minted",
        payload={
            "target_member_id": target.id,
            "login_token_id": tok.id,
        },
    )
    return JSONResponse(
        {
            "member_id": target.id,
            "url": str(request.url_for("login", token=tok.token).path),
            "expires_at": tok.expires_at.isoformat(),
        }
    )
