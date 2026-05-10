"""Voting service: cast a vote, run the configured tally, advance the claim
state machine when a threshold is reached.

The scheme used for a claim is re-derived each call from the pool's current
``governance_config`` plus the claim's amount, via
:func:`api.claims.initial_status_for_amount`. That keeps the wizard's tier
config the single source of truth — change it once and every active claim
re-evaluates against the new rules on the next vote.

Eligible voters: members of the pool with ``status=active`` and
``role != observer`` (admin and member roles count). The submitter is
included; v0 lets people vote on their own claims.

Vote mutability: first vote is final. Re-votes raise ``ValueError``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.claims import initial_status_for_amount
from api.webhooks import dispatch_event
from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
    Vote,
    VoteDecision,
)
from governance import TallyOutcome, get_scheme


def _eligible_voter_filter(pool_id: int):
    return (
        Member.pool_id == pool_id,
        Member.status == MemberStatus.active,
        Member.role != MemberRole.observer,
    )


def eligible_voter_count(db: Session, pool_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Member.id)).where(*_eligible_voter_filter(pool_id))
        )
        or 0
    )


def _scheme_for(claim: Claim, pool: Pool) -> str:
    _, scheme = initial_status_for_amount(pool.governance_config, claim.amount_requested)
    return scheme


def _vote_counts(db: Session, claim_id: int) -> tuple[int, int, int]:
    """Returns ``(approve, reject, abstain)`` for the claim."""
    rows = db.execute(
        select(Vote.decision, func.count(Vote.id))
        .where(Vote.claim_id == claim_id)
        .group_by(Vote.decision)
    ).all()
    out = {VoteDecision.approve: 0, VoteDecision.reject: 0, VoteDecision.abstain: 0}
    for decision, count in rows:
        out[decision] = int(count)
    return out[VoteDecision.approve], out[VoteDecision.reject], out[VoteDecision.abstain]


def cast_vote(
    db: Session,
    *,
    claim_id: int,
    member_id: int,
    decision: VoteDecision,
    reason: str | None = None,
    now: datetime | None = None,
) -> Vote:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ValueError("claim not found")
    if claim.status != ClaimStatus.voting:
        raise ValueError(f"claim is not open for voting (status={claim.status.value})")

    member = db.get(Member, member_id)
    if member is None or member.pool_id != claim.pool_id:
        raise ValueError("voter not in pool")
    if member.status != MemberStatus.active:
        raise ValueError("only active members may vote")
    if member.role == MemberRole.observer:
        raise ValueError("observers cannot vote")

    already_voted = db.scalar(
        select(func.count(Vote.id)).where(
            Vote.claim_id == claim_id, Vote.member_id == member_id
        )
    )
    if already_voted:
        raise ValueError("you've already voted on this claim")

    pool = db.get(Pool, claim.pool_id)
    assert pool is not None  # FK on Claim guarantees this
    scheme_name = _scheme_for(claim, pool)
    scheme = get_scheme(scheme_name)

    now = now or datetime.now(timezone.utc)

    vote = Vote(
        claim_id=claim_id,
        member_id=member_id,
        decision=decision,
        reason=(reason or None),
        cast_at=now,
    )
    db.add(vote)
    db.flush()

    db.add(
        AuditEvent(
            pool_id=claim.pool_id,
            actor_member_id=member_id,
            kind="vote.cast",
            payload_json={
                "claim_id": claim_id,
                "decision": decision.value,
                "scheme": scheme_name,
            },
            recorded_at=now,
        )
    )

    approve, reject, abstain = _vote_counts(db, claim_id)
    eligible = eligible_voter_count(db, claim.pool_id)
    outcome = scheme(approve=approve, reject=reject, abstain=abstain, eligible=eligible)

    if outcome == TallyOutcome.approved:
        claim.status = ClaimStatus.approved
        db.add(
            AuditEvent(
                pool_id=claim.pool_id,
                actor_member_id=member_id,
                kind="claim.approved",
                payload_json={
                    "claim_id": claim_id,
                    "scheme": scheme_name,
                    "approve": approve,
                    "reject": reject,
                    "eligible": eligible,
                },
                recorded_at=now,
            )
        )
    elif outcome == TallyOutcome.rejected:
        claim.status = ClaimStatus.rejected
        db.add(
            AuditEvent(
                pool_id=claim.pool_id,
                actor_member_id=member_id,
                kind="claim.rejected",
                payload_json={
                    "claim_id": claim_id,
                    "scheme": scheme_name,
                    "approve": approve,
                    "reject": reject,
                    "eligible": eligible,
                },
                recorded_at=now,
            )
        )

    db.commit()
    db.refresh(vote)

    # Outbound webhook on threshold crossing (not on plain vote.cast).
    if outcome == TallyOutcome.approved:
        dispatch_event(db, claim.pool_id, "claim.approved", {
            "claim_id": claim.id,
            "member_id": claim.member_id,
            "amount_cents": claim.amount_requested,
            "scheme": scheme_name,
            "approve": approve, "reject": reject, "eligible": eligible,
        })
    elif outcome == TallyOutcome.rejected:
        dispatch_event(db, claim.pool_id, "claim.rejected", {
            "claim_id": claim.id,
            "member_id": claim.member_id,
            "amount_cents": claim.amount_requested,
            "scheme": scheme_name,
            "approve": approve, "reject": reject, "eligible": eligible,
        })

    return vote


def list_pending_for_member(
    db: Session, *, pool_id: int, member_id: int
) -> list[Claim]:
    """Claims in the pool currently in ``voting`` status that ``member_id``
    has not yet voted on. Observers always get an empty list."""
    member = db.get(Member, member_id)
    if member is None or member.pool_id != pool_id:
        return []
    if member.status != MemberStatus.active or member.role == MemberRole.observer:
        return []

    voted_subq = select(Vote.claim_id).where(Vote.member_id == member_id)
    return list(
        db.scalars(
            select(Claim)
            .where(
                Claim.pool_id == pool_id,
                Claim.status == ClaimStatus.voting,
                ~Claim.id.in_(voted_subq),
            )
            .order_by(Claim.submitted_at.asc())
        ).all()
    )
