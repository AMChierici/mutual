"""Service-layer tests for vote casting and the claim state machine."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.claims import submit_claim
from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    Member,
    MemberRole,
    MemberStatus,
    Vote,
    VoteDecision,
)
from api.voting import (
    cast_vote,
    eligible_voter_count,
    list_pending_for_member,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _voting_claim(session, pool, submitter, amount_cents=80_000) -> Claim:
    """Submit a claim that lands in voting (majority tier in our fixtures)."""
    return submit_claim(
        session,
        pool_id=pool.id,
        member_id=submitter.id,
        amount_cents=amount_cents,
        category="dental",
        description="root canal",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


def _activate(session, members):
    """Flip the conftest's `members` fixture from invited to active so they
    count as eligible voters."""
    for m in members:
        m.status = MemberStatus.active
    session.commit()


# ---------------------------------------------------------------------------
# eligible_voter_count
# ---------------------------------------------------------------------------
def test_eligible_voter_count_includes_admin_and_members(session, pool, admin, members):
    assert eligible_voter_count(session, pool.id) == 1 + len(members)  # 1 admin + 3 members


def test_eligible_voter_count_excludes_observers(session, pool, admin, members):
    members[0].role = MemberRole.observer
    session.commit()
    assert eligible_voter_count(session, pool.id) == 1 + len(members) - 1


def test_eligible_voter_count_excludes_invited_or_inactive(session, pool, admin, members):
    members[0].status = MemberStatus.invited
    members[1].status = MemberStatus.inactive
    session.commit()
    assert eligible_voter_count(session, pool.id) == 1 + 1  # admin + members[2]


# ---------------------------------------------------------------------------
# cast_vote — golden paths
# ---------------------------------------------------------------------------
def test_cast_vote_records_vote_row(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin)
    v = cast_vote(
        session,
        claim_id=claim.id,
        member_id=members[0].id,
        decision=VoteDecision.approve,
        reason="seems legit",
    )
    assert v.id is not None
    assert v.decision == VoteDecision.approve
    assert v.reason == "seems legit"


def test_cast_vote_writes_audit_event(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin)
    cast_vote(
        session,
        claim_id=claim.id,
        member_id=members[0].id,
        decision=VoteDecision.approve,
    )
    audit = session.query(AuditEvent).filter_by(kind="vote.cast").one()
    assert audit.actor_member_id == members[0].id
    assert audit.payload_json["claim_id"] == claim.id
    assert audit.payload_json["decision"] == "approve"


def test_cast_vote_first_below_threshold_keeps_voting(session, pool, admin, members):
    """4 eligible (admin + 3 members), majority needs > 50% = 3. One yes ≠ done."""
    claim = _voting_claim(session, pool, admin)
    cast_vote(
        session, claim_id=claim.id,
        member_id=members[0].id, decision=VoteDecision.approve,
    )
    session.refresh(claim)
    assert claim.status == ClaimStatus.voting


def test_cast_vote_majority_threshold_flips_to_approved(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin)
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.approve)
    cast_vote(session, claim_id=claim.id, member_id=members[0].id, decision=VoteDecision.approve)
    cast_vote(session, claim_id=claim.id, member_id=members[1].id, decision=VoteDecision.approve)
    session.refresh(claim)
    assert claim.status == ClaimStatus.approved
    # Audit fired the right event
    kinds = [e.kind for e in session.query(AuditEvent).all()]
    assert "claim.approved" in kinds


def test_cast_vote_majority_rejection_flips_to_rejected(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin)
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.reject)
    cast_vote(session, claim_id=claim.id, member_id=members[0].id, decision=VoteDecision.reject)
    cast_vote(session, claim_id=claim.id, member_id=members[1].id, decision=VoteDecision.reject)
    session.refresh(claim)
    assert claim.status == ClaimStatus.rejected
    kinds = [e.kind for e in session.query(AuditEvent).all()]
    assert "claim.rejected" in kinds


def test_cast_vote_unanimous_one_reject_flips_to_rejected(session, pool, admin, members):
    """A claim that lands in the unanimous tier (>$1000 in our fixtures)."""
    claim = _voting_claim(session, pool, admin, amount_cents=200_000)
    cast_vote(
        session, claim_id=claim.id,
        member_id=members[0].id, decision=VoteDecision.reject, reason="no",
    )
    session.refresh(claim)
    assert claim.status == ClaimStatus.rejected


def test_cast_vote_unanimous_all_approve_flips_to_approved(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin, amount_cents=200_000)
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.approve)
    cast_vote(session, claim_id=claim.id, member_id=members[0].id, decision=VoteDecision.approve)
    cast_vote(session, claim_id=claim.id, member_id=members[1].id, decision=VoteDecision.approve)
    session.refresh(claim)
    assert claim.status == ClaimStatus.voting  # still 1 to go
    cast_vote(session, claim_id=claim.id, member_id=members[2].id, decision=VoteDecision.approve)
    session.refresh(claim)
    assert claim.status == ClaimStatus.approved


# ---------------------------------------------------------------------------
# cast_vote — guards
# ---------------------------------------------------------------------------
def test_cast_vote_rejects_unknown_claim(session, members):
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=99999,
            member_id=members[0].id, decision=VoteDecision.approve,
        )


def test_cast_vote_rejects_claim_not_in_voting(session, pool, admin, members):
    """An already-approved claim can't be voted on."""
    claim = _voting_claim(session, pool, admin, amount_cents=5_000)  # auto-approve tier
    assert claim.status == ClaimStatus.approved
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=claim.id,
            member_id=members[0].id, decision=VoteDecision.approve,
        )


