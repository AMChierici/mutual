"""Contribution recording service.

Every recorded contribution produces three rows in one transaction:

* ``Contribution`` — the line item (member, amount, period).
* ``LedgerEntry`` — the running-balance update (kind=contribution).
* ``AuditEvent`` — the ``contribution.recorded`` audit trail.

``record_bulk`` is the monthly-close path. It skips rows where the member
already has a contribution for the given period — that's the v0 guard
against accidentally clicking submit twice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.orm import (
    AuditEvent,
    Contribution,
    LedgerEntry,
    LedgerKind,
    Member,
)

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class BulkContributionRow(BaseModel):
    member_id: int
    amount_cents: int = Field(ge=0)


@dataclass
class BulkSummary:
    created_contribution_ids: list[int] = field(default_factory=list)
    skipped_member_ids: list[int] = field(default_factory=list)


def _validate_period(period: str) -> None:
    if not _PERIOD_RE.match(period):
        raise ValueError(f"invalid period {period!r}; expected YYYY-MM")


def current_balance(db: Session, pool_id: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.delta), 0))
        .where(LedgerEntry.pool_id == pool_id)
    )
    return int(total or 0)


def record_contribution(
    db: Session,
    *,
    pool_id: int,
    member_id: int,
    amount_cents: int,
    period: str,
    recorded_by: int,
    now: datetime | None = None,
) -> Contribution:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    _validate_period(period)

    member = db.get(Member, member_id)
    if member is None or member.pool_id != pool_id:
        raise ValueError("member not in pool")

    now = now or datetime.now(timezone.utc)

    contribution = Contribution(
        pool_id=pool_id,
        member_id=member_id,
        amount=amount_cents,
        period=period,
        recorded_at=now,
        recorded_by=recorded_by,
    )
    db.add(contribution)
    db.flush()  # need contribution.id for the ledger entry

    new_balance = current_balance(db, pool_id) + amount_cents
    db.add(
        LedgerEntry(
            pool_id=pool_id,
            kind=LedgerKind.contribution,
            ref_id=contribution.id,
            delta=amount_cents,
            balance_after=new_balance,
            recorded_at=now,
        )
    )
    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=recorded_by,
            kind="contribution.recorded",
            payload_json={
                "contribution_id": contribution.id,
                "member_id": member_id,
                "amount_cents": amount_cents,
                "period": period,
            },
            recorded_at=now,
        )
    )
    db.commit()
    db.refresh(contribution)
    return contribution


def existing_period_member_ids(db: Session, pool_id: int, period: str) -> set[int]:
    rows = db.execute(
        select(Contribution.member_id).where(
            Contribution.pool_id == pool_id,
            Contribution.period == period,
        )
    ).all()
    return {r[0] for r in rows}


def record_bulk(
    db: Session,
    *,
    pool_id: int,
    period: str,
    rows: list[BulkContributionRow],
    recorded_by: int,
    now: datetime | None = None,
) -> BulkSummary:
    _validate_period(period)
    now = now or datetime.now(timezone.utc)

    already = existing_period_member_ids(db, pool_id, period)
    summary = BulkSummary()
    balance = current_balance(db, pool_id)

    for row in rows:
        if row.amount_cents <= 0:
            summary.skipped_member_ids.append(row.member_id)
            continue
        if row.member_id in already:
            summary.skipped_member_ids.append(row.member_id)
            continue
        member = db.get(Member, row.member_id)
        if member is None or member.pool_id != pool_id:
            summary.skipped_member_ids.append(row.member_id)
            continue

        contribution = Contribution(
            pool_id=pool_id,
            member_id=row.member_id,
            amount=row.amount_cents,
            period=period,
            recorded_at=now,
            recorded_by=recorded_by,
        )
        db.add(contribution)
        db.flush()

        balance += row.amount_cents
        db.add(
            LedgerEntry(
                pool_id=pool_id,
                kind=LedgerKind.contribution,
                ref_id=contribution.id,
                delta=row.amount_cents,
                balance_after=balance,
                recorded_at=now,
            )
        )
        db.add(
            AuditEvent(
                pool_id=pool_id,
                actor_member_id=recorded_by,
                kind="contribution.recorded",
                payload_json={
                    "contribution_id": contribution.id,
                    "member_id": row.member_id,
                    "amount_cents": row.amount_cents,
                    "period": period,
                },
                recorded_at=now,
            )
        )
        summary.created_contribution_ids.append(contribution.id)
        already.add(row.member_id)

    db.commit()
    return summary
