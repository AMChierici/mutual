"""Magic-link auth service layer.

The flow (M1: account-scoped sessions):
    1. ``create_login_token(db, user_id)`` mints a single-use, time-limited
       token bound to a :class:`User`. Admin copies the URL out-of-band.
    2. The member opens the URL; the route layer calls
       ``consume_login_token(db, token)`` which marks the token used,
       activates any invited memberships the user has, and returns a fresh
       :class:`AuthSession`.
    3. Every subsequent request goes through ``resolve_session(db, cookie)``
       which validates expiry and revocation.

The session belongs to the account (User), not to a pool. The current pool
is resolved from the URL slug in pool-scoped routes (M2). For M1, routes
still use the legacy ``current_member`` dependency which returns the user's
single membership — single-pool behaviour is preserved.

No passwords; the link itself is the credential. Sessions are server-side
so revocation is just an UPDATE.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db, get_pool_from_slug
from api.orm import AuthSession, LoginToken, Membership, MemberStatus, Pool, User

LOGIN_TOKEN_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=30)
SESSION_COOKIE = "mutual_session"


class AuthError(Exception):
    """Raised when a magic link cannot be consumed (unknown / used / expired / inactive)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mint_token() -> str:
    """Cryptographically random url-safe token used for both login tokens
    and session cookies. ~43 chars, well within ``String(64)``.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------
def create_login_token(db: Session, user_id: int, *, now: datetime | None = None) -> LoginToken:
    now = now or _utcnow()
    tok = LoginToken(
        user_id=user_id,
        token=mint_token(),
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

    user = db.get(User, tok.user_id)
    if user is None:
        raise AuthError("user not found")

    memberships = db.scalars(
        select(Membership).where(Membership.user_id == user.id)
    ).all()

    # Activate any invited memberships — this is the "I clicked my invite
    # link" moment.
    for m in memberships:
        if m.status == MemberStatus.invited:
            m.status = MemberStatus.active

    active = [m for m in memberships if m.status == MemberStatus.active]
    # A platform-admin user may have no memberships and still be allowed in;
    # everyone else needs at least one active membership.
    if not active and not user.is_platform_admin:
        raise AuthError("no active memberships")

    tok.used_at = now

    auth_session = AuthSession(
        user_id=user.id,
        token=mint_token(),
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
def current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Return the logged-in :class:`User`, or raise 401.

    M1: this is the new identity dependency. M2 will use it (plus a pool
    slug from the URL) to derive the active :class:`Membership`.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    auth_session = resolve_session(db, cookie)
    if auth_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    refresh_session(db, auth_session)
    user = db.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    return user


def current_member(
    request: Request, db: Session = Depends(get_db)
) -> Membership:
    """Legacy single-pool dependency — returns the user's active membership.

    Pre-M2 routes assume one pool per install, so a logged-in user has
    exactly one membership. We look it up and return it, preserving the
    v0 contract where routes access ``.role``, ``.pool_id``, ``.status``,
    ``.display_name`` on the returned object.

    M2 replaces this with ``current_membership(user, pool)`` resolved from
    the URL slug; both will coexist while routers migrate.
    """
    user = current_user(request, db)
    membership = db.scalars(
        select(Membership)
        .where(Membership.user_id == user.id)
        .where(Membership.status == MemberStatus.active)
        .order_by(Membership.id)
    ).first()
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no active membership")
    return membership


def require_admin(member: Membership = Depends(current_member)) -> Membership:
    if member.role.value != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return member


def current_membership_for_pool(
    pool: Pool = Depends(get_pool_from_slug),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Membership:
    """Pool-scoped membership for a URL like ``/pools/{slug}/...``.

    404s if the pool doesn't exist (handled by :func:`get_pool_from_slug`).
    404s — not 403 — if the current user has no membership in this pool;
    leaking "pool exists but you're not in it" is a small information
    disclosure we don't need to give in v1.
    """
    membership = db.scalars(
        select(Membership)
        .where(Membership.user_id == user.id)
        .where(Membership.pool_id == pool.id)
    ).one_or_none()
    if membership is None or membership.status != MemberStatus.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool not found")
    return membership


def require_pool_admin(
    membership: Membership = Depends(current_membership_for_pool),
) -> Membership:
    if membership.role.value != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return membership


def optional_current_member(
    request: Request, db: Session = Depends(get_db)
) -> Membership | None:
    """Like :func:`current_member` but returns ``None`` instead of raising."""
    cookie = request.cookies.get(SESSION_COOKIE)
    auth_session = resolve_session(db, cookie)
    if auth_session is None:
        return None
    refresh_session(db, auth_session)
    user = db.get(User, auth_session.user_id)
    if user is None:
        return None
    membership = db.scalars(
        select(Membership)
        .where(Membership.user_id == user.id)
        .where(Membership.status == MemberStatus.active)
        .order_by(Membership.id)
    ).first()
    return membership
