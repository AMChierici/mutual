"""Service-layer tests for claim submission and tier routing."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.claims import (
    initial_status_for_amount,
    submit_claim,
)
from api.orm import (
    AuditEvent,
    Claim,
    ClaimStatus,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
)


# ---------------------------------------------------------------------------
# initial_status_for_amount
# ---------------------------------------------------------------------------
_GOVERNANCE = {
    "tiers": [
        {"max_amount_cents": 10_000, "scheme": "auto_approve"},
        {"max_amount_cents": 100_000, "scheme": "majority"},
        {"max_amount_cents": None, "scheme": "unanimous"},
    ]
}


def test_routes_small_claim_to_auto_approve():
    status, scheme = initial_status_for_amount(_GOVERNANCE, 5_000)
    assert status == ClaimStatus.approved
    assert scheme == "auto_approve"


def test_routes_at_tier_boundary_to_first_tier():
    status, scheme = initial_status_for_amount(_GOVERNANCE, 10_000)
    assert scheme == "auto_approve"
    assert status == ClaimStatus.approved


def test_routes_mid_claim_to_majority_voting():
    status, scheme = initial_status_for_amount(_GOVERNANCE, 50_000)
    assert status == ClaimStatus.voting
    assert scheme == "majority"


def test_routes_huge_claim_to_unanimous_catch_all():
    status, scheme = initial_status_for_amount(_GOVERNANCE, 500_000)
    assert status == ClaimStatus.voting
    assert scheme == "unanimous"


def test_falls_back_to_unanimous_when_no_catch_all_matches():
    """Defensive: if all tiers have a max and none matches, fall safe."""
    no_catchall = {
        "tiers": [
            {"max_amount_cents": 10_000, "scheme": "auto_approve"},
            {"max_amount_cents": 100_000, "scheme": "majority"},
        ]
    }
    status, scheme = initial_status_for_amount(no_catchall, 10_000_000)
    assert status == ClaimStatus.voting
    assert scheme == "unanimous"


def test_empty_governance_falls_back_to_unanimous():
    status, scheme = initial_status_for_amount({}, 100)
    assert status == ClaimStatus.voting
    assert scheme == "unanimous"


# ---------------------------------------------------------------------------
# submit_claim — golden path
# ---------------------------------------------------------------------------
@pytest.fixture
def governed_pool(session):
    p = Pool(name="P", currency="USD", governance_config=_GOVERNANCE)
    session.add(p)
    session.commit()
    return p


@pytest.fixture
def active_member(session, governed_pool):
    m = Member(
        pool_id=governed_pool.id,
        display_name="Bo",
        role=MemberRole.member,
        status=MemberStatus.active,
    )
    session.add(m)
    session.commit()
    return m


def _occurred() -> datetime:
    return datetime(2026, 4, 15, tzinfo=timezone.utc)


def test_submit_claim_creates_row_with_routed_status(
    session, governed_pool, active_member
):
    claim = submit_claim(
        session,
        pool_id=governed_pool.id,
        member_id=active_member.id,
        amount_cents=5_000,
        category="medical",
        description="Pharmacy receipt",
        occurred_at=_occurred(),
    )
    assert claim.id is not None
    assert claim.status == ClaimStatus.approved  # auto_approve tier
    assert claim.amount_requested == 5_000
    assert claim.category == "medical"
    assert claim.evidence_uris == []


def test_submit_claim_routes_via_voting_for_larger_amount(
    session, governed_pool, active_member
):
    claim = submit_claim(
        session,
        pool_id=governed_pool.id,
        member_id=active_member.id,
        amount_cents=80_000,
        category="dental",
        description="Root canal",
        occurred_at=_occurred(),
    )
    assert claim.status == ClaimStatus.voting


def test_submit_claim_writes_audit_event(session, governed_pool, active_member):
    claim = submit_claim(
        session,
        pool_id=governed_pool.id,
        member_id=active_member.id,
        amount_cents=80_000,
        category="dental",
        description="x",
        occurred_at=_occurred(),
    )
    audit = (
        session.query(AuditEvent).filter_by(kind="claim.submitted").one()
    )
    assert audit.actor_member_id == active_member.id
    assert audit.payload_json["claim_id"] == claim.id
    assert audit.payload_json["scheme"] == "majority"
    assert audit.payload_json["initial_status"] == "voting"
    assert audit.payload_json["evidence_count"] == 0


def test_submit_claim_persists_files_and_records_paths(
    session, governed_pool, active_member, tmp_path, monkeypatch
):
    monkeypatch.setenv("MUTUAL_UPLOADS_DIR", str(tmp_path / "u"))
    claim = submit_claim(
        session,
        pool_id=governed_pool.id,
        member_id=active_member.id,
        amount_cents=5_000,
        category="medical",
        description="Receipts",
        occurred_at=_occurred(),
        files=[
            ("receipt.jpg", b"\xff\xd8\xff\xe0fakejpeg"),
            ("photo of front.png", b"\x89PNG\r\n\x1a\nfake"),
        ],
    )
    assert len(claim.evidence_uris) == 2
    p0 = tmp_path / "u" / claim.evidence_uris[0]
    p1 = tmp_path / "u" / claim.evidence_uris[1]
    assert p0.is_file() and p0.read_bytes().startswith(b"\xff\xd8")
    assert p1.is_file() and p1.read_bytes().startswith(b"\x89PNG")
    # Sanitization stripped the space in "photo of front.png"
    assert " " not in p1.name


def test_submit_claim_rejects_zero_amount(session, governed_pool, active_member):
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=active_member.id,
            amount_cents=0,
            category="medical",
            description="x",
            occurred_at=_occurred(),
        )


def test_submit_claim_rejects_empty_category(session, governed_pool, active_member):
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=active_member.id,
            amount_cents=100,
            category="   ",
            description="x",
            occurred_at=_occurred(),
        )


def test_submit_claim_rejects_empty_description(session, governed_pool, active_member):
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=active_member.id,
            amount_cents=100,
            category="medical",
            description="",
            occurred_at=_occurred(),
        )


def test_submit_claim_rejects_inactive_member(session, governed_pool):
    inactive = Member(
        pool_id=governed_pool.id,
        display_name="X",
        role=MemberRole.member,
        status=MemberStatus.inactive,
    )
    session.add(inactive)
    session.commit()
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=inactive.id,
            amount_cents=100,
            category="medical",
            description="x",
            occurred_at=_occurred(),
        )


def test_submit_claim_rejects_member_in_other_pool(session, governed_pool):
    other = Pool(name="O", currency="USD", governance_config=_GOVERNANCE)
    session.add(other)
    session.commit()
    foreign = Member(
        pool_id=other.id, display_name="F",
        role=MemberRole.member, status=MemberStatus.active,
    )
    session.add(foreign)
    session.commit()
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=foreign.id,
            amount_cents=100,
            category="medical",
            description="x",
            occurred_at=_occurred(),
        )


def test_submit_claim_rolls_back_on_invalid_amount(
    session, governed_pool, active_member
):
    with pytest.raises(ValueError):
        submit_claim(
            session,
            pool_id=governed_pool.id,
            member_id=active_member.id,
            amount_cents=-5,
            category="medical",
            description="x",
            occurred_at=_occurred(),
        )
    assert session.query(Claim).count() == 0
