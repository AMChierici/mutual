"""Cross-pool data isolation tests (M2).

A user who belongs to pool A but not pool B must not be able to read or
modify anything in pool B by URL tampering — we 404 rather than 403 so we
don't leak "this pool exists, you're just not in it".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.claims import submit_claim
from api.orm import Membership, MemberRole, MemberStatus, Pool, User


@pytest.fixture
def other_pool(session):
    p = Pool(
        slug="b-pool",
        name="B Pool",
        currency="EUR",
        governance_config={
            "tiers": [
                {"max_amount_cents": 10_000, "scheme": "auto_approve"},
                {"max_amount_cents": None, "scheme": "unanimous"},
            ]
        },
    )
    session.add(p)
    session.flush()
    u = User(email="b-admin@example.test", display_name="B Admin")
    session.add(u)
    session.flush()
    session.add(Membership(
        user_id=u.id, pool_id=p.id, display_name="B Admin",
        role=MemberRole.admin, status=MemberStatus.active,
    ))
    session.commit()
    return p


@pytest.fixture
def other_pool_claim(session, other_pool):
    admin = (
        session.query(Membership)
        .filter_by(pool_id=other_pool.id, role=MemberRole.admin)
        .one()
    )
    return submit_claim(
        session,
        pool_id=other_pool.id,
        member_id=admin.id,
        amount_cents=5_000,
        category="medical",
        description="b pool claim",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# A pool A member calling pool B routes gets 404, not 200 / 403.
# ---------------------------------------------------------------------------
async def test_pool_a_member_cannot_read_pool_b_dashboard(
    admin_client, pool, other_pool
):
    """admin_client is authed against `pool` (pool A) only."""
    r = await admin_client.get(f"/pools/{other_pool.slug}/", follow_redirects=False)
    assert r.status_code == 404


async def test_pool_a_member_cannot_read_pool_b_claim_list(
    admin_client, other_pool
):
    r = await admin_client.get(f"/pools/{other_pool.slug}/claims")
    assert r.status_code == 404


async def test_pool_a_member_cannot_read_pool_b_claim_detail(
    admin_client, other_pool, other_pool_claim
):
    r = await admin_client.get(
        f"/pools/{other_pool.slug}/claims/{other_pool_claim.id}"
    )
    assert r.status_code == 404


async def test_pool_a_member_cannot_read_pool_b_audit(admin_client, other_pool):
    r = await admin_client.get(f"/pools/{other_pool.slug}/audit")
    assert r.status_code == 404


async def test_pool_a_admin_cannot_post_to_pool_b_settings(
    admin_client, other_pool
):
    r = await admin_client.post(
        f"/pools/{other_pool.slug}/settings/webhook",
        data={"webhook_url": "https://example.com/hook"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Unknown pool slug always 404
# ---------------------------------------------------------------------------
async def test_unknown_pool_slug_is_404(admin_client):
    r = await admin_client.get("/pools/does-not-exist/")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# A user who's a member of both pools sees both
# ---------------------------------------------------------------------------
async def test_multi_pool_member_can_access_both(
    client, session, pool, other_pool, admin
):
    """Give the admin user a second membership in other_pool; they can now
    open both dashboards."""
    session.add(Membership(
        user_id=admin.user_id,
        pool_id=other_pool.id,
        display_name=admin.display_name,
        role=MemberRole.member,
        status=MemberStatus.active,
    ))
    session.commit()

    tok = create_login_token(session, admin.user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)

    a = await client.get(f"/pools/{pool.slug}/")
    b = await client.get(f"/pools/{other_pool.slug}/")
    assert a.status_code == 200
    assert b.status_code == 200
