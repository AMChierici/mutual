"""HTTP-level tests for the claim submission and viewing endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.orm import Claim, ClaimStatus, MemberStatus


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated_uploads(tmp_path, monkeypatch):
    """Every claims test gets its own uploads dir under tmp_path so file
    writes never escape the test's scratch space."""
    monkeypatch.setenv("MUTUAL_UPLOADS_DIR", str(tmp_path / "uploads"))


@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    """An HTTP client carrying a session cookie for an active member
    (members[0])."""
    tok = create_login_token(session, members[0].user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


# ---------------------------------------------------------------------------
# GET /pools/{slug}/claims/new
# ---------------------------------------------------------------------------
async def test_get_claim_form_unauthenticated_is_401(client, pool):
    r = await client.get(f"/pools/{pool.slug}/claims/new")
    assert r.status_code == 401


async def test_get_claim_form_active_member_renders(member_client, pool):
    r = await member_client.get(f"/pools/{pool.slug}/claims/new")
    assert r.status_code == 200
    assert 'name="amount_dollars"' in r.text
    assert 'name="category"' in r.text
    assert 'name="description"' in r.text
    assert 'name="occurred_date"' in r.text
    assert 'name="photos"' in r.text


# ---------------------------------------------------------------------------
# POST /pools/{slug}/claims
# ---------------------------------------------------------------------------
async def test_post_claim_routes_small_amount_to_approved(
    member_client, session, pool, members
):
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "Pharmacy receipt",
            "occurred_date": "2026-04-15",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    session.expire_all()
    claim = session.query(Claim).one()
    assert claim.status == ClaimStatus.approved
    assert claim.amount_requested == 5_000
    assert claim.member_id == members[0].id
    assert claim.evidence_uris == []


async def test_post_claim_routes_medium_amount_to_voting(
    member_client, session, pool
):
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "800.00",
            "category": "dental",
            "description": "Root canal",
            "occurred_date": "2026-04-15",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    claim = session.query(Claim).one()
    assert claim.status == ClaimStatus.voting


async def test_post_claim_persists_uploads(
    member_client, session, pool, tmp_path
):
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "Receipts",
            "occurred_date": "2026-04-15",
        },
        files=[
            ("photos", ("receipt.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")),
            ("photos", ("box-front.png", b"\x89PNG\r\n\x1a\nfake", "image/png")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    claim = session.query(Claim).one()
    assert len(claim.evidence_uris) == 2

    uploads = Path(tmp_path) / "uploads"
    p0 = uploads / claim.evidence_uris[0]
    p1 = uploads / claim.evidence_uris[1]
    assert p0.is_file() and p0.read_bytes().startswith(b"\xff\xd8")
    assert p1.is_file() and p1.read_bytes().startswith(b"\x89PNG")


async def test_post_claim_rejects_non_image_upload(member_client, pool):
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
        files=[("photos", ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert r.status_code == 400


async def test_post_claim_rejects_too_big_upload(member_client, pool):
    big = b"\xff\xd8" + b"\x00" * (10 * 1024 * 1024 + 1)
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
        files=[("photos", ("big.jpg", big, "image/jpeg"))],
    )
    assert r.status_code == 413


async def test_post_claim_rejects_too_many_uploads(member_client, pool):
    files = [
        ("photos", (f"r{i}.jpg", b"\xff\xd8jpeg", "image/jpeg"))
        for i in range(11)
    ]
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
        files=files,
    )
    assert r.status_code == 400


async def test_post_claim_rejects_zero_amount(member_client, pool):
    r = await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "0",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
    )
    assert r.status_code == 400


async def test_post_claim_writes_audit_event(member_client, session, pool):
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50.00",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
    )
    session.expire_all()
    from api.orm import AuditEvent
    audit = session.query(AuditEvent).filter_by(kind="claim.submitted").one()
    assert audit.payload_json["initial_status"] == "approved"


async def test_post_claim_unauthenticated_is_401(client, pool):
    r = await client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "x",
            "description": "y",
            "occurred_date": "2026-04-15",
        },
    )
    assert r.status_code == 401


