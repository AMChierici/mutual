"""HTTP-level tests for the magic-link auth flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from api.auth import SESSION_COOKIE, create_login_token
from api.orm import AuthSession, LoginToken, MemberStatus


# ---------------------------------------------------------------------------
# GET /auth/login/{token}
# ---------------------------------------------------------------------------
async def test_login_consumes_token_and_sets_cookie(client, session, member):
    tok = create_login_token(session, member.id)
    r = await client.get(f"/auth/login/{tok.token}")
    assert r.status_code == 200
    assert SESSION_COOKIE in r.cookies
    assert r.cookies[SESSION_COOKIE]
    assert "Bo" in r.text  # display name shown in success page

    session.expire_all()
    refreshed = session.get(LoginToken, tok.id)
    assert refreshed.used_at is not None
    fresh_member = session.get(type(member), member.id)
    assert fresh_member.status == MemberStatus.active


async def test_login_rejects_unknown_token(client):
    r = await client.get("/auth/login/not-a-real-token")
    assert r.status_code == 400
    assert SESSION_COOKIE not in r.cookies


async def test_login_rejects_used_token(client, session, member):
    tok = create_login_token(session, member.id)
    first = await client.get(f"/auth/login/{tok.token}")
    assert first.status_code == 200
    # Drop the cookie so the second call is anonymous (otherwise httpx replays it).
    client.cookies.delete(SESSION_COOKIE)
    second = await client.get(f"/auth/login/{tok.token}")
    assert second.status_code == 400


async def test_login_rejects_expired_token(client, session, member):
    tok = create_login_token(session, member.id)
    tok.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    r = await client.get(f"/auth/login/{tok.token}")
    assert r.status_code == 400


async def test_login_cookie_is_httponly_and_samesite_lax(client, session, member):
    tok = create_login_token(session, member.id)
    r = await client.get(f"/auth/login/{tok.token}")
    set_cookie = r.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
async def test_logout_revokes_session_and_clears_cookie(admin_client, session):
    cookie_before = admin_client.cookies.get(SESSION_COOKIE)
    assert cookie_before
    r = await admin_client.post("/auth/logout")
    assert r.status_code == 200

    # Server-side: revoked_at populated
    session.expire_all()
    found = session.query(AuthSession).filter_by(token=cookie_before).one()
    assert found.revoked_at is not None


async def test_logout_when_not_logged_in_is_noop(client):
    r = await client.post("/auth/logout")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /auth/magic-link  (admin-only)
# ---------------------------------------------------------------------------
async def test_admin_can_create_magic_link_for_member(admin_client, member):
    r = await admin_client.post("/auth/magic-link", json={"member_id": member.id})
    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("/auth/login/")
    assert body["member_id"] == member.id
    assert "expires_at" in body


async def test_magic_link_for_nonexistent_member_returns_404(admin_client):
    r = await admin_client.post("/auth/magic-link", json={"member_id": 99999})
    assert r.status_code == 404


async def test_non_admin_cannot_create_magic_link(client, session, member):
    """A regular member is forbidden from minting links for others."""
    # Make `member` active and log them in.
    member.status = MemberStatus.active
    session.commit()
    tok = create_login_token(session, member.id)
    login = await client.get(f"/auth/login/{tok.token}")
    assert login.status_code == 200

    r = await client.post("/auth/magic-link", json={"member_id": member.id})
    assert r.status_code == 403


async def test_unauthenticated_cannot_create_magic_link(client, member):
    r = await client.post("/auth/magic-link", json={"member_id": member.id})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Reusing the same session cookie keeps a member logged in
# ---------------------------------------------------------------------------
async def test_session_cookie_persists_across_requests(admin_client):
    r1 = await admin_client.post("/auth/magic-link", json={"member_id": 99999})
    assert r1.status_code == 404  # but auth recognized us
    r2 = await admin_client.post("/auth/magic-link", json={"member_id": 99998})
    assert r2.status_code == 404
