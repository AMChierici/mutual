"""HTTP-level tests for the first-run setup wizard."""
from __future__ import annotations

from api.auth import SESSION_COOKIE
from api.orm import (
    AuthSession,
    LedgerEntry,
    LedgerKind,
    LoginToken,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
)


def _form(**overrides) -> dict:
    """Default valid wizard payload as form-encoded dict."""
    base = {
        "pool_name": "Family",
        "currency": "USD",
        "starting_balance_dollars": "500.00",
        "member_name_0": "Ada",
        "member_email_0": "ada@example.com",
        "member_role_0": "admin",
        "member_name_1": "Bo",
        "member_email_1": "",
        "member_role_1": "member",
        "policy_template_id": "family",
        "policy_text": "We cover unexpected medical and dental costs.",
        "tier_max_0": "100",
        "tier_scheme_0": "auto_approve",
        "tier_max_1": "1000",
        "tier_scheme_1": "majority",
        "tier_max_2": "",
        "tier_scheme_2": "unanimous",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# GET /setup
# ---------------------------------------------------------------------------
async def test_get_setup_renders_wizard_when_no_pool(client):
    r = await client.get("/setup")
    assert r.status_code == 200
    assert "Set up your pool" in r.text
    assert "policy_text" in r.text  # field present


async def test_get_setup_redirects_when_pool_exists(client, pool):
    r = await client.get("/setup", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# POST /setup
# ---------------------------------------------------------------------------
async def test_post_setup_creates_pool_admin_and_session(client, session):
    r = await client.post("/setup", data=_form())
    assert r.status_code == 200, r.text
    assert "Setup complete" in r.text or "Welcome" in r.text or "/auth/login/" in r.text

    session.expire_all()
    pool = session.query(Pool).one()
    assert pool.name == "Family"
    assert pool.currency == "USD"
    assert "medical" in pool.policy_text
    assert pool.governance_config["tiers"][0]["scheme"] == "auto_approve"
    assert pool.governance_config["tiers"][0]["max_amount_cents"] == 10_000  # $100 -> cents
    assert pool.governance_config["tiers"][-1]["max_amount_cents"] is None

    members = session.query(Member).order_by(Member.id).all()
    assert len(members) == 2
    assert members[0].role == MemberRole.admin
    assert members[0].status == MemberStatus.active
    assert members[1].role == MemberRole.member
    assert members[1].status == MemberStatus.invited


async def test_post_setup_sets_session_cookie_and_logs_admin_in(client, session):
    r = await client.post("/setup", data=_form())
    assert r.status_code == 200
    assert SESSION_COOKIE in r.cookies
    cookie = r.cookies[SESSION_COOKIE]

    # The cookie corresponds to a real, unrevoked AuthSession
    session.expire_all()
    s = session.query(AuthSession).filter_by(token=cookie).one()
    assert s.revoked_at is None

    # And the admin can hit an admin-protected endpoint without re-logging-in
    member = session.query(Member).filter_by(role=MemberRole.member).one()
    r2 = await client.post("/auth/magic-link", json={"member_id": member.id})
    assert r2.status_code == 200


async def test_post_setup_writes_opening_balance_when_amount_positive(client, session):
    r = await client.post("/setup", data=_form(starting_balance_dollars="500.00"))
    assert r.status_code == 200

    session.expire_all()
    entries = session.query(LedgerEntry).all()
    assert len(entries) == 1
    assert entries[0].kind == LedgerKind.opening_balance
    assert entries[0].delta == 50_000  # $500.00 in cents
    assert entries[0].balance_after == 50_000


async def test_post_setup_skips_ledger_when_balance_zero(client, session):
    r = await client.post("/setup", data=_form(starting_balance_dollars="0"))
    assert r.status_code == 200
    session.expire_all()
    assert session.query(LedgerEntry).count() == 0


async def test_post_setup_persists_login_link(client, session):
    r = await client.post("/setup", data=_form())
    assert r.status_code == 200
    assert "/auth/login/" in r.text  # backup URL shown to admin
    session.expire_all()
    assert session.query(LoginToken).count() == 1


async def test_post_setup_renders_form_with_errors_on_invalid_input(client, session):
    bad = _form(member_role_0="member", member_role_1="member")  # no admin
    r = await client.post("/setup", data=bad)
    assert r.status_code == 400
    assert "admin" in r.text.lower()
    session.expire_all()
    assert session.query(Pool).count() == 0


async def test_post_setup_when_already_setup_returns_409(client, pool):
    r = await client.post("/setup", data=_form())
    assert r.status_code == 409


async def test_post_setup_skips_blank_member_rows(client, session):
    """Admin may leave optional rows empty; those should be ignored, not errored."""
    payload = _form(member_name_2="", member_email_2="", member_role_2="member")
    r = await client.post("/setup", data=payload)
    assert r.status_code == 200
    session.expire_all()
    assert session.query(Member).count() == 2  # admin + Bo, blank row dropped


# ---------------------------------------------------------------------------
# HTMX helpers
# ---------------------------------------------------------------------------
async def test_get_setup_member_row_returns_row_html(client):
    r = await client.get("/setup/member-row")
    assert r.status_code == 200
    assert "member_name_" in r.text
    assert "member_role_" in r.text


async def test_get_setup_policy_returns_template_text(client):
    r = await client.get("/setup/policy", params={"policy_template_id": "family"})
    assert r.status_code == 200
    # Either the README content or a textarea wrapping it.
    assert len(r.text) > 0


async def test_get_setup_policy_unknown_template_returns_404(client):
    r = await client.get("/setup/policy", params={"policy_template_id": "no-such-template"})
    assert r.status_code == 404


async def test_get_setup_policy_blank_returns_empty_textarea(client):
    """Selecting the '(blank)' option clears the textarea — no template lookup."""
    r = await client.get("/setup/policy", params={"policy_template_id": ""})
    assert r.status_code == 200
