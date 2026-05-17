"""HTTP-level tests for the pool-admin members page (M3)."""
from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.orm import (
    AuditEvent,
    LoginToken,
    Membership,
    MemberRole,
    MemberStatus,
    Pool,
    User,
)


@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    """An HTTP client carrying a session cookie for an active member
    (members[0])."""
    tok = create_login_token(session, members[0].user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


# ---------------------------------------------------------------------------
# GET /pools/{slug}/members
# ---------------------------------------------------------------------------
async def test_list_unauthenticated_is_401(client, pool):
    r = await client.get(f"/pools/{pool.slug}/members")
    assert r.status_code == 401


async def test_list_non_admin_is_403(member_client, pool):
    r = await member_client.get(f"/pools/{pool.slug}/members")
    assert r.status_code == 403


async def test_list_admin_renders_table(admin_client, session, pool, admin, members):
    r = await admin_client.get(f"/pools/{pool.slug}/members")
    assert r.status_code == 200
    body = r.text
    for m in (admin, *members):
        assert m.display_name in body
    assert "Invite a member" in body


# ---------------------------------------------------------------------------
# GET / POST /pools/{slug}/members/invite
# ---------------------------------------------------------------------------
async def test_invite_form_admin_renders(admin_client, pool):
    r = await admin_client.get(f"/pools/{pool.slug}/members/invite")
    assert r.status_code == 200
    assert 'name="display_name"' in r.text
    assert 'name="email"' in r.text


async def test_invite_creates_user_membership_and_token(
    admin_client, session, pool
):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/invite",
        data={"display_name": "Eli", "email": "eli@example.test", "role": "member"},
    )
    assert r.status_code == 200
    assert "/auth/login/" in r.text  # copyable login URL shown

    session.expire_all()
    user = session.query(User).filter_by(email="eli@example.test").one()
    membership = session.query(Membership).filter_by(user_id=user.id).one()
    assert membership.pool_id == pool.id
    assert membership.role == MemberRole.member
    assert membership.status == MemberStatus.invited
    token = session.query(LoginToken).filter_by(user_id=user.id).one()
    assert token.used_at is None

    # Two audit events: member.invited + auth.magic_link_minted
    kinds = {
        e.kind
        for e in session.query(AuditEvent)
        .filter(AuditEvent.payload_json["target_member_id"].as_integer() == membership.id)
        .all()
    }
    assert "member.invited" in kinds
    assert "auth.magic_link_minted" in kinds


async def test_invite_reuses_existing_user_by_email(
    admin_client, session, pool, admin
):
    """Inviting an existing user (different pool) attaches a membership;
    does NOT create a duplicate User row."""
    # Stand up a second pool with its own admin who has a User row.
    other_pool = Pool(
        slug="other-pool-invite",
        name="Other",
        currency="USD",
        governance_config={},
    )
    session.add(other_pool)
    session.flush()
    existing_user = User(email="shared@example.test", display_name="Shared")
    session.add(existing_user)
    session.flush()
    session.add(Membership(
        user_id=existing_user.id, pool_id=other_pool.id,
        display_name="Shared", role=MemberRole.member, status=MemberStatus.active,
    ))
    session.commit()
    user_count_before = session.query(User).count()

    r = await admin_client.post(
        f"/pools/{pool.slug}/members/invite",
        data={"display_name": "Shared", "email": "shared@example.test", "role": "member"},
    )
    assert r.status_code == 200

    session.expire_all()
    assert session.query(User).count() == user_count_before  # no new User
    # Two memberships for shared@: one in each pool.
    memberships = (
        session.query(Membership).filter_by(user_id=existing_user.id).all()
    )
    assert {m.pool_id for m in memberships} == {other_pool.id, pool.id}


async def test_invite_rejects_duplicate_membership_in_same_pool(
    admin_client, session, pool, admin
):
    """Re-inviting an email that already has a membership in this pool
    refuses, with a clear error."""
    # admin was created in conftest with email like 'admin-1@example.test'.
    admin_user = session.get(User, admin.user_id)
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/invite",
        data={
            "display_name": "Admin Again",
            "email": admin_user.email,
            "role": "member",
        },
    )
    assert r.status_code == 400
    assert "already a member" in r.text


