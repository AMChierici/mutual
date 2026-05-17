"""Service-layer tests for magic-link auth.

Pure logic, no HTTP — that's exercised by tests/test_auth_routes.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from api.auth import (
    AuthError,
    LOGIN_TOKEN_TTL,
    SESSION_TTL,
    consume_login_token,
    create_login_token,
    refresh_session,
    resolve_session,
    revoke_session,
)
from api.orm import (
    AuthSession,
    LoginToken,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
    User,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_slug_seq = count(1)
_user_seq = count(1)


def _pool(session, name="P", currency="USD"):
    p = Pool(
        slug=f"pool-{next(_slug_seq)}",
        name=name,
        currency=currency,
        governance_config={},
    )
    session.add(p)
    session.commit()
    return p


def _member(session, pool, name="Ada", role=MemberRole.member, status=MemberStatus.invited):
    u = User(email=f"u{next(_user_seq)}@example.test", display_name=name)
    session.add(u)
    session.flush()
    m = Member(
        user_id=u.id,
        pool_id=pool.id,
        display_name=name,
        role=role,
        status=status,
    )
    session.add(m)
    session.commit()
    return m


# ---------------------------------------------------------------------------
# create_login_token
# ---------------------------------------------------------------------------
def test_create_login_token_returns_unused_unexpired(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    assert isinstance(tok, LoginToken)
    assert tok.id is not None
    assert tok.user_id == m.user_id
    assert tok.token  # non-empty
    assert tok.used_at is None
    assert tok.expires_at > datetime.now(timezone.utc)


def test_create_login_token_uses_configured_ttl(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    delta = tok.expires_at - tok.created_at
    # within a second of LOGIN_TOKEN_TTL
    assert abs((delta - LOGIN_TOKEN_TTL).total_seconds()) < 1


def test_create_login_token_returns_unique_token_each_call(session):
    p = _pool(session)
    m = _member(session, p)
    a = create_login_token(session, m.user_id)
    b = create_login_token(session, m.user_id)
    assert a.token != b.token


# ---------------------------------------------------------------------------
# consume_login_token
# ---------------------------------------------------------------------------
def test_consume_login_token_creates_session_and_activates_member(session):
    p = _pool(session)
    m = _member(session, p, status=MemberStatus.invited)
    tok = create_login_token(session, m.user_id)
    auth_session = consume_login_token(session, tok.token)

    assert isinstance(auth_session, AuthSession)
    assert auth_session.user_id == m.user_id
    assert auth_session.revoked_at is None

    # token now marked used
    session.refresh(tok)
    assert tok.used_at is not None

    # member activated
    session.refresh(m)
    assert m.status == MemberStatus.active


def test_consume_login_token_keeps_active_member_active(session):
    p = _pool(session)
    m = _member(session, p, status=MemberStatus.active)
    tok = create_login_token(session, m.user_id)
    consume_login_token(session, tok.token)
    session.refresh(m)
    assert m.status == MemberStatus.active


def test_consume_login_token_rejects_unknown(session):
    with pytest.raises(AuthError):
        consume_login_token(session, "does-not-exist")


def test_consume_login_token_rejects_used_token(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    consume_login_token(session, tok.token)
    with pytest.raises(AuthError):
        consume_login_token(session, tok.token)


def test_consume_login_token_rejects_expired(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    tok.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    with pytest.raises(AuthError):
        consume_login_token(session, tok.token)


def test_consume_login_token_rejects_inactive_member(session):
    p = _pool(session)
    m = _member(session, p, status=MemberStatus.inactive)
    tok = create_login_token(session, m.user_id)
    with pytest.raises(AuthError):
        consume_login_token(session, tok.token)


def test_consume_login_token_session_uses_configured_ttl(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    auth_session = consume_login_token(session, tok.token)
    delta = auth_session.expires_at - auth_session.created_at
    assert abs((delta - SESSION_TTL).total_seconds()) < 1


# ---------------------------------------------------------------------------
# resolve_session / refresh_session / revoke_session
# ---------------------------------------------------------------------------
def test_resolve_session_returns_active_session(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    s = consume_login_token(session, tok.token)
    found = resolve_session(session, s.token)
    assert found is not None
    assert found.id == s.id


def test_resolve_session_returns_none_for_unknown(session):
    assert resolve_session(session, "no-such-token") is None


def test_resolve_session_returns_none_for_revoked(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    s = consume_login_token(session, tok.token)
    revoke_session(session, s.token)
    assert resolve_session(session, s.token) is None


def test_resolve_session_returns_none_for_expired(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    s = consume_login_token(session, tok.token)
    s.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert resolve_session(session, s.token) is None


def test_refresh_session_updates_last_seen(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    s = consume_login_token(session, tok.token)
    original = s.last_seen_at
    # nudge time forward via explicit `now`
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    refresh_session(session, s, now=later)
    session.refresh(s)
    assert s.last_seen_at > original


def test_revoke_session_idempotent(session):
    p = _pool(session)
    m = _member(session, p)
    tok = create_login_token(session, m.user_id)
    s = consume_login_token(session, tok.token)
    revoke_session(session, s.token)
    # second call should not raise
    revoke_session(session, s.token)
    session.refresh(s)
    assert s.revoked_at is not None


def test_revoke_session_unknown_token_is_noop(session):
    revoke_session(session, "nonexistent")  # must not raise
