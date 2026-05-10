"""Payout recording service.

Treasurer (= admin in v0) marks an approved claim as paid. One call writes
three rows in one transaction:

* ``Payout`` — the line item (amount, paid_at, notes, who recorded).
* ``LedgerEntry`` (kind=payout, ``delta = -amount``) — the balance update.
* ``AuditEvent`` (``payout.recorded``) — audit trail.

The claim's status flips to :attr:`ClaimStatus.paid`.

Guards:
    - Claim must exist and be in :attr:`ClaimStatus.approved`.
    - Amount must be positive.
    - Amount must not exceed the pool's current balance (no overdraw in v0).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from api.contributions import current_balance
from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    LedgerEntry,
    LedgerKind,
    Payout,
)
from api.webhooks import dispatch_event


def record_payout(
    db: Session,
    *,
    claim_id: int,
    amount_paid_cents: int,
    recorded_by: int,
    paid_at: datetime | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> Payout:
    if amount_paid_cents <= 0:
        raise ValueError("amount must be positive")

    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ValueError("claim not found")
    if claim.status != ClaimStatus.approved:
        raise ValueError(
            f"claim is not approved (status={claim.status.value}); cannot pay"
        )

    balance = current_balance(db, claim.pool_id)
    if amount_paid_cents > balance:
        raise ValueError(
            f"insufficient balance: pool has {balance} cents, "
            f"payout requested {amount_paid_cents} cents"
        )

    now = now or datetime.now(timezone.utc)
    paid_at = paid_at or now

    payout = Payout(
        claim_id=claim_id,
        amount_paid=amount_paid_cents,
        paid_at=paid_at,
        recorded_by=recorded_by,
        notes=(notes.strip() if notes else None) or None,
    )
    db.add(payout)
    db.flush()

    new_balance = balance - amount_paid_cents
    db.add(
        LedgerEntry(
            pool_id=claim.pool_id,
            kind=LedgerKind.payout,
            ref_id=payout.id,
            delta=-amount_paid_cents,
            balance_after=new_balance,
            recorded_at=now,
        )
    )

    claim.status = ClaimStatus.paid

    db.add(
        AuditEvent(
            pool_id=claim.pool_id,
            actor_member_id=recorded_by,
            kind="payout.recorded",
            payload_json={
                "claim_id": claim_id,
                "payout_id": payout.id,
                "amount_paid_cents": amount_paid_cents,
                "balance_after": new_balance,
            },
            recorded_at=now,
        )
    )

    db.commit()
    db.refresh(payout)

    dispatch_event(db, claim.pool_id, "claim.paid", {
        "claim_id": claim_id,
        "payout_id": payout.id,
        "amount_paid_cents": amount_paid_cents,
        "balance_after_cents": new_balance,
        "paid_at": paid_at.isoformat(),
    })

    return payout
