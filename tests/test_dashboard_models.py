"""Tests for the bridge from API ORM rows → actuarial dataclasses → models."""
from __future__ import annotations

from datetime import datetime, timezone

from api.claims import submit_claim
from api.contributions import record_contribution
from api.dashboard_models import (
    actuarial_history,
    actuarial_members,
    compute_pricing,
    compute_reserving,
)
from api.payouts import record_payout
from models.base import Claim as ActClaim, Member as ActMember


def _seed_paid_claim(session, pool, admin, amount_cents, occurred_iso):
    """Submit, auto-approve at the small tier, then mark paid."""
    claim = submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=amount_cents,
        category="medical", description="x",
        occurred_at=datetime.fromisoformat(occurred_iso).replace(tzinfo=timezone.utc),
    )
    record_payout(
        session, claim_id=claim.id,
        amount_paid_cents=amount_cents, recorded_by=admin.id,
        paid_at=datetime.fromisoformat(occurred_iso).replace(tzinfo=timezone.utc),
    )
    return claim


def _seed_balance(session, pool, admin, cents=100_000):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=cents, period="2026-01", recorded_by=admin.id,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
def test_actuarial_members_skips_inactive_and_observers(session, pool, admin, members):
    members[0].status = type(members[0].status).inactive
    from api.orm import MemberRole
    members[1].role = MemberRole.observer
    session.commit()
    out = actuarial_members(session, pool.id)
    assert all(isinstance(m, ActMember) for m in out)
    ids = {m.id for m in out}
    assert str(admin.id) in ids
    assert str(members[2].id) in ids
    assert str(members[0].id) not in ids
    assert str(members[1].id) not in ids


def test_actuarial_history_uses_actual_paid_amount_in_dollars(
    session, pool, admin
):
    _seed_balance(session, pool, admin, cents=100_000)
    _seed_paid_claim(session, pool, admin, amount_cents=2_500, occurred_iso="2026-04-15")
    _seed_paid_claim(session, pool, admin, amount_cents=7_750, occurred_iso="2026-04-20")
    history = actuarial_history(session, pool.id)
    assert all(isinstance(c, ActClaim) for c in history)
    paids = sorted(c.paid for c in history)
    assert paids == [25.00, 77.50]


def test_actuarial_history_excludes_unpaid_claims(session, pool, admin):
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )  # voting, not paid
    history = actuarial_history(session, pool.id)
    assert history == []


# ---------------------------------------------------------------------------
# compute_pricing  (smoke — actuarial models have their own deep tests)
# ---------------------------------------------------------------------------
def test_compute_pricing_with_history_returns_per_member_premium(
    session, pool, admin, members
):
    _seed_balance(session, pool, admin, cents=100_000)
    _seed_paid_claim(session, pool, admin, amount_cents=5_000, occurred_iso="2026-04-15")
    result = compute_pricing(session, pool.id)
    assert result.period == "monthly"
    assert "rationale" in result.__dict__ or hasattr(result, "rationale")
    # One premium per active non-observer member (admin + 3 members).
    assert len(result.premiums) == 4
    # Rationale mentions the load factor and member count.
    assert "Flat pricing" in result.rationale


def test_compute_pricing_without_history_falls_back(session, pool, admin, members):
    result = compute_pricing(session, pool.id)
    assert len(result.premiums) == 4
    assert result.rationale  # non-empty


# ---------------------------------------------------------------------------
# compute_reserving  (smoke)
# ---------------------------------------------------------------------------
def test_compute_reserving_with_history_returns_required_reserve(
    session, pool, admin, members
):
    _seed_balance(session, pool, admin, cents=100_000)
    _seed_paid_claim(session, pool, admin, amount_cents=5_000, occurred_iso="2026-04-15")
    result = compute_reserving(session, pool.id, simulations=200, seed=42)
    assert result.required_reserve >= 0.0
    assert 0.0 < result.confidence <= 1.0
    assert "Bootstrap Monte Carlo" in result.rationale
    assert "ruin_probability_at_current_balance" in result.diagnostics


def test_compute_reserving_without_history_uses_fallback_rationale(
    session, pool, admin, members
):
    _seed_balance(session, pool, admin, cents=100_000)
    result = compute_reserving(session, pool.id, simulations=200, seed=42)
    assert "No claim history" in result.rationale
