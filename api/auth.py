"""Magic-link auth service layer.

The flow:
    1. ``create_login_token(db, member_id)`` mints a single-use, time-limited
       token. Admin copies the resulting URL out-of-band (email, Signal,
       paper, whatever).
    2. The member opens the URL; the route layer calls
       ``consume_login_token(db, token)`` which marks the token used,
       activates the member if they were ``invited``, and returns a fresh
       :class:`AuthSession`.
    3. Every subsequent request goes through ``resolve_session(db, cookie)``
       which validates expiry and revocation.

No passwords; the link itself is the credential. Sessions are server-side
so revocation is just an UPDATE.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from api.orm import AuthSession, LoginToken, Member, MemberStatus

LOGIN_TOKEN_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=30)
SESSION_COOKIE = "mutual_session"


class AuthError(Exception):
    """Raised when a magic link cannot be consumed (unknown / used / expired / inactive)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_token() -> str:
    # 32 bytes -> ~43 url-safe chars; well within String(64).
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------
def create_login_token(db: Session, member_id: int, *, now: datetime | None = None) -> LoginToken:
    now = now or _utcnow()
    tok = LoginToken(
        member_id=member_id,
        token=_new_token(),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_TTL,
    )
    db.add(tok)
    db.commit()
    db.refresh(tok)
    return tok


def consume_login_token(db: Session, token: str, *, now: datetime | None = None) -> AuthSession:
    now = now or _utcnow()
    tok = db.scalars(select(LoginToken).where(LoginToken.token == token)).one_or_none()
    if tok is None:
        raise AuthError("unknown token")
    if tok.used_at is not None:
        raise AuthError("token already used")
    if tok.expires_at <= now:
        raise AuthError("token expired")

    member = db.get(Member, tok.member_id)
    if member is None or member.status == MemberStatus.inactive:
        raise AuthError("member not eligible")

    tok.used_at = now
    if member.status == MemberStatus.invited:
        member.status = MemberStatus.active

    auth_session = AuthSession(
        member_id=member.id,
        token=_new_token(),
        created_at=now,
        expires_at=now + SESSION_TTL,
        last_seen_at=now,
    )
    db.add(auth_session)
    db.commit()
    db.refresh(auth_session)
    return auth_session


# ---------------------------------------------------------------------------
# Session lookup
# ---------------------------------------------------------------------------
def resolve_session(
    db: Session, token: str | None, *, now: datetime | None = None
) -> AuthSession | None:
    if not token:
        return None
    now = now or _utcnow()
    s = db.scalars(select(AuthSession).where(AuthSession.token == token)).one_or_none()
    if s is None:
        return None
    if s.revoked_at is not None or s.expires_at <= now:
        return None
    return s


def refresh_session(db: Session, auth_session: AuthSession, *, now: datetime | None = None) -> None:
    auth_session.last_seen_at = now or _utcnow()
    db.commit()


def revoke_session(db: Session, token: str, *, now: datetime | None = None) -> None:
    s = db.scalars(select(AuthSession).where(AuthSession.token == token)).one_or_none()
    if s is None:
        return
    if s.revoked_at is None:
        s.revoked_at = now or _utcnow()
        db.commit()


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
def current_member(
    request: Request, db: Session = Depends(get_db)
) -> Member:
    """Return the logged-in Member, or raise 401."""
    cookie = request.cookies.get(SESSION_COOKIE)
    auth_session = resolve_session(db, cookie)
    if auth_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    refresh_session(db, auth_session)
    member = db.get(Member, auth_session.member_id)
    if member is None or member.status != MemberStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "member not active")
    return member


def require_admin(member: Member = Depends(current_member)) -> Member:
    if member.role.value != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return member


def optional_current_member(
    request: Request, db: Session = Depends(get_db)
) -> Member | None:
    """Like :func:`current_member` but returns ``None`` instead of raising."""
    cookie = request.cookies.get(SESSION_COOKIE)
    auth_session = resolve_session(db, cookie)
    if auth_session is None:
        return None
    refresh_session(db, auth_session)
    member = db.get(Member, auth_session.member_id)
    if member is None or member.status != MemberStatus.active:
        return None
    return member
