"""Dashboard aggregations.

Pure read queries against the ledger and the contribution / member tables.
The dashboard route layer turns these into the per-pool overview page.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.contributions import current_balance
from api.orm import (
    Claim,
    ClaimStatus,
    Contribution,
    LedgerEntry,
    LedgerKind,
    Member,
    MemberStatus,
    Payout,
    Pool,
)


@dataclass(frozen=True)
class MonthBucket:
    year_month: str  # "YYYY-MM"
    contributions_cents: int
    payouts_cents: int


def _month_label(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _last_12_months(now: datetime) -> list[str]:
    """Return 12 ``YYYY-MM`` strings, oldest first, ending in ``now``'s month."""
    out: list[str] = []
    y, m = now.year, now.month
    for _ in range(12):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def monthly_buckets(
    db: Session, pool_id: int, *, now: datetime | None = None
) -> list[MonthBucket]:
    """Last 12 months of contributions (in) and payouts (out), oldest first.

    Contributions are bucketed by ``Contribution.recorded_at``; payouts by
    ``LedgerEntry.recorded_at`` (which equals ``Payout.paid_at`` from
    :func:`api.payouts.record_payout`).
    """
    now = now or datetime.now(timezone.utc)
    months = _last_12_months(now)

    contrib_by_month: dict[str, int] = {ym: 0 for ym in months}
    for c in db.execute(
        select(Contribution).where(Contribution.pool_id == pool_id)
    ).scalars():
        ym = _month_label(c.recorded_at)
        if ym in contrib_by_month:
            contrib_by_month[ym] += c.amount

    payout_by_month: dict[str, int] = {ym: 0 for ym in months}
    for le in db.execute(
        select(LedgerEntry).where(
            LedgerEntry.pool_id == pool_id,
            LedgerEntry.kind == LedgerKind.payout,
        )
    ).scalars():
        ym = _month_label(le.recorded_at)
        if ym in payout_by_month:
            payout_by_month[ym] += -le.delta  # delta is negative for payouts

    return [
        MonthBucket(
            year_month=ym,
            contributions_cents=contrib_by_month[ym],
            payouts_cents=payout_by_month[ym],
        )
        for ym in months
    ]


def member_contribution_status(db: Session, pool_id: int) -> list[dict]:
    """Per-member: total contributed (cents) + most recent ``period`` paid.

    Excludes ``inactive`` members. Ordered by display_name.
    """
    members = (
        db.query(Member)
        .filter(
            Member.pool_id == pool_id, Member.status != MemberStatus.inactive
        )
        .order_by(Member.display_name)
        .all()
    )
    totals = dict(
        db.execute(
            select(Contribution.member_id, func.sum(Contribution.amount))
            .where(Contribution.pool_id == pool_id)
            .group_by(Contribution.member_id)
        ).all()
    )
    last_periods = dict(
        db.execute(
            select(Contribution.member_id, func.max(Contribution.period))
            .where(Contribution.pool_id == pool_id)
            .group_by(Contribution.member_id)
        ).all()
    )
    return [
        {
            "member_id": m.id,
            "display_name": m.display_name,
            "role": m.role.value,
            "status": m.status.value,
            "total_cents": int(totals.get(m.id) or 0),
            "last_period": last_periods.get(m.id),
        }
        for m in members
    ]


def pending_claims(db: Session, pool_id: int) -> list[Claim]:
    return list(
        db.execute(
            select(Claim)
            .where(Claim.pool_id == pool_id, Claim.status == ClaimStatus.voting)
            .order_by(Claim.submitted_at.asc())
        ).scalars()
    )


def overview_summary(db: Session, pool_id: int) -> dict:
    pool = db.get(Pool, pool_id)
    return {
        "pool_name": pool.name if pool else "",
        "currency": pool.currency if pool else "",
        "balance_cents": current_balance(db, pool_id),
        "pending_claims_count": len(pending_claims(db, pool_id)),
        "paid_claims_count": db.scalar(
            select(func.count(Claim.id)).where(
                Claim.pool_id == pool_id, Claim.status == ClaimStatus.paid
            )
        )
        or 0,
        "total_payouts_cents": int(
            db.scalar(
                select(func.coalesce(func.sum(Payout.amount_paid), 0))
                .join(Claim, Payout.claim_id == Claim.id)
                .where(Claim.pool_id == pool_id)
            )
            or 0
        ),
        "member_count": db.scalar(
            select(func.count(Member.id)).where(
                Member.pool_id == pool_id, Member.status != MemberStatus.inactive
            )
        )
        or 0,
    }
