"""End-to-end webhook dispatch from each lifecycle hook."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import api.webhooks as webhooks_module
from api.claims import submit_claim
from api.contributions import record_contribution
from api.orm import VoteDecision
from api.payouts import record_payout
from api.voting import cast_vote
from api.webhooks import set_webhook_url


@pytest.fixture
def sink(monkeypatch):
    """Capture every dispatched webhook call (drops network)."""
    calls: list[dict] = []

    def fake(url, body, timeout=5.0):
        calls.append({"url": url, "body": json.loads(body)})
        return 200, None

    monkeypatch.setattr(webhooks_module, "_post_webhook", fake)
    return calls


@pytest.fixture
def configured_pool(session, pool):
    set_webhook_url(session, pool.id, "https://hook.example/x")
    return pool


def _events(sink):
    return [c["body"]["event"] for c in sink]


# ---------------------------------------------------------------------------
# Claim submission
# ---------------------------------------------------------------------------
def test_submit_claim_emits_claim_submitted(session, configured_pool, admin, sink):
    submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    assert _events(sink) == ["claim.submitted"]
    body = sink[0]["body"]
    assert body["payload"]["amount_cents"] == 80_000
    assert body["payload"]["category"] == "dental"
    assert "claim_id" in body["payload"]


def test_auto_approved_claim_emits_submitted_then_approved(
    session, configured_pool, admin, sink
):
    submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=5_000,  # auto_approve tier
        category="medical", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    assert _events(sink) == ["claim.submitted", "claim.approved"]


# ---------------------------------------------------------------------------
# Voting transitions
# ---------------------------------------------------------------------------
def test_majority_threshold_emits_claim_approved(
    session, configured_pool, admin, members, sink
):
    claim = submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    sink.clear()  # drop the claim.submitted call from setup
    # 4 eligible voters, majority = 3
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.approve)
    cast_vote(session, claim_id=claim.id, member_id=members[0].id, decision=VoteDecision.approve)
    assert "claim.approved" not in _events(sink)  # below threshold
    cast_vote(session, claim_id=claim.id, member_id=members[1].id, decision=VoteDecision.approve)
    assert "claim.approved" in _events(sink)


def test_majority_threshold_emits_claim_rejected(
    session, configured_pool, admin, members, sink
):
    claim = submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    sink.clear()
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.reject)
    cast_vote(session, claim_id=claim.id, member_id=members[0].id, decision=VoteDecision.reject)
    cast_vote(session, claim_id=claim.id, member_id=members[1].id, decision=VoteDecision.reject)
    assert "claim.rejected" in _events(sink)


def test_pending_vote_does_not_emit(session, configured_pool, admin, members, sink):
    claim = submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=80_000, category="dental", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    sink.clear()
    cast_vote(session, claim_id=claim.id, member_id=admin.id, decision=VoteDecision.approve)
    # Below majority threshold → no transition event
    assert _events(sink) == []


# ---------------------------------------------------------------------------
# Payouts
# ---------------------------------------------------------------------------
def test_record_payout_emits_claim_paid(session, configured_pool, admin, sink):
    record_contribution(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-01", recorded_by=admin.id,
    )
    claim = submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    sink.clear()
    record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000, recorded_by=admin.id,
    )
    assert _events(sink) == ["claim.paid"]
    body = sink[0]["body"]
    assert body["payload"]["claim_id"] == claim.id
    assert body["payload"]["amount_paid_cents"] == 5_000


# ---------------------------------------------------------------------------
# No-config = no calls
# ---------------------------------------------------------------------------
def test_no_webhook_url_means_no_dispatch(session, pool, admin, sink):
    """Same flow but no URL configured — sink stays empty."""
    submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    assert sink == []


# ---------------------------------------------------------------------------
# A failing webhook does NOT break the calling flow
# ---------------------------------------------------------------------------
def test_failing_webhook_does_not_break_submit_claim(
    session, configured_pool, admin, monkeypatch
):
    def fail(url, body, timeout=5.0):
        return None, "connection refused"

    monkeypatch.setattr(webhooks_module, "_post_webhook", fail)
    claim = submit_claim(
        session, pool_id=configured_pool.id, member_id=admin.id,
        amount_cents=5_000, category="medical", description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    assert claim.id is not None  # submission still succeeded
