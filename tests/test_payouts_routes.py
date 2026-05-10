"""HTTP-level tests for marking approved claims as paid."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.claims import submit_claim
from api.contributions import record_contribution
from api.orm import Claim, ClaimStatus, LedgerEntry, LedgerKind, Payout


@pytest.fixture
def funded_pool(session, pool, admin):
    """Seed the pool with $1000 so payouts have something to draw from."""
    record_contribution(
        session,
        pool_id=pool.id,
        member_id=admin.id,
        amount_cents=100_000,
        period="2026-W01",
        recorded_by=admin.id,
    )
    return pool


@pytest.fixture
def approved_claim(session, funded_pool, admin):
    return submit_claim(
        session,
        pool_id=funded_pool.id,
        member_id=admin.id,
        amount_cents=5_000,  # auto_approve tier in fixture config
        category="medical",
        description="prescription",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


# ---------------------------------------------------------------------------
# POST /claims/{id}/pay
# ---------------------------------------------------------------------------
async def test_post_pay_admin_records_payout_and_redirects(
    admin_client, session, approved_claim
):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={
            "amount_dollars": "50.00",
            "paid_date": "2026-05-01",
            "notes": "Venmo",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/claims/{approved_claim.id}"

    session.expire_all()
    p = session.query(Payout).filter_by(claim_id=approved_claim.id).one()
    assert p.amount_paid == 5_000
    assert p.notes == "Venmo"
    fresh = session.get(Claim, approved_claim.id)
    assert fresh.status == ClaimStatus.paid


async def test_post_pay_writes_negative_ledger_entry(
    admin_client, session, approved_claim
):
    await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "50", "paid_date": "2026-05-01"},
    )
    session.expire_all()
    le = session.query(LedgerEntry).filter_by(kind=LedgerKind.payout).one()
    assert le.delta == -5_000
    assert le.balance_after == 95_000  # $1000 seed - $50


async def test_post_pay_unauthenticated_is_401(client, approved_claim):
    r = await client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "50"},
    )
    assert r.status_code == 401


async def test_post_pay_non_admin_is_403(member_client, approved_claim):
    r = await member_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "50"},
    )
    assert r.status_code == 403


async def test_post_pay_unknown_claim_is_400(admin_client):
    r = await admin_client.post(
        "/claims/99999/pay",
        data={"amount_dollars": "50"},
    )
    assert r.status_code == 400


async def test_post_pay_non_approved_claim_is_400(
    admin_client, session, funded_pool, admin
):
    voting_claim = submit_claim(
        session,
        pool_id=funded_pool.id,
        member_id=admin.id,
        amount_cents=80_000,  # majority tier -> voting
        category="dental",
        description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    r = await admin_client.post(
        f"/claims/{voting_claim.id}/pay",
        data={"amount_dollars": "800"},
    )
    assert r.status_code == 400


async def test_post_pay_zero_amount_is_400(admin_client, approved_claim):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "0"},
    )
    assert r.status_code == 400


async def test_post_pay_overdraw_is_400(admin_client, session, approved_claim):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "5000.00"},  # $5000 > $1000 in pool
    )
    assert r.status_code == 400
    session.expire_all()
    assert session.query(Payout).count() == 0


async def test_post_pay_default_amount_is_requested_when_blank(
    admin_client, session, approved_claim
):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "", "paid_date": "2026-05-01"},
    )
    assert r.status_code == 303
    session.expire_all()
    p = session.query(Payout).one()
    assert p.amount_paid == approved_claim.amount_requested  # 5_000


async def test_post_pay_default_paid_date_is_now_when_blank(
    admin_client, session, approved_claim
):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "50", "paid_date": ""},
    )
    assert r.status_code == 303
    session.expire_all()
    p = session.query(Payout).one()
    assert p.paid_at is not None


async def test_post_pay_partial_amount_allowed(
    admin_client, session, approved_claim
):
    r = await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "30.00", "paid_date": "2026-05-01"},
    )
    assert r.status_code == 303
    session.expire_all()
    p = session.query(Payout).one()
    assert p.amount_paid == 3_000
    fresh = session.get(Claim, approved_claim.id)
    assert fresh.status == ClaimStatus.paid


# ---------------------------------------------------------------------------
# Detail page integration
# ---------------------------------------------------------------------------
async def test_detail_admin_sees_pay_form_when_approved(
    admin_client, approved_claim
):
    r = await admin_client.get(f"/claims/{approved_claim.id}")
    assert r.status_code == 200
    assert "Mark as paid" in r.text or "amount_dollars" in r.text


async def test_detail_member_does_not_see_pay_form(
    member_client, approved_claim
):
    r = await member_client.get(f"/claims/{approved_claim.id}")
    assert r.status_code == 200
    assert "Mark as paid" not in r.text


async def test_detail_shows_payout_details_when_paid(
    admin_client, session, approved_claim
):
    await admin_client.post(
        f"/claims/{approved_claim.id}/pay",
        data={"amount_dollars": "50", "paid_date": "2026-05-01", "notes": "Wire"},
    )
    r = await admin_client.get(f"/claims/{approved_claim.id}")
    assert r.status_code == 200
    assert "Wire" in r.text  # notes shown
    assert "paid" in r.text.lower()
    assert "Mark as paid" not in r.text  # form hidden after payment
