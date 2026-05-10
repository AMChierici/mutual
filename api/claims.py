"""Claim submission service.

A submission writes one ``Claim`` row, persists any uploaded evidence to
disk, and emits one ``claim.submitted`` audit event — all in one
transaction. The initial status is determined by the pool's
``governance_config`` tier list:

* tier with ``scheme = auto_approve``  → :data:`ClaimStatus.approved`
* anything else                        → :data:`ClaimStatus.voting`

Voting itself (collecting member decisions, advancing to approved /
rejected) is wired in step 6.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    Member,
    MemberStatus,
    Pool,
)
from api.storage import claim_evidence_dir, safe_filename
from api.webhooks import dispatch_event


def initial_status_for_amount(
    governance_config: dict, amount_cents: int
) -> tuple[ClaimStatus, str]:
    """Return ``(initial_status, scheme_name)`` for a new claim of the given
    amount, walking the wizard's tier list in order. The first tier whose
    ``max_amount_cents`` is ``None`` (catch-all) or ``>= amount_cents`` wins.
    Falls back to ``unanimous`` voting if nothing matches — strictest path.
    """
    for tier in governance_config.get("tiers", []):
        max_cents = tier.get("max_amount_cents")
        if max_cents is None or amount_cents <= max_cents:
            scheme = tier["scheme"]
            status = (
                ClaimStatus.approved
                if scheme == "auto_approve"
                else ClaimStatus.voting
            )
            return status, scheme
    return ClaimStatus.voting, "unanimous"


def _save_evidence(claim_id: int, files: Iterable[tuple[str, bytes]]) -> list[str]:
    """Save each ``(filename, content_bytes)`` pair under the claim's evidence
    directory. Returns the list of relative paths (from the uploads root)
    that should be stored in ``Claim.evidence_uris``.
    """
    target_dir = claim_evidence_dir(claim_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for i, (filename, content) in enumerate(files):
        safe = safe_filename(filename)
        target = target_dir / f"{i}_{safe}"
        target.write_bytes(content)
        saved.append(f"claims/{claim_id}/{i}_{safe}")
    return saved


def submit_claim(
    db: Session,
    *,
    pool_id: int,
    member_id: int,
    amount_cents: int,
    category: str,
    description: str,
    occurred_at: datetime,
    files: list[tuple[str, bytes]] | None = None,
    now: datetime | None = None,
) -> Claim:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    category = category.strip()
    description = description.strip()
    if not category:
        raise ValueError("category is required")
    if not description:
        raise ValueError("description is required")

    member = db.get(Member, member_id)
    if member is None or member.pool_id != pool_id:
        raise ValueError("member not in pool")
    if member.status != MemberStatus.active:
        raise ValueError("only active members can submit claims")

    pool = db.get(Pool, pool_id)
    if pool is None:
        raise ValueError("pool not found")

    status, scheme = initial_status_for_amount(pool.governance_config, amount_cents)
    now = now or datetime.now(timezone.utc)

    claim = Claim(
        pool_id=pool_id,
        member_id=member_id,
        amount_requested=amount_cents,
        category=category,
        description=description,
        evidence_uris=[],
        occurred_at=occurred_at,
        submitted_at=now,
        status=status,
    )
    db.add(claim)
    db.flush()  # need claim.id before we can lay out the evidence dir

    if files:
        claim.evidence_uris = _save_evidence(claim.id, files)

    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=member_id,
            kind="claim.submitted",
            payload_json={
                "claim_id": claim.id,
                "amount_cents": amount_cents,
                "category": category,
                "scheme": scheme,
                "initial_status": status.value,
                "evidence_count": len(claim.evidence_uris),
            },
            recorded_at=now,
        )
    )
    db.commit()
    db.refresh(claim)

    # Outbound webhooks (fire-and-log, never raise). ``claim.submitted`` always;
    # ``claim.approved`` follows immediately when the small tier auto-approves.
    submitted_payload = {
        "claim_id": claim.id,
        "member_id": member_id,
        "amount_cents": amount_cents,
        "category": category,
        "occurred_at": occurred_at.isoformat(),
        "scheme": scheme,
        "initial_status": status.value,
    }
    dispatch_event(db, pool_id, "claim.submitted", submitted_payload)
    if status == ClaimStatus.approved:
        dispatch_event(db, pool_id, "claim.approved", {
            "claim_id": claim.id,
            "member_id": member_id,
            "amount_cents": amount_cents,
            "scheme": scheme,
        })

    return claim
