"""Service-layer tests for recording payouts."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.claims import submit_claim
from api.contributions import current_balance, record_contribution
from api.orm import (
    AuditEvent,
    ClaimStatus,
    LedgerEntry,
    LedgerKind,
    Payout,
)
from api.payouts import record_payout


def _approved_claim(session, pool, submitter, amount_cents=5_000):
    """Submit a claim that auto-approves at the small tier."""
    return submit_claim(
        session,
        pool_id=pool.id,
        member_id=submitter.id,
        amount_cents=amount_cents,
        category="medical",
        description="receipt",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


def _seed_balance(session, pool, admin, amount_cents):
    """Seed the pool's balance via a contribution from admin."""
    record_contribution(
        session,
        pool_id=pool.id,
        member_id=admin.id,
        amount_cents=amount_cents,
        period="2026-01",
        recorded_by=admin.id,
    )


# ---------------------------------------------------------------------------
# Golden path
# ---------------------------------------------------------------------------
def test_record_payout_creates_payout_row(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    p = record_payout(
        session,
        claim_id=claim.id,
        amount_paid_cents=5_000,
        recorded_by=admin.id,
        notes="Venmo @ada",
    )
    assert p.id is not None
    assert p.claim_id == claim.id
    assert p.amount_paid == 5_000
    assert p.notes == "Venmo @ada"
    assert p.recorded_by == admin.id


def test_record_payout_transitions_claim_to_paid(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000, recorded_by=admin.id,
    )
    session.refresh(claim)
    assert claim.status == ClaimStatus.paid


def test_record_payout_writes_negative_ledger_entry(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    p = record_payout(
        session, claim_id=claim.id, amount_paid_cents=3_000, recorded_by=admin.id,
    )
    le = (
        session.query(LedgerEntry)
        .filter_by(kind=LedgerKind.payout, ref_id=p.id)
        .one()
    )
    assert le.delta == -3_000
    assert le.balance_after == 100_000 - 3_000


def test_record_payout_writes_audit_event(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    p = record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000, recorded_by=admin.id,
    )
    audit = session.query(AuditEvent).filter_by(kind="payout.recorded").one()
    assert audit.actor_member_id == admin.id
    assert audit.payload_json["claim_id"] == claim.id
    assert audit.payload_json["payout_id"] == p.id
    assert audit.payload_json["amount_paid_cents"] == 5_000
    assert audit.payload_json["balance_after"] == 95_000


def test_record_payout_running_balance_correct_across_payouts(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    c1 = _approved_claim(session, pool, admin, amount_cents=2_000)
    c2 = _approved_claim(session, pool, admin, amount_cents=3_000)
    record_payout(session, claim_id=c1.id, amount_paid_cents=2_000, recorded_by=admin.id)
    record_payout(session, claim_id=c2.id, amount_paid_cents=3_000, recorded_by=admin.id)
    assert current_balance(session, pool.id) == 95_000


def test_record_payout_uses_provided_paid_at(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    explicit = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    p = record_payout(
        session,
        claim_id=claim.id,
        amount_paid_cents=5_000,
        recorded_by=admin.id,
        paid_at=explicit,
    )
    assert p.paid_at == explicit


def test_record_payout_amount_can_be_less_than_requested(session, pool, admin):
    """v0 lets the treasurer pay a partial / negotiated amount."""
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin, amount_cents=5_000)
    p = record_payout(
        session, claim_id=claim.id, amount_paid_cents=4_000, recorded_by=admin.id,
    )
    assert p.amount_paid == 4_000
    session.refresh(claim)
    assert claim.status == ClaimStatus.paid  # still flips to paid


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_record_payout_rejects_unknown_claim(session, admin):
    with pytest.raises(ValueError):
        record_payout(
            session, claim_id=99999, amount_paid_cents=100, recorded_by=admin.id,
        )


@pytest.mark.parametrize(
    "starting_status",
    [ClaimStatus.submitted, ClaimStatus.voting, ClaimStatus.rejected,
     ClaimStatus.paid, ClaimStatus.withdrawn],
)
def test_record_payout_rejects_non_approved_claim(
    session, pool, admin, starting_status
):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    claim.status = starting_status
    session.commit()
    with pytest.raises(ValueError):
        record_payout(
            session, claim_id=claim.id, amount_paid_cents=100, recorded_by=admin.id,
        )


def test_record_payout_rejects_zero_amount(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    with pytest.raises(ValueError):
        record_payout(
            session, claim_id=claim.id, amount_paid_cents=0, recorded_by=admin.id,
        )


def test_record_payout_rejects_negative_amount(session, pool, admin):
    _seed_balance(session, pool, admin, 100_000)
    claim = _approved_claim(session, pool, admin)
    with pytest.raises(ValueError):
        record_payout(
            session, claim_id=claim.id, amount_paid_cents=-1, recorded_by=admin.id,
        )


def test_record_payout_rejects_overdraw(session, pool, admin):
    _seed_balance(session, pool, admin, 5_000)  # only $50 in the pool
    claim = _approved_claim(session, pool, admin)
    with pytest.raises(ValueError, match="balance"):
        record_payout(
            session, claim_id=claim.id, amount_paid_cents=10_000, recorded_by=admin.id,
        )


def test_record_payout_overdraw_does_not_persist(session, pool, admin):
    _seed_balance(session, pool, admin, 5_000)
    claim = _approved_claim(session, pool, admin)
    with pytest.raises(ValueError):
        record_payout(
            session, claim_id=claim.id, amount_paid_cents=10_000, recorded_by=admin.id,
        )
    assert session.query(Payout).count() == 0
    session.refresh(claim)
    assert claim.status == ClaimStatus.approved  # unchanged


def test_record_payout_at_exact_balance_boundary_is_allowed(session, pool, admin):
    _seed_balance(session, pool, admin, 5_000)
    claim = _approved_claim(session, pool, admin)
    p = record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000, recorded_by=admin.id,
    )
    assert p.amount_paid == 5_000
    assert current_balance(session, pool.id) == 0