def test_cast_vote_rejects_observer(session, pool, admin, members):
    members[0].role = MemberRole.observer
    session.commit()
    claim = _voting_claim(session, pool, admin)
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=claim.id,
            member_id=members[0].id, decision=VoteDecision.approve,
        )


def test_cast_vote_rejects_inactive_voter(session, pool, admin, members):
    members[0].status = MemberStatus.inactive
    session.commit()
    claim = _voting_claim(session, pool, admin)
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=claim.id,
            member_id=members[0].id, decision=VoteDecision.approve,
        )


def test_cast_vote_rejects_member_in_other_pool(session, pool, admin):
    from api.orm import Pool
    other = Pool(name="O", currency="USD", governance_config=pool.governance_config)
    session.add(other)
    session.commit()
    foreign = Member(
        pool_id=other.id, display_name="X",
        role=MemberRole.member, status=MemberStatus.active,
    )
    session.add(foreign)
    session.commit()
    claim = _voting_claim(session, pool, admin)
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=claim.id,
            member_id=foreign.id, decision=VoteDecision.approve,
        )


def test_cast_vote_rejects_double_vote_from_same_member(session, pool, admin, members):
    claim = _voting_claim(session, pool, admin)
    cast_vote(
        session, claim_id=claim.id,
        member_id=members[0].id, decision=VoteDecision.approve,
    )
    with pytest.raises(ValueError):
        cast_vote(
            session, claim_id=claim.id,
            member_id=members[0].id, decision=VoteDecision.reject,
        )


def test_cast_vote_submitter_is_eligible(session, pool, admin, members):
    """v0 lets the submitter vote on their own claim."""
    claim = _voting_claim(session, pool, admin)
    cast_vote(
        session, claim_id=claim.id,
        member_id=admin.id, decision=VoteDecision.approve,
    )
    assert session.query(Vote).filter_by(member_id=admin.id).count() == 1


# ---------------------------------------------------------------------------
# list_pending_for_member
# ---------------------------------------------------------------------------
def test_list_pending_returns_voting_claims_member_has_not_voted(
    session, pool, admin, members
):
    claim_a = _voting_claim(session, pool, admin)
    claim_b = _voting_claim(session, pool, admin)
    # members[0] votes on A but not B
    cast_vote(
        session, claim_id=claim_a.id,
        member_id=members[0].id, decision=VoteDecision.approve,
    )
    pending = list_pending_for_member(session, pool_id=pool.id, member_id=members[0].id)
    ids = {c.id for c in pending}
    assert claim_a.id not in ids
    assert claim_b.id in ids


def test_list_pending_excludes_already_decided_claims(
    session, pool, admin, members
):
    auto_approved = _voting_claim(session, pool, admin, amount_cents=5_000)  # auto
    assert auto_approved.status == ClaimStatus.approved
    pending = list_pending_for_member(
        session, pool_id=pool.id, member_id=members[0].id
    )
    assert auto_approved.id not in {c.id for c in pending}


def test_list_pending_for_observer_is_empty(session, pool, admin, members):
    members[0].role = MemberRole.observer
    session.commit()
    _voting_claim(session, pool, admin)
    assert list_pending_for_member(session, pool_id=pool.id, member_id=members[0].id) == []
