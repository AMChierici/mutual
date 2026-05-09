"""Service-layer tests for the first-run setup wizard."""
from __future__ import annotations

import pytest

from api.setup import (
    GovernanceTier,
    MemberSpec,
    SetupAlreadyComplete,
    SetupRequest,
    complete_setup,
    is_first_run,
)
from api.orm import (
    AuditEvent,
    AuthSession,
    LedgerEntry,
    LedgerKind,
    LoginToken,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
)


def _basic_request(**overrides) -> SetupRequest:
    payload = dict(
        pool_name="Family",
        currency="USD",
        starting_balance_cents=0,
        members=[
            MemberSpec(display_name="Ada", role="admin", email="ada@example.com"),
            MemberSpec(display_name="Bo", role="member"),
        ],
        policy_template_id="family",
        policy_text="We cover unexpected medical and dental costs.",
        governance_tiers=[
            GovernanceTier(max_amount_cents=10_000, scheme="auto_approve"),
            GovernanceTier(max_amount_cents=100_000, scheme="majority"),
            GovernanceTier(max_amount_cents=None, scheme="unanimous"),
        ],
    )
    payload.update(overrides)
    return SetupRequest(**payload)


# ---------------------------------------------------------------------------
# is_first_run
# ---------------------------------------------------------------------------
def test_is_first_run_true_on_empty_db(session):
    assert is_first_run(session) is True


def test_is_first_run_false_after_pool_created(session):
    session.add(Pool(name="P", currency="USD", governance_config={}))
    session.commit()
    assert is_first_run(session) is False


# ---------------------------------------------------------------------------
# complete_setup — golden path
# ---------------------------------------------------------------------------
def test_complete_setup_creates_pool_with_metadata(session):
    result = complete_setup(session, _basic_request())
    pool = session.get(Pool, result.pool_id)
    assert pool is not None
    assert pool.name == "Family"
    assert pool.currency == "USD"
    assert pool.policy_template_id == "family"
    assert "medical" in pool.policy_text
    assert pool.governance_config["tiers"][0]["scheme"] == "auto_approve"
    assert pool.governance_config["tiers"][-1]["max_amount_cents"] is None


def test_complete_setup_creates_admin_active_and_member_invited(session):
    result = complete_setup(session, _basic_request())
    admin = session.get(Member, result.admin_member_id)
    assert admin.role == MemberRole.admin
    assert admin.status == MemberStatus.active

    others = session.query(Member).filter(Member.id != admin.id).all()
    assert len(others) == 1
    assert others[0].role == MemberRole.member
    assert others[0].status == MemberStatus.invited


def test_complete_setup_returns_login_url_and_session_token(session):
    result = complete_setup(session, _basic_request())
    assert result.admin_login_url.startswith("/auth/login/")
    assert result.admin_session_token  # non-empty
    # Session is persisted and active
    s = session.query(AuthSession).filter_by(token=result.admin_session_token).one()
    assert s.member_id == result.admin_member_id
    assert s.revoked_at is None
    # Login token is also persisted (so admin can log in from another device)
    assert session.query(LoginToken).filter_by(member_id=result.admin_member_id).count() == 1


def test_complete_setup_writes_opening_balance_ledger_entry(session):
    result = complete_setup(session, _basic_request(starting_balance_cents=50_000))
    entries = session.query(LedgerEntry).filter_by(pool_id=result.pool_id).all()
    assert len(entries) == 1
    e = entries[0]
    assert e.kind == LedgerKind.opening_balance
    assert e.delta == 50_000
    assert e.balance_after == 50_000


def test_complete_setup_skips_ledger_entry_when_balance_is_zero(session):
    result = complete_setup(session, _basic_request(starting_balance_cents=0))
    assert session.query(LedgerEntry).filter_by(pool_id=result.pool_id).count() == 0


def test_complete_setup_writes_audit_events(session):
    result = complete_setup(session, _basic_request(starting_balance_cents=100))
    kinds = [
        e.kind
        for e in session.query(AuditEvent)
        .filter_by(pool_id=result.pool_id)
        .order_by(AuditEvent.id)
        .all()
    ]
    assert "pool.created" in kinds
    assert "member.added" in kinds
    assert kinds.count("member.added") == 2  # admin + 1 member
    assert "ledger.opening_balance" in kinds


# ---------------------------------------------------------------------------
# complete_setup — guards
# ---------------------------------------------------------------------------
def test_complete_setup_rejects_when_pool_already_exists(session):
    complete_setup(session, _basic_request())
    with pytest.raises(SetupAlreadyComplete):
        complete_setup(session, _basic_request())


def test_setup_request_rejects_payload_with_no_admin():
    with pytest.raises(ValueError):
        SetupRequest(
            pool_name="x",
            currency="USD",
            starting_balance_cents=0,
            members=[MemberSpec(display_name="Bo", role="member")],
            policy_template_id=None,
            policy_text="",
            governance_tiers=[GovernanceTier(max_amount_cents=None, scheme="unanimous")],
        )


def test_setup_request_normalises_currency_to_upper():
    req = SetupRequest(
        pool_name="x",
        currency="eur",
        starting_balance_cents=0,
        members=[MemberSpec(display_name="Ada", role="admin")],
        policy_template_id=None,
        policy_text="",
        governance_tiers=[GovernanceTier(max_amount_cents=None, scheme="unanimous")],
    )
    assert req.currency == "EUR"


def test_setup_request_rejects_empty_pool_name():
    with pytest.raises(ValueError):
        SetupRequest(
            pool_name="",
            currency="USD",
            starting_balance_cents=0,
            members=[MemberSpec(display_name="Ada", role="admin")],
            policy_template_id=None,
            policy_text="",
            governance_tiers=[GovernanceTier(max_amount_cents=None, scheme="unanimous")],
        )


def test_setup_request_requires_governance_tiers():
    with pytest.raises(ValueError):
        SetupRequest(
            pool_name="x",
            currency="USD",
            starting_balance_cents=0,
            members=[MemberSpec(display_name="Ada", role="admin")],
            policy_template_id=None,
            policy_text="",
            governance_tiers=[],
        )


def test_setup_request_rejects_negative_starting_balance():
    with pytest.raises(ValueError):
        SetupRequest(
            pool_name="x",
            currency="USD",
            starting_balance_cents=-1,
            members=[MemberSpec(display_name="Ada", role="admin")],
            policy_template_id=None,
            policy_text="",
            governance_tiers=[GovernanceTier(max_amount_cents=None, scheme="unanimous")],
        )
