"""HTTP-level tests for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone

from api.claims import submit_claim
from api.contributions import record_contribution


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
async def test_root_unauthenticated_with_pool_is_401(client, pool):
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 401


async def test_root_with_no_pool_redirects_to_setup(client):
    """Brand-new install: the only thing to do is run the wizard."""
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


async def test_root_authenticated_renders_dashboard(admin_client, session, pool, admin):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=50_000, period="2026-01", recorded_by=admin.id,
    )
    r = await admin_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert pool.name in body
    assert "USD" in body
    # Balance shown as dollars (we wrote $500.00)
    assert "500.00" in body
    # Tab nav links present
    assert 'href="/models"' in body
    # Quick-action links to other pages
    assert "/claims/new" in body


async def test_root_lists_active_members_and_their_totals(
    admin_client, session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=20_000, period="2026-04", recorded_by=admin.id,
    )
    r = await admin_client.get("/")
    assert r.status_code == 200
    for m in (admin, *members):
        assert m.display_name in r.text
    # member[0]'s total $200.00 shown
    assert "200.00" in r.text
    # member[0]'s last_period column shows "2026-04"
    assert "2026-04" in r.text


async def test_root_shows_pending_claims_section(admin_client, session, pool, admin):
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="implants",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    r = await admin_client.get("/")
    assert r.status_code == 200
    assert "implants" in r.text


async def test_root_chart_renders_inline_svg(admin_client, session, pool, admin):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=10_000, period="2026-04", recorded_by=admin.id,
    )
    r = await admin_client.get("/")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "</svg>" in r.text


# ---------------------------------------------------------------------------
# GET /models
# ---------------------------------------------------------------------------
async def test_models_unauthenticated_is_401(client, pool):
    r = await client.get("/models")
    assert r.status_code == 401


async def test_models_renders_pricing_and_reserving_rationales(
    admin_client, session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-01", recorded_by=admin.id,
    )
    r = await admin_client.get("/models")
    assert r.status_code == 200
    body = r.text
    # Pricing rationale (FlatPricing always emits this)
    assert "Flat pricing" in body
    # Reserving fallback rationale (no claim history)
    assert "No claim history" in body
    # Tab nav back to overview
    assert 'href="/"' in body


async def test_models_with_no_pool_redirects_to_setup(client):
    r = await client.get("/models", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


async def test_models_shows_per_member_premium(
    admin_client, session, pool, admin, members
):
    r = await admin_client.get("/models")
    assert r.status_code == 200
    # All four eligible members appear (admin + 3 members)
    for m in (admin, *members):
        assert m.display_name in r.text
