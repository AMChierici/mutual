"""Bridge between the API ORM and the actuarial models in ``models/``.

The actuarial models speak in ``models.base.Member`` / ``models.base.Claim``
dataclasses (string ids, dollar amounts as floats, ``date`` not ``datetime``).
The API ORM speaks in integer ids and integer cents. This module does the
translation, pulls the relevant slice of pool data, and runs each model.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.contributions import current_balance
from api.dashboard import monthly_buckets
from api.orm import (
    Claim as ApiClaim,
    ClaimStatus,
    Member as ApiMember,
    MemberRole,
    MemberStatus,
    Payout,
)
from models.base import Claim as ActClaim, Member as ActMember, PricingResult, ReservingResult
from models.pricing.flat import FlatPricing
from models.reserving.ruin_probability import RuinProbabilityReserving


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
def actuarial_members(db: Session, pool_id: int) -> list[ActMember]:
    """Active, non-observer members translated to actuarial dataclasses."""
    rows = db.scalars(
        select(ApiMember).where(
            ApiMember.pool_id == pool_id,
            ApiMember.status == MemberStatus.active,
            ApiMember.role != MemberRole.observer,
        )
    ).all()
    return [
        ActMember(
            id=str(m.id),
            joined=m.joined_at.date(),
            exposure=1.0,
        )
        for m in rows
    ]


def actuarial_history(db: Session, pool_id: int) -> list[ActClaim]:
    """Paid claims, joined to their Payout rows for the actual paid amount.

    Only ``paid`` claims contribute to the actuarial picture — pricing and
    reserving both want "what did this cost the pool", not "what was claimed".
    """
    rows = db.execute(
        select(ApiClaim, Payout)
        .join(Payout, Payout.claim_id == ApiClaim.id)
        .where(
            ApiClaim.pool_id == pool_id,
            ApiClaim.status == ClaimStatus.paid,
        )
    ).all()
    return [
        ActClaim(
            id=str(claim.id),
            member_id=str(claim.member_id),
            occurred=claim.occurred_at.date(),
            paid=payout.amount_paid / 100.0,
            category=claim.category,
        )
        for claim, payout in rows
    ]


# ---------------------------------------------------------------------------
# Model runners
# ---------------------------------------------------------------------------
def _avg_monthly_inflow_dollars(db: Session, pool_id: int, *, now: datetime | None = None) -> float:
    buckets = monthly_buckets(db, pool_id, now=now)
    total = sum(b.contributions_cents for b in buckets)
    if not buckets:
        return 0.0
    return (total / len(buckets)) / 100.0


def compute_pricing(
    db: Session,
    pool_id: int,
    *,
    safety_loading: float = 1.2,
    target_payout_capacity: float | None = None,
) -> PricingResult:
    members = actuarial_members(db, pool_id)
    history = actuarial_history(db, pool_id)
    if target_payout_capacity is None:
        # When no history, FlatPricing falls back to target / 12 as expected
        # monthly. Use current balance as a reasonable seed.
        target_payout_capacity = current_balance(db, pool_id) / 100.0
    return FlatPricing(safety_loading=safety_loading).price(
        members=members,
        history=history,
        target_payout_capacity=target_payout_capacity,
    )


def compute_reserving(
    db: Session,
    pool_id: int,
    *,
    horizon_months: int = 12,
    simulations: int = 5000,
    confidence: float = 0.95,
    seed: int | None = None,
    now: datetime | None = None,
) -> ReservingResult:
    members = actuarial_members(db, pool_id)
    history = actuarial_history(db, pool_id)
    monthly_premium_inflow = _avg_monthly_inflow_dollars(db, pool_id, now=now)
    return RuinProbabilityReserving(
        horizon_months=horizon_months,
        simulations=simulations,
        monthly_premium_inflow=monthly_premium_inflow,
        seed=seed,
    ).reserve(
        members=members,
        history=history,
        current_balance=current_balance(db, pool_id) / 100.0,
        confidence=confidence,
    )