async def test_invite_requires_email(admin_client, pool):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/invite",
        data={"display_name": "No Email", "email": "", "role": "member"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/role — change role
# ---------------------------------------------------------------------------
async def test_change_role_admin_demotes_member_to_observer(
    admin_client, session, pool, members
):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{members[0].id}/role",
        data={"role": "observer"},
    )
    assert r.status_code == 200
    session.expire_all()
    assert session.get(Membership, members[0].id).role == MemberRole.observer
    audit = (
        session.query(AuditEvent)
        .filter_by(kind="member.role_changed")
        .order_by(AuditEvent.id.desc())
        .first()
    )
    assert audit.payload_json["target_member_id"] == members[0].id
    assert audit.payload_json["new_role"] == "observer"


async def test_change_role_blocks_last_admin_demotion(
    admin_client, session, pool, admin
):
    """admin is the only active admin; demoting them must fail."""
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{admin.id}/role",
        data={"role": "member"},
    )
    assert r.status_code == 400
    session.expire_all()
    assert session.get(Membership, admin.id).role == MemberRole.admin


async def test_change_role_allows_admin_demotion_when_another_admin_exists(
    admin_client, session, pool, admin, members
):
    """Promote members[0] to admin, then demote the original admin — OK."""
    members[0].role = MemberRole.admin
    session.commit()
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{admin.id}/role",
        data={"role": "member"},
    )
    assert r.status_code == 200
    session.expire_all()
    assert session.get(Membership, admin.id).role == MemberRole.member


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/status — deactivate
# ---------------------------------------------------------------------------
async def test_status_deactivate_member(admin_client, session, pool, members):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{members[0].id}/status",
        data={"status": "inactive"},
    )
    assert r.status_code == 200
    session.expire_all()
    assert session.get(Membership, members[0].id).status == MemberStatus.inactive
    audit = (
        session.query(AuditEvent)
        .filter_by(kind="member.deactivated")
        .order_by(AuditEvent.id.desc())
        .first()
    )
    assert audit.payload_json["target_member_id"] == members[0].id


async def test_status_blocks_last_admin_deactivation(
    admin_client, session, pool, admin
):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{admin.id}/status",
        data={"status": "inactive"},
    )
    assert r.status_code == 400
    session.expire_all()
    assert session.get(Membership, admin.id).status == MemberStatus.active


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/magic-link — re-issue
# ---------------------------------------------------------------------------
async def test_magic_link_admin_mints_fresh_token(
    admin_client, session, pool, members
):
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{members[0].id}/magic-link",
    )
    assert r.status_code == 200
    assert "/auth/login/" in r.text  # link shown in flash
    session.expire_all()
    tokens = session.query(LoginToken).filter_by(user_id=members[0].user_id).all()
    assert len(tokens) >= 1


async def test_magic_link_inactive_member_is_400(
    admin_client, session, pool, members
):
    members[0].status = MemberStatus.inactive
    session.commit()
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{members[0].id}/magic-link",
    )
    assert r.status_code == 400


async def test_member_in_other_pool_cannot_be_targeted(
    admin_client, session, pool
):
    """A pool A admin trying to mutate a pool B membership gets 404, never 403."""
    other_pool = Pool(
        slug="other-pool-target",
        name="Other",
        currency="USD",
        governance_config={},
    )
    session.add(other_pool)
    session.flush()
    foreign_user = User(email="foreign-target@example.test", display_name="F")
    session.add(foreign_user)
    session.flush()
    foreign = Membership(
        user_id=foreign_user.id, pool_id=other_pool.id, display_name="F",
        role=MemberRole.member, status=MemberStatus.active,
    )
    session.add(foreign)
    session.commit()
    r = await admin_client.post(
        f"/pools/{pool.slug}/members/{foreign.id}/role",
        data={"role": "observer"},
    )
    assert r.status_code == 404
