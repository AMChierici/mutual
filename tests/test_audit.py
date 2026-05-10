"""Tests for audit event emissions, the listing service, and /audit."""
from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient

from api.audit import list_audit_events
from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.contributions import record_contribution
from api.orm import AuditEvent, MemberStatus


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


# ---------------------------------------------------------------------------
# Audit emissions for the auth flow
# ---------------------------------------------------------------------------
async def test_post_magic_link_emits_audit_event(admin_client, session, members):
    r = await admin_client.post(
        "/auth/magic-link", json={"member_id": members[0].id}
    )
    assert r.status_code == 200
    session.expire_all()
    audit = (
        session.query(AuditEvent)
        .filter_by(kind="auth.magic_link_minted")
        .one()
    )
    assert audit.payload_json["target_member_id"] == members[0].id
    # actor_member_id is the admin who minted the link
    from api.orm import Member, MemberRole
    actor = session.get(Member, audit.actor_member_id)
    assert actor.role == MemberRole.admin


async def test_login_emits_audit_event(client, session, members):
    members[0].status = MemberStatus.active
    session.commit()
    tok = create_login_token(session, members[0].id)
    r = await client.get(f"/auth/login/{tok.token}")
    assert r.status_code == 200
    session.expire_all()
    audit = session.query(AuditEvent).filter_by(kind="auth.login").one()
    assert audit.actor_member_id == members[0].id


async def test_logout_emits_audit_event(member_client, session, members):
    r = await member_client.post("/auth/logout")
    assert r.status_code == 200
    session.expire_all()
    audit = session.query(AuditEvent).filter_by(kind="auth.logout").one()
    assert audit.actor_member_id == members[0].id


async def test_logout_when_not_logged_in_does_not_emit_audit(client, session):
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    session.expire_all()
    assert session.query(AuditEvent).filter_by(kind="auth.logout").count() == 0


# ---------------------------------------------------------------------------
# Service: list_audit_events
# ---------------------------------------------------------------------------
def test_list_audit_events_newest_first(session, pool, admin):
    # Create several events with implicit ordering via insert order; service
    # orders by recorded_at desc.
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=1_000, period="2026-01", recorded_by=admin.id,
    )
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=2_000, period="2026-02", recorded_by=admin.id,
    )
    events = list_audit_events(session, pool.id)
    assert len(events) >= 2
    # ordering check: each adjacent pair has recorded_at non-increasing
    for prev, nxt in zip(events, events[1:]):
        assert prev.recorded_at >= nxt.recorded_at


def test_list_audit_events_caps_at_limit(session, pool, admin):
    for i in range(10):
        record_contribution(
            session, pool_id=pool.id, member_id=admin.id,
            amount_cents=100, period=f"2026-{i+1:02d}" if i < 9 else "2026-10",
            recorded_by=admin.id,
        )
    events = list_audit_events(session, pool.id, limit=5)
    assert len(events) == 5


def test_list_audit_events_filters_to_pool(session, pool, admin):
    """Insert an audit event for a different pool; the listing must skip it."""
    from api.orm import Pool
    other = Pool(name="O", currency="USD", governance_config={})
    session.add(other)
    session.commit()
    session.add(
        AuditEvent(
            pool_id=other.id,
            actor_member_id=None,
            kind="other.event",
            payload_json={"x": 1},
        )
    )
    session.commit()
    events = list_audit_events(session, pool.id)
    assert all(e.pool_id == pool.id for e in events)


# ---------------------------------------------------------------------------
# Route: GET /audit
# ---------------------------------------------------------------------------
async def test_get_audit_unauthenticated_is_401(client, pool):
    r = await client.get("/audit")
    assert r.status_code == 401


async def test_get_audit_renders_events(admin_client, session, pool, admin):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=12_500, period="2026-01", recorded_by=admin.id,
    )
    r = await admin_client.get("/audit")
    assert r.status_code == 200
    body = r.text
    assert "contribution.recorded" in body
    assert admin.display_name in body  # actor name shown
    assert "12500" in body or "125.00" in body  # payload visible


async def test_get_audit_handles_null_actor(admin_client, session, pool):
    """System-originated events have actor_member_id=None."""
    session.add(
        AuditEvent(
            pool_id=pool.id,
            actor_member_id=None,
            kind="system.startup",
            payload_json={"note": "boot"},
        )
    )
    session.commit()
    r = await admin_client.get("/audit")
    assert r.status_code == 200
    assert "system.startup" in r.text


async def test_get_audit_member_can_see(member_client, session, pool, admin):
    """Mutual aid transparency — any active member can read the audit log."""
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100, period="2026-01", recorded_by=admin.id,
    )
    r = await member_client.get("/audit")
    assert r.status_code == 200
    assert "contribution.recorded" in r.text


async def test_get_audit_includes_each_lifecycle_kind_after_full_run(
    admin_client, session, pool, admin
):
    """Smoke: after a real-ish flow we should see the major event kinds."""
    from datetime import datetime, timezone

    from api.claims import submit_claim
    from api.payouts import record_payout
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-01", recorded_by=admin.id,
    )
    claim = submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000, recorded_by=admin.id,
    )
    r = await admin_client.get("/audit")
    body = r.text
    for kind in (
        "contribution.recorded",
        "claim.submitted",
        "payout.recorded",
    ):
        assert kind in body
