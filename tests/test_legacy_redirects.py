"""Legacy single-pool URLs 302 to the new /pools/{slug}/... shape (M2).

A v0 install bookmarked /claims, /audit, /settings, etc. After M2 those
paths get rewritten to /pools/{first_membership.pool.slug}/... for the
currently logged-in user. Anonymous or no-pool requests fall through to
the normal 401/404 handling.
"""
from __future__ import annotations


async def test_legacy_claims_redirects_for_logged_in_user(admin_client, pool):
    r = await admin_client.get("/claims", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pools/{pool.slug}/claims"


async def test_legacy_claim_detail_redirects_with_id_preserved(
    admin_client, pool
):
    r = await admin_client.get("/claims/42", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pools/{pool.slug}/claims/42"


async def test_legacy_audit_redirects_for_logged_in_user(admin_client, pool):
    r = await admin_client.get("/audit", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pools/{pool.slug}/audit"


async def test_legacy_settings_redirects_for_logged_in_user(admin_client, pool):
    r = await admin_client.get("/settings", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pools/{pool.slug}/settings"


async def test_legacy_contributions_redirects_with_query(admin_client, pool):
    r = await admin_client.get(
        "/contributions/bulk?period=2026-W01", follow_redirects=False
    )
    assert r.status_code == 303
    assert (
        r.headers["location"]
        == f"/pools/{pool.slug}/contributions/bulk?period=2026-W01"
    )


async def test_legacy_models_redirects_for_logged_in_user(admin_client, pool):
    r = await admin_client.get("/models", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/pools/{pool.slug}/models"


async def test_legacy_path_for_anonymous_falls_through_to_404_or_401(
    client, pool
):
    """No session → middleware doesn't rewrite, so the request hits the
    normal handler which has no route at /claims anymore — 404."""
    r = await client.get(
        "/claims", follow_redirects=False, headers={"accept": "*/*"}
    )
    assert r.status_code == 404


async def test_legacy_path_for_user_with_no_active_membership_falls_through(
    client, session, pool
):
    """A logged-in user with no active membership in any pool gets the
    same 404 — the rewrite is keyed on having a pool to redirect into."""
    from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
    from api.orm import User
    u = User(email="orphan@example.test", display_name="Orphan", is_platform_admin=True)
    session.add(u)
    session.commit()
    tok = create_login_token(session, u.id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)

    r = await client.get(
        "/claims", follow_redirects=False, headers={"accept": "*/*"}
    )
    assert r.status_code == 404
