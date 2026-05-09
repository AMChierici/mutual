"""HTTP-level tests for voting and the pending-claims list."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.claims import submit_claim
from api.orm import Claim, ClaimStatus, Vote, VoteDecision
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    """Active-member session for members[0]."""
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


@pytest.fixture
def voting_claim(session, pool, admin):
    return submit_claim(
        session,
        pool_id=pool.id,
        member_id=admin.id,
        amount_cents=80_000,  # majority tier in fixture config
        category="dental",
        description="root canal",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


@pytest.fixture
def auto_approved_claim(session, pool, admin):
    return submit_claim(
        session,
        pool_id=pool.id,
        member_id=admin.id,
        amount_cents=5_000,  # auto_approve tier
        category="medical",
        description="aspirin",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# GET /claims/pending
# ---------------------------------------------------------------------------
async def test_get_pending_unauthenticated_is_401(client):
    r = await client.get("/claims/pending")
    assert r.status_code == 401


async def test_get_pending_lists_voting_claims(member_client, voting_claim):
    r = await member_client.get("/claims/pending")
    assert r.status_code == 200
    assert "root canal" in r.text
    assert "Approve" in r.text
    assert "Reject" in r.text


async def test_get_pending_excludes_auto_approved(member_client, auto_approved_claim):
    r = await member_client.get("/claims/pending")
    assert r.status_code == 200
    assert "aspirin" not in r.text


async def test_get_pending_excludes_claims_member_already_voted(
    member_client, session, voting_claim, members
):
    from api.voting import cast_vote
    cast_vote(
        session, claim_id=voting_claim.id,
        member_id=members[0].id, decision=VoteDecision.approve,
    )
    r = await member_client.get("/claims/pending")
    assert r.status_code == 200
    assert "root canal" not in r.text


async def test_get_pending_empty_renders_no_claims_message(member_client):
    r = await member_client.get("/claims/pending")
    assert r.status_code == 200
    assert "No claims" in r.text or "no claims" in r.text


# ---------------------------------------------------------------------------
# POST /claims/{id}/vote
# ---------------------------------------------------------------------------
async def test_post_vote_approve_records_and_redirects(
    member_client, session, voting_claim, members
):
    r = await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "approve", "reason": "looks reasonable"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/claims/pending"

    session.expire_all()
    v = session.query(Vote).filter_by(
        claim_id=voting_claim.id, member_id=members[0].id
    ).one()
    assert v.decision == VoteDecision.approve
    assert v.reason == "looks reasonable"


async def test_post_vote_reject_records_decision(member_client, session, voting_claim, members):
    r = await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "reject"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    v = session.query(Vote).one()
    assert v.decision == VoteDecision.reject
    assert v.reason is None


async def test_post_vote_blank_reason_stored_as_null(
    member_client, session, voting_claim
):
    await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "approve", "reason": "   "},
    )
    session.expire_all()
    v = session.query(Vote).one()
    assert v.reason is None


async def test_post_vote_unauthenticated_is_401(client, voting_claim):
    r = await client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "approve"},
    )
    assert r.status_code == 401


async def test_post_vote_on_unknown_claim_is_404(member_client):
    r = await member_client.post(
        "/claims/99999/vote",
        data={"decision": "approve"},
    )
    assert r.status_code in (400, 404)


async def test_post_vote_on_auto_approved_is_400(
    member_client, auto_approved_claim
):
    r = await member_client.post(
        f"/claims/{auto_approved_claim.id}/vote",
        data={"decision": "approve"},
    )
    assert r.status_code == 400


async def test_post_vote_double_vote_from_same_member_is_400(
    member_client, voting_claim
):
    await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "approve"},
    )
    r = await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "reject"},
    )
    assert r.status_code == 400


async def test_post_vote_invalid_decision_is_400(member_client, voting_claim):
    r = await member_client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "shrug"},
    )
    assert r.status_code == 400


async def test_post_vote_observer_is_403(client, session, members, voting_claim):
    from api.orm import MemberRole
    members[0].role = MemberRole.observer
    session.commit()
    tok = create_login_token(session, members[0].id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    r = await client.post(
        f"/claims/{voting_claim.id}/vote",
        data={"decision": "approve"},
    )
    assert r.status_code in (400, 403)


async def test_majority_threshold_flips_claim_status_via_http(
    client, session, pool, admin, members, voting_claim
):
    """4 eligible voters; majority = 3 approves to flip status."""
    for actor_id in (admin.id, members[0].id, members[1].id):
        client.cookies.delete(SESSION_COOKIE)
        tok = create_login_token(session, actor_id)
        auth_session = consume_login_token(session, tok.token)
        client.cookies.set(SESSION_COOKIE, auth_session.token)
        r = await client.post(
            f"/claims/{voting_claim.id}/vote",
            data={"decision": "approve"},
        )
        assert r.status_code == 303

    session.expire_all()
    fresh = session.get(Claim, voting_claim.id)
    assert fresh.status == ClaimStatus.approved


# ---------------------------------------------------------------------------
# Detail page: vote form is shown only when applicable
# ---------------------------------------------------------------------------
async def test_detail_shows_vote_form_when_pending(member_client, voting_claim):
    r = await member_client.get(f"/claims/{voting_claim.id}")
    assert r.status_code == 200
    assert "Approve" in r.text
    assert "Reject" in r.text


async def test_detail_hides_vote_form_after_voting(
    member_client, session, voting_claim, members
):
    from api.voting import cast_vote
    cast_vote(
        session, claim_id=voting_claim.id,
        member_id=members[0].id, decision=VoteDecision.approve,
    )
    r = await member_client.get(f"/claims/{voting_claim.id}")
    assert r.status_code == 200
    # No active vote-button form for this user anymore
    assert 'name="decision" value="approve"' not in r.text


async def test_detail_hides_vote_form_for_auto_approved(
    member_client, auto_approved_claim
):
    r = await member_client.get(f"/claims/{auto_approved_claim.id}")
    assert r.status_code == 200
    assert 'name="decision" value="approve"' not in r.text
