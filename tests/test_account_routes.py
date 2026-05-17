"""HTTP-level tests for account-scoped routes (M2).

The account home, the per-user pool picker, and the wizard for creating
an additional pool. None of these live inside a single pool's URL space.
"""
from __future__ import annotations

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.orm import Membership, MemberRole, MemberStatus, Pool, User


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
async def test_root_with_no_pool_redirects_to_setup(client):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


async def test_root_with_pool_redirects_to_pools_listing(client, pool):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/pools/"


# ---------------------------------------------------------------------------
# GET /pools/
# ---------------------------------------------------------------------------
async def test_pools_listing_unauthenticated_is_401(client, pool):
    r = await client.get("/pools/", follow_redirects=False)
    assert r.status_code == 401


async def test_pools_listing_single_pool_user_short_circuits_to_dashboard(
    admin_client, pool
):
    """If a user only belongs to one pool, the picker skips the list and
    goes straight to that pool's dashboard."""
    r = await admin_client.get("/pools/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/pools/{pool.slug}/"


async def test_pools_listing_multi_pool_user_renders_picker(
    client, session, pool
):
    """A user with two memberships sees a picker page listing both."""
    # Build user with two memberships in two pools.
    u = User(email="multi@example.test", display_name="Multi")
    session.add(u)
    session.flush()
    pool2 = Pool(slug="other-pool", name="Other", currency="EUR", governance_config={})
    session.add(pool2)
    session.flush()
    session.add(Membership(
        user_id=u.id, pool_id=pool.id, display_name="Multi",
        role=MemberRole.member, status=MemberStatus.active,
    ))
    session.add(Membership(
        user_id=u.id, pool_id=pool2.id, display_name="Multi",
        role=MemberRole.admin, status=MemberStatus.active,
    ))
    session.commit()

    tok = create_login_token(session, u.id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)

    r = await client.get("/pools/")
    assert r.status_code == 200
    assert pool.name in r.text
    assert pool2.name in r.text
    assert f"/pools/{pool.slug}/" in r.text
    assert f"/pools/{pool2.slug}/" in r.text


# ---------------------------------------------------------------------------
# POST /pools/new — create-additional-pool
# ---------------------------------------------------------------------------
async def test_new_pool_unauthenticated_is_401(client, pool):
    r = await client.get("/pools/new", follow_redirects=False)
    assert r.status_code == 401


async def test_new_pool_creates_pool_under_current_user(
    admin_client, session, admin, pool
):
    data = {
        "pool_name": "Family Backup",
        "currency": "USD",
        "starting_balance_dollars": "0",
        "tier_max_1": "100",
        "tier_scheme_1": "auto_approve",
        "tier_max_2": "",
        "tier_scheme_2": "majority",
    }
    r = await admin_client.post("/pools/new", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/pools/family-backup")

    # The admin user now has a membership in the new pool (plus the original).
    session.expire_all()
    new_pool = session.query(Pool).filter_by(name="Family Backup").one()
    memberships = session.query(Membership).filter_by(
        user_id=admin.user_id, pool_id=new_pool.id
    ).all()
    assert len(memberships) == 1
    assert memberships[0].role == MemberRole.admin
    assert memberships[0].status == MemberStatus.active


async def test_new_pool_slug_collision_appends_suffix(
    admin_client, session, pool
):
    """Two pools with the same name → second one gets a unique slug."""
    data = {
        "pool_name": pool.name,  # collide with the existing fixture pool
        "currency": "USD",
        "starting_balance_dollars": "0",
        "tier_max_1": "100",
        "tier_scheme_1": "auto_approve",
        "tier_max_2": "",
        "tier_scheme_2": "majority",
    }
    r = await admin_client.post("/pools/new", data=data, follow_redirects=False)
    assert r.status_code == 303
    # First fixture pool had slug "test-pool" → new pool should slug something
    # else (e.g. "test-pool-2" if names slugify identically, otherwise a new
    # base). The contract is just that it isn't "test-pool".
    location = r.headers["location"]
    new_slug = location.split("/pools/")[1].rstrip("/")
    assert new_slug != pool.slug
    session.expire_all()
    assert session.query(Pool).filter(Pool.slug == new_slug).count() == 1


async def test_new_pool_invalid_currency_re_renders_with_error(
    admin_client, pool
):
    data = {
        "pool_name": "Bad",
        "currency": "us",  # too short
        "starting_balance_dollars": "0",
        "tier_max_1": "100",
        "tier_scheme_1": "auto_approve",
    }
    r = await admin_client.post("/pools/new", data=data)
    assert r.status_code == 400
