"""HTTP-level tests for the contribution endpoints."""
from __future__ import annotations

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.orm import Contribution, LedgerEntry, LedgerKind


# ---------------------------------------------------------------------------
# POST /contributions — single-record
# ---------------------------------------------------------------------------
async def test_post_contribution_admin_records_and_writes_ledger(
    admin_client, session, pool, members
):
    r = await admin_client.post(
        "/contributions",
        json={
            "member_id": members[0].id,
            "amount_cents": 12_500,
            "period": "2026-W01",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount_cents"] == 12_500
    assert body["period"] == "2026-W01"
    assert "id" in body

    session.expire_all()
    assert session.query(Contribution).count() == 1
    le = session.query(LedgerEntry).one()
    assert le.kind == LedgerKind.contribution
    assert le.delta == 12_500
    assert le.balance_after == 12_500


async def test_post_contribution_unauthenticated_is_401(client, members):
    r = await client.post(
        "/contributions",
        json={"member_id": members[0].id, "amount_cents": 100, "period": "2026-W01"},
    )
    assert r.status_code == 401


async def test_post_contribution_non_admin_is_403(client, session, members):
    """Member with role=member is not allowed."""
    members[0].status = type(members[0].status).active
    session.commit()
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)

    r = await client.post(
        "/contributions",
        json={"member_id": members[1].id, "amount_cents": 100, "period": "2026-W01"},
    )
    assert r.status_code == 403


async def test_post_contribution_rejects_zero_amount(admin_client, members):
    r = await admin_client.post(
        "/contributions",
        json={"member_id": members[0].id, "amount_cents": 0, "period": "2026-W01"},
    )
    assert r.status_code == 422  # pydantic ge=1


async def test_post_contribution_rejects_bad_period(admin_client, members):
    r = await admin_client.post(
        "/contributions",
        json={"member_id": members[0].id, "amount_cents": 100, "period": "2026-13"},
    )
    assert r.status_code == 400


async def test_post_contribution_rejects_unknown_member(admin_client):
    r = await admin_client.post(
        "/contributions",
        json={"member_id": 99999, "amount_cents": 100, "period": "2026-W01"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /contributions/bulk
# ---------------------------------------------------------------------------
async def test_get_bulk_form_admin_lists_all_active_members(
    admin_client, session, pool, members
):
    r = await admin_client.get("/contributions/bulk", params={"period": "2026-W01"})
    assert r.status_code == 200
    for m in members:
        assert m.display_name in r.text
    assert 'name="period"' in r.text
    assert 'value="2026-W01"' in r.text


async def test_get_bulk_form_excludes_inactive_members(
    admin_client, session, pool, members
):
    members[1].status = type(members[1].status).inactive
    session.commit()
    r = await admin_client.get("/contributions/bulk", params={"period": "2026-W01"})
    assert r.status_code == 200
    assert members[0].display_name in r.text
    assert members[2].display_name in r.text
    assert members[1].display_name not in r.text


async def test_get_bulk_form_shows_already_recorded_amounts(
    admin_client, session, pool, admin, members
):
    # Pre-record one contribution for member[0] in 2026-01
    await admin_client.post(
        "/contributions",
        json={"member_id": members[0].id, "amount_cents": 5_000, "period": "2026-W01"},
    )
    r = await admin_client.get("/contributions/bulk", params={"period": "2026-W01"})
    assert r.status_code == 200
    assert "50.00" in r.text or "5000" in r.text  # already-recorded marker


async def test_get_bulk_form_unauthenticated_is_401(client):
    r = await client.get("/contributions/bulk")
    assert r.status_code == 401


async def test_get_bulk_form_default_period_is_current_iso_week(admin_client):
    r = await admin_client.get("/contributions/bulk")
    assert r.status_code == 200
    # Form should default to a YYYY-Www value (current ISO week).
    import re
    assert re.search(
        r'name="period"\s+value="\d{4}-W(0[1-9]|[1-4]\d|5[0-3])"', r.text
    )


# ---------------------------------------------------------------------------
# POST /contributions/bulk
# ---------------------------------------------------------------------------
async def test_post_bulk_records_multiple(admin_client, session, pool, members):
    data = {
        "period": "2026-W02",
        f"amount_{members[0].id}": "100.00",
        f"amount_{members[1].id}": "50.00",
        f"amount_{members[2].id}": "25.00",
    }
    r = await admin_client.post("/contributions/bulk", data=data)
    assert r.status_code == 200
    assert "3 recorded" in r.text or "3" in r.text  # summary shown

    session.expire_all()
    contribs = session.query(Contribution).filter_by(period="2026-W02").all()
    assert {c.amount for c in contribs} == {10_000, 5_000, 2_500}
    assert session.query(LedgerEntry).count() == 3


async def test_post_bulk_skips_blank_and_zero_rows(admin_client, session, members):
    data = {
        "period": "2026-W03",
        f"amount_{members[0].id}": "100.00",
        f"amount_{members[1].id}": "",
        f"amount_{members[2].id}": "0",
    }
    r = await admin_client.post("/contributions/bulk", data=data)
    assert r.status_code == 200
    session.expire_all()
    assert session.query(Contribution).filter_by(period="2026-W03").count() == 1


async def test_post_bulk_skips_members_already_recorded(
    admin_client, session, members
):
    # Pre-record member[0] for 2026-04
    await admin_client.post(
        "/contributions",
        json={"member_id": members[0].id, "amount_cents": 1_000, "period": "2026-W04"},
    )
    data = {
        "period": "2026-W04",
        f"amount_{members[0].id}": "100.00",
        f"amount_{members[1].id}": "50.00",
    }
    r = await admin_client.post("/contributions/bulk", data=data)
    assert r.status_code == 200
    session.expire_all()
    # member[0] still has only the original $10 contribution
    rows = session.query(Contribution).filter_by(member_id=members[0].id).all()
    assert len(rows) == 1
    assert rows[0].amount == 1_000


async def test_post_bulk_rejects_invalid_period(admin_client, members):
    data = {
        "period": "2026-13",
        f"amount_{members[0].id}": "10.00",
    }
    r = await admin_client.post("/contributions/bulk", data=data)
    assert r.status_code == 400


async def test_post_bulk_non_admin_is_403(client, session, members):
    members[0].status = type(members[0].status).active
    session.commit()
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    data = {"period": "2026-W01", f"amount_{members[1].id}": "10.00"}
    r = await client.post("/contributions/bulk", data=data)
    assert r.status_code == 403