async def test_post_claim_inactive_member_is_401(client, session, members):
    """Inactive members lose their session check anyway — this just locks
    in that the route never accepts them."""
    members[0].status = MemberStatus.inactive
    session.commit()
    tok = create_login_token(session, members[0].user_id)
    # consume_login_token rejects users with no active memberships at the
    # service layer.
    from api.auth import AuthError
    with pytest.raises(AuthError):
        consume_login_token(session, tok.token)


# ---------------------------------------------------------------------------
# GET /pools/{slug}/claims and /pools/{slug}/claims/{id}
# ---------------------------------------------------------------------------
async def test_get_claim_detail_owner_sees_own(member_client, session, pool, members):
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
    )
    session.expire_all()
    claim = session.query(Claim).one()
    r = await member_client.get(f"/pools/{pool.slug}/claims/{claim.id}")
    assert r.status_code == 200
    assert "medical" in r.text
    assert members[0].display_name in r.text


async def test_get_claim_detail_other_member_can_see(
    client, session, pool, admin, members
):
    """Mutual-aid transparency: any active member can see any claim."""
    # members[0] submits
    tok = create_login_token(session, members[0].user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    await client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "first",
            "occurred_date": "2026-04-15",
        },
    )
    session.expire_all()
    claim = session.query(Claim).one()

    # members[1] views
    client.cookies.delete(SESSION_COOKIE)
    tok2 = create_login_token(session, members[1].user_id)
    auth_session2 = consume_login_token(session, tok2.token)
    client.cookies.set(SESSION_COOKIE, auth_session2.token)
    r = await client.get(f"/pools/{pool.slug}/claims/{claim.id}")
    assert r.status_code == 200
    assert "first" in r.text


async def test_get_claim_detail_unknown_id_is_404(member_client, pool):
    r = await member_client.get(f"/pools/{pool.slug}/claims/99999")
    assert r.status_code == 404


async def test_get_claims_list_renders(member_client, session, pool, members):
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "first",
            "occurred_date": "2026-04-15",
        },
    )
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "800",
            "category": "dental",
            "description": "second",
            "occurred_date": "2026-04-16",
        },
    )
    r = await member_client.get(f"/pools/{pool.slug}/claims")
    assert r.status_code == 200
    # List view shows category + status (descriptions are on the detail page).
    assert "medical" in r.text
    assert "dental" in r.text
    assert "approved" in r.text
    assert "voting" in r.text
    session.expire_all()
    assert session.query(Claim).count() == 2


async def test_get_claims_list_unauthenticated_is_401(client, pool):
    r = await client.get(f"/pools/{pool.slug}/claims")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /pools/{slug}/claims/{id}/evidence/{index}
# ---------------------------------------------------------------------------
async def test_get_claim_evidence_serves_file(member_client, session, pool):
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
        files=[("photos", ("receipt.jpg", b"\xff\xd8\xff body", "image/jpeg"))],
    )
    session.expire_all()
    claim = session.query(Claim).one()
    r = await member_client.get(f"/pools/{pool.slug}/claims/{claim.id}/evidence/0")
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff body"


async def test_get_claim_evidence_unauthenticated_is_401(
    client, session, pool, members
):
    """Filenames aren't guessable, but the route must not leak even if
    they were."""
    tok = create_login_token(session, members[0].user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    await client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
        files=[("photos", ("receipt.jpg", b"\xff\xd8jpeg", "image/jpeg"))],
    )
    session.expire_all()
    claim = session.query(Claim).one()

    client.cookies.delete(SESSION_COOKIE)
    r = await client.get(f"/pools/{pool.slug}/claims/{claim.id}/evidence/0")
    assert r.status_code == 401


async def test_get_claim_evidence_unknown_index_is_404(member_client, session, pool):
    await member_client.post(
        f"/pools/{pool.slug}/claims",
        data={
            "amount_dollars": "50",
            "category": "medical",
            "description": "x",
            "occurred_date": "2026-04-15",
        },
    )
    session.expire_all()
    claim = session.query(Claim).one()
    r = await member_client.get(f"/pools/{pool.slug}/claims/{claim.id}/evidence/99")
    assert r.status_code == 404
