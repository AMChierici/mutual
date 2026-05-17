"""HTTP-level tests for the platform-admin (cross-pool operator) view (M4).

Covers:
* require_platform_admin: regular pool admins get 403 on every /admin/*.
* The overview / pools / users views render the right cross-pool totals.
* PII redaction is on by default; flipping MUTUAL_PLATFORM_ADMIN_SEES_PII
  unredacts claim descriptions and email addresses.
* The router exposes only GET endpoints (no mutation surface in v1).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.claims import submit_claim
from api.contributions import record_contribution
from api.main import ensure_platform_admin, PLATFORM_ADMIN_EMAIL_ENV
from api.orm import Membership, MemberRole, MemberStatus, Pool, User


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------
def _make_platform_admin(session, email: str = "ops@example.test") -> User:
    """Insert a platform-admin User with no memberships, the way the
    env-var bootstrap would have done."""
    u = User(
        email=email,
        display_name="Platform Admin",
        is_platform_admin=True,
        created_at=datetime.now(timezone.utc),
    )
    session.add(u)
    session.commit()
    return u


@pytest_asyncio.fixture
async def platform_admin_client(client, session) -> AsyncClient:
    admin_user = _make_platform_admin(session)
    tok = create_login_token(session, admin_user.id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


@pytest.fixture
def pii_off(monkeypatch):
    monkeypatch.delenv("MUTUAL_PLATFORM_ADMIN_SEES_PII", raising=False)


@pytest.fixture
def pii_on(monkeypatch):
    monkeypatch.setenv("MUTUAL_PLATFORM_ADMIN_SEES_PII", "1")


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
async def test_admin_unauthenticated_is_401(client):
    r = await client.get("/admin")
    assert r.status_code == 401


async def test_admin_pool_admin_without_platform_flag_is_403(admin_client):
    """A regular pool admin (no is_platform_admin) gets 403 across /admin/*."""
    for path in (
        "/admin",
        "/admin/pools",
        "/admin/users",
    ):
        r = await admin_client.get(path)
        assert r.status_code == 403, f"{path} expected 403, got {r.status_code}"


async def test_admin_platform_admin_can_open_overview(
    platform_admin_client, pool
):
    r = await platform_admin_client.get("/admin")
    assert r.status_code == 200
    assert "Platform admin" in r.text
    assert pool.name in r.text


# ---------------------------------------------------------------------------
# Overview totals
# ---------------------------------------------------------------------------
async def test_overview_shows_pool_balance_and_member_count(
    platform_admin_client, session, pool, admin
):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=12_345, period="2026-W01", recorded_by=admin.id,
    )
    r = await platform_admin_client.get("/admin")
    assert r.status_code == 200
    body = r.text
    assert pool.name in body
    # Balance shown as dollars
    assert "123.45" in body
    # Member count shown — admin is one member
    assert "1" in body


# ---------------------------------------------------------------------------
# Pools listing + deep view
# ---------------------------------------------------------------------------
async def test_pools_list_renders_all_pools(
    platform_admin_client, session, pool
):
    second = Pool(slug="second-pool", name="Second", currency="EUR", governance_config={})
    session.add(second)
    session.commit()

    r = await platform_admin_client.get("/admin/pools")
    assert r.status_code == 200
    assert pool.name in r.text
    assert second.name in r.text


async def test_pool_detail_pii_off_redacts_claim_description(
    platform_admin_client, session, pool, admin, pii_off
):
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="VERY SENSITIVE DESC",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    r = await platform_admin_client.get(f"/admin/pools/{pool.id}")
    assert r.status_code == 200
    assert "VERY SENSITIVE DESC" not in r.text
    assert "[redacted]" in r.text


async def test_pool_detail_pii_on_shows_claim_description(
    platform_admin_client, session, pool, admin, pii_on
):
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="VERY SENSITIVE DESC",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    r = await platform_admin_client.get(f"/admin/pools/{pool.id}")
    assert r.status_code == 200
    assert "VERY SENSITIVE DESC" in r.text


async def test_pool_detail_pii_off_redacts_email_to_domain(
    platform_admin_client, session, pool, admin, pii_off
):
    admin_user = session.get(User, admin.user_id)
    admin_user.email = "secret-handle@example.com"
    session.commit()
    r = await platform_admin_client.get(f"/admin/pools/{pool.id}")
    assert r.status_code == 200
    assert "secret-handle@example.com" not in r.text
    assert "***@example.com" in r.text


async def test_pool_detail_unknown_pool_is_404(platform_admin_client):
    r = await platform_admin_client.get("/admin/pools/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Users listing + deep view
# ---------------------------------------------------------------------------
async def test_users_list_shows_all_users(
    platform_admin_client, session, admin, members
):
    r = await platform_admin_client.get("/admin/users")
    assert r.status_code == 200
    # admin, members[0..2], and the platform admin all show up
    assert "Admin" in r.text
    assert "Bo" in r.text


async def test_user_detail_shows_membership_list_and_sessions(
    platform_admin_client, session, pool, admin
):
    r = await platform_admin_client.get(f"/admin/users/{admin.user_id}")
    assert r.status_code == 200
    assert pool.name in r.text  # membership row links to pool
    # Admin has at least one recent magic-link minted at fixture time.
    assert "magic links" in r.text.lower() or "Created" in r.text


async def test_user_detail_unknown_user_is_404(platform_admin_client):
    r = await platform_admin_client.get("/admin/users/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Mutation surface: there is none.
# ---------------------------------------------------------------------------
async def test_admin_post_paths_are_405(platform_admin_client):
    """No /admin route accepts POST in v1 — that's the read-only contract."""
    for path in ("/admin", "/admin/pools", "/admin/users", "/admin/pools/1", "/admin/users/1"):
        r = await platform_admin_client.post(path, data={"any": "x"})
        # Either 405 (method not allowed) or 404 (no such resource) — neither
        # mutates state.
        assert r.status_code in (404, 405), (
            f"{path} POST returned {r.status_code}; admin should be read-only"
        )


# ---------------------------------------------------------------------------
# Bootstrap: ensure_platform_admin sets the flag from env
# ---------------------------------------------------------------------------
def test_ensure_platform_admin_creates_user_when_missing(
    session, app_with_db, monkeypatch
):
    monkeypatch.setenv(PLATFORM_ADMIN_EMAIL_ENV, "bootstrap@example.test")
    ensure_platform_admin(app_with_db.state.session_factory)
    session.expire_all()
    u = session.query(User).filter_by(email="bootstrap@example.test").one()
    assert u.is_platform_admin is True


def test_ensure_platform_admin_promotes_existing_user(
    session, app_with_db, monkeypatch
):
    existing = User(
        email="existing@example.test",
        display_name="Existing",
        is_platform_admin=False,
        created_at=datetime.now(timezone.utc),
    )
    session.add(existing)
    session.commit()

    monkeypatch.setenv(PLATFORM_ADMIN_EMAIL_ENV, "existing@example.test")
    ensure_platform_admin(app_with_db.state.session_factory)
    session.expire_all()
    refreshed = session.get(User, existing.id)
    assert refreshed.is_platform_admin is True


def test_ensure_platform_admin_no_env_is_noop(session, app_with_db, monkeypatch):
    monkeypatch.delenv(PLATFORM_ADMIN_EMAIL_ENV, raising=False)
    ensure_platform_admin(app_with_db.state.session_factory)
    session.expire_all()
    assert session.query(User).filter_by(is_platform_admin=True).count() == 0


# ---------------------------------------------------------------------------
# A platform admin who is also a pool member can still hit pool views
# ---------------------------------------------------------------------------
async def test_platform_admin_can_also_be_pool_member(
    client, session, pool
):
    """The two roles compose — the platform flag doesn't grant pool access,
    but it doesn't block it either."""
    u = _make_platform_admin(session, email="dual@example.test")
    session.add(Membership(
        user_id=u.id, pool_id=pool.id, display_name="Dual",
        role=MemberRole.member, status=MemberStatus.active,
    ))
    session.commit()
    tok = create_login_token(session, u.id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)

    # Both surfaces work.
    r1 = await client.get("/admin")
    r2 = await client.get(f"/pools/{pool.slug}/")
    assert r1.status_code == 200
    assert r2.status_code == 200
