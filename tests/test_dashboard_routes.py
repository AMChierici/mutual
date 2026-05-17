"""HTTP-level tests for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone

from api.claims import submit_claim
from api.contributions import record_contribution


# ---------------------------------------------------------------------------
# GET /pools/{slug}/  (and the legacy / redirect)
# ---------------------------------------------------------------------------
async def test_root_unauthenticated_with_pool_redirects_to_login_html(client, pool):
    """Account home now lives at `/`; logged-out callers redirect to login."""
    r = await client.get(
        "/", follow_redirects=False, headers={"accept": "text/html"}
    )
    # `/` first 303s to `/pools/`, which requires auth and itself 303s to login.
    assert r.status_code in (302, 303, 307)


async def test_root_with_no_pool_redirects_to_setup(client):
    """Brand-new install: the only thing to do is run the wizard."""
    r = await client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


async def test_pool_root_authenticated_renders_dashboard(
    admin_client, session, pool, admin
):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=50_000, period="2026-W01", recorded_by=admin.id,
    )
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    body = r.text
    assert pool.name in body
    assert "USD" in body
    # Balance shown as dollars (we wrote $500.00)
    assert "500.00" in body
    # Tab nav links present
    assert f'/pools/{pool.slug}/models' in body
    # Quick-action links to other pages
    assert f"/pools/{pool.slug}/claims/new" in body


async def test_pool_root_lists_active_members_and_their_totals(
    admin_client, session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=20_000, period="2026-W04", recorded_by=admin.id,
    )
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    for m in (admin, *members):
        assert m.display_name in r.text
    # member[0]'s total $200.00 shown
    assert "200.00" in r.text
    # member[0]'s last_period column shows "2026-04"
    assert "2026-04" in r.text


async def test_pool_root_shows_pending_claims_section(
    admin_client, session, pool, admin
):
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="implants",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    assert "implants" in r.text


async def test_pool_root_chart_renders_inline_svg(admin_client, session, pool, admin):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=10_000, period="2026-W04", recorded_by=admin.id,
    )
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "</svg>" in r.text


async def test_pool_root_default_bucket_is_period(admin_client, session, pool, admin):
    """Backfilling 5 historical periods today should show 5 columns under
    the default view, not one tall column."""
    today = datetime.now(timezone.utc)
    for i, period in enumerate(("2026-W02", "2026-W06", "2026-W11", "2026-W15", "2026-W19")):
        record_contribution(
            session, pool_id=pool.id, member_id=admin.id,
            amount_cents=10_000 * (i + 1), period=period,
            recorded_by=admin.id, now=today,
        )
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    # Five distinct non-zero contribution bars (one per period).
    for amount in ("100.00", "200.00", "300.00", "400.00", "500.00"):
        assert f"in {amount}" in r.text


async def test_pool_root_bucket_recorded_at_query_stacks_into_today(
    admin_client, session, pool, admin
):
    today = datetime.now(timezone.utc)
    for i, period in enumerate(("2026-W02", "2026-W06", "2026-W11", "2026-W15", "2026-W19")):
        record_contribution(
            session, pool_id=pool.id, member_id=admin.id,
            amount_cents=10_000 * (i + 1), period=period,
            recorded_by=admin.id, now=today,
        )
    r = await admin_client.get(f"/pools/{pool.slug}/?bucket=recorded_at")
    assert r.status_code == 200
    # Total $1500 stacks into a single column under the recorded_at view.
    assert "in 1500.00" in r.text


async def test_pool_root_renders_bucket_toggle_links(
    admin_client, session, pool, admin
):
    r = await admin_client.get(f"/pools/{pool.slug}/")
    assert r.status_code == 200
    # Two toggle controls present, default is "period".
    assert "By period" in r.text
    assert "By recorded date" in r.text
    # Recorded-date is a link (not active)
    assert f'href="/pools/{pool.slug}/?bucket=recorded_at"' in r.text


async def test_pool_root_invalid_bucket_query_falls_back_to_default(
    admin_client, pool
):
    r = await admin_client.get(f"/pools/{pool.slug}/?bucket=garbage")
    assert r.status_code == 200
    # Doesn't 400; falls back gracefully.


# ---------------------------------------------------------------------------
# GET /pools/{slug}/models
# ---------------------------------------------------------------------------
async def test_models_unauthenticated_is_401(client, pool):
    r = await client.get(f"/pools/{pool.slug}/models")
    assert r.status_code == 401


async def test_models_renders_pricing_and_reserving_rationales(
    admin_client, session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-W01", recorded_by=admin.id,
    )
    r = await admin_client.get(f"/pools/{pool.slug}/models")
    assert r.status_code == 200
    body = r.text
    # Pricing rationale (FlatPricing always emits this)
    assert "Flat pricing" in body
    # Reserving fallback rationale (no claim history)
    assert "No claim history" in body
    # Tab nav back to overview
    assert f'href="/pools/{pool.slug}/"' in body


async def test_models_for_unknown_pool_is_404(client):
    """No legacy redirect for a missing pool slug — just 404 from get_pool_from_slug."""
    r = await client.get("/pools/does-not-exist/models", follow_redirects=False)
    assert r.status_code == 404


async def test_models_shows_per_member_premium(
    admin_client, session, pool, admin, members
):
    r = await admin_client.get(f"/pools/{pool.slug}/models")
    assert r.status_code == 200
    # All four eligible members appear (admin + 3 members)
    for m in (admin, *members):
        assert m.display_name in r.text
