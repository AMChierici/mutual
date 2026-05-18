"""Tests for SQLAlchemy ORM models defined in api/orm.py.

Schema mirrors docs/architecture.md exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    Contribution,
    LedgerEntry,
    LedgerKind,
    Member,
    MemberRole,
    MemberStatus,
    Payout,
    Pool,
    User,
    Vote,
    VoteDecision,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_slug_seq = count(1)


def _pool(session, name="Family", currency="USD", governance_config=None):
    p = Pool(
        slug=f"pool-{next(_slug_seq)}",
        name=name,
        currency=currency,
        governance_config=governance_config or {},
    )
    session.add(p)
    session.commit()
    return p


_user_seq = count(1)


def _member(session, pool, name="Ada", role=MemberRole.member):
    u = User(email=f"u{next(_user_seq)}@example.test", display_name=name)
    session.add(u)
    session.flush()
    m = Member(user_id=u.id, pool_id=pool.id, display_name=name, role=role)
    session.add(m)
    session.commit()
    return m


def _claim(session, pool, member, amount=10000, category="medical"):
    c = Claim(
        pool_id=pool.id,
        member_id=member.id,
        amount_requested=amount,
        category=category,
        description="test",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(c)
    session.commit()
    return c


# ---------------------------------------------------------------------------
# table presence
# ---------------------------------------------------------------------------
def test_all_required_tables_exist(engine):
    expected = {
        "pools",
        "users",
        "memberships",
        "contributions",
        "claims",
        "votes",
        "payouts",
        "ledger_entries",
        "audit_events",
    }
    assert expected <= set(inspect(engine).get_table_names())


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------
def test_pool_persists_and_sets_created_at(session):
    p = _pool(session, name="Co-op", currency="EUR")
    assert p.id is not None
    assert p.created_at is not None
    assert p.created_at.tzinfo is not None


def test_pool_currency_is_required(session):
    session.add(Pool(slug="orphan", name="x"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_pool_slug_is_required(session):
    session.add(Pool(name="no-slug", currency="USD"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_pool_governance_config_is_json(session):
    p = _pool(session, governance_config={"tiers": [{"max": 500, "scheme": "auto_approve"}]})
    session.expire_all()
    fetched = session.get(Pool, p.id)
    assert fetched.governance_config == {"tiers": [{"max": 500, "scheme": "auto_approve"}]}


# ---------------------------------------------------------------------------
# Membership (the per-pool role; class is still importable as ``Member``)
# ---------------------------------------------------------------------------
def test_member_default_role_and_status(session):
    p = _pool(session)
    u = User(email="bo@example.test", display_name="Bo")
    session.add(u)
    session.flush()
    m = Member(user_id=u.id, pool_id=p.id, display_name="Bo")
    session.add(m)
    session.commit()
    assert m.role == MemberRole.member
    assert m.status == MemberStatus.invited
    assert m.joined_at is not None


def test_member_role_enum_rejects_unknown(session):
    p = _pool(session)
    u = User(email="x@example.test", display_name="x")
    session.add(u)
    session.flush()
    with pytest.raises((StatementError, ValueError, LookupError)):
        m = Member(user_id=u.id, pool_id=p.id, display_name="x", role="dictator")  # type: ignore[arg-type]
        session.add(m)
        session.commit()


def test_member_pool_fk_required(session):
    u = User(email="orphan@example.test", display_name="orphan")
    session.add(u)
    session.flush()
    session.add(Member(user_id=u.id, display_name="orphan"))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Contribution + Ledger
# ---------------------------------------------------------------------------
def test_contribution_persists(session):
    p = _pool(session)
    m = _member(session, p)
    c = Contribution(
        pool_id=p.id,
        member_id=m.id,
        amount=10000,
        period="2026-W01",
        recorded_by=m.id,
    )
    session.add(c)
    session.commit()
    assert c.id is not None
    assert c.recorded_at is not None


def test_ledger_entry_records_kind_and_balance(session):
    p = _pool(session)
    le = LedgerEntry(
        pool_id=p.id,
        kind=LedgerKind.contribution,
        ref_id=1,
        delta=10000,
        balance_after=10000,
    )
    session.add(le)
    session.commit()
    assert le.id is not None
    assert le.recorded_at is not None
    assert le.kind == LedgerKind.contribution


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------
def test_claim_default_status_is_submitted(session):
    p = _pool(session)
    m = _member(session, p)
    c = _claim(session, p, m)
    assert c.status == ClaimStatus.submitted
    assert c.submitted_at is not None
    assert c.evidence_uris == []


def test_claim_evidence_uris_round_trip_as_list(session):
    p = _pool(session)
    m = _member(session, p)
    c = Claim(
        pool_id=p.id,
        member_id=m.id,
        amount_requested=20000,
        category="dental",
        description="root canal",
        evidence_uris=["uploads/a.jpg", "uploads/b.pdf"],
        occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    session.add(c)
    session.commit()
    session.expire_all()
    fetched = session.scalars(select(Claim)).one()
    assert fetched.evidence_uris == ["uploads/a.jpg", "uploads/b.pdf"]


def test_claim_status_can_advance(session):
    p = _pool(session)
    m = _member(session, p)
    c = _claim(session, p, m)
    c.status = ClaimStatus.voting
    session.commit()
    assert session.get(Claim, c.id).status == ClaimStatus.voting


# ---------------------------------------------------------------------------
# Vote
# ---------------------------------------------------------------------------
def test_vote_records_decision_and_reason(session):
    p = _pool(session)
    m = _member(session, p)
    c = _claim(session, p, m)
    v = Vote(
        pool_id=p.id,
        claim_id=c.id,
        member_id=m.id,
        decision=VoteDecision.approve,
        reason="ok",
    )
    session.add(v)
    session.commit()
    assert v.id is not None
    assert v.cast_at is not None
    assert v.decision == VoteDecision.approve


def test_vote_requires_existing_claim(session):
    p = _pool(session)
    m = _member(session, p)
    session.add(Vote(pool_id=p.id, claim_id=999, member_id=m.id, decision=VoteDecision.reject))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------
def test_payout_links_to_claim(session):
    p = _pool(session)
    m = _member(session, p)
    c = _claim(session, p, m)
    pay = Payout(
        pool_id=p.id,
        claim_id=c.id,
        amount_paid=10000,
        recorded_by=m.id,
        notes="venmo",
    )
    session.add(pay)
    session.commit()
    assert pay.id is not None
    assert pay.paid_at is not None


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------
def test_audit_event_payload_round_trips(session):
    p = _pool(session)
    m = _member(session, p)
    ae = AuditEvent(
        pool_id=p.id,
        actor_member_id=m.id,
        kind="claim.submitted",
        payload_json={"claim_id": 7, "amount": 1234},
    )
    session.add(ae)
    session.commit()
    session.expire_all()
    fetched = session.get(AuditEvent, ae.id)
    assert fetched.kind == "claim.submitted"
    assert fetched.payload_json == {"claim_id": 7, "amount": 1234}


def test_audit_event_actor_can_be_null(session):
    """System-originated events (e.g. cron 'monthly close due') have no actor."""
    p = _pool(session)
    ae = AuditEvent(pool_id=p.id, actor_member_id=None, kind="system.startup", payload_json={})
    session.add(ae)
    session.commit()
    assert ae.id is not None
