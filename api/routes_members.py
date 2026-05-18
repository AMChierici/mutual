"""Pool-admin members page (M3).

Lets an admin invite, role-change, deactivate, and re-issue magic links
for members of a single pool — all through the web UI. The old
shell-and-/auth/magic-link path still works (and shares the same audit
event taxonomy), but the day-to-day admin flow is now the table here.

Invariants enforced server-side:
* The pool never drops below one active admin (last-admin guard on both
  role-change and deactivate).
* Inviting an email that already has a :class:`User` reuses it — one
  person, one account, many pool memberships.
* Every mutation emits an :class:`AuditEvent` keyed to this pool.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.auth import LOGIN_TOKEN_TTL, mint_token, require_pool_admin
from api.deps import get_db, get_pool_from_slug
from api.orm import (
    AuditEvent,
    LoginToken,
    Membership,
    MemberRole,
    MemberStatus,
    Pool,
    User,
    is_synthetic_email,
)

router = APIRouter(prefix="/pools/{pool_slug}/members", tags=["members"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _audit(
    db: Session,
    *,
    pool_id: int,
    actor_id: int,
    kind: str,
    payload: dict,
    now: datetime,
) -> None:
    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=actor_id,
            kind=kind,
            payload_json=payload,
            recorded_at=now,
        )
    )


def _active_admin_count(db: Session, pool_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Membership.id))
            .where(Membership.pool_id == pool_id)
            .where(Membership.role == MemberRole.admin)
            .where(Membership.status == MemberStatus.active)
        )
        or 0
    )


def _load_target(db: Session, pool: Pool, member_id: int) -> Membership:
    target = db.get(Membership, member_id)
    if target is None or target.pool_id != pool.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    return target


def _list_context(db: Session, pool: Pool, *, flash: str | None = None) -> dict:
    members = (
        db.scalars(
            select(Membership)
            .where(Membership.pool_id == pool.id)
            .order_by(Membership.role.desc(), Membership.display_name)
        ).all()
    )
    users_by_id = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_({m.user_id for m in members}))
        )
    }
    rows = [
        {
            "membership": m,
            "user": users_by_id.get(m.user_id),
            "email": (
                users_by_id[m.user_id].email
                if (
                    m.user_id in users_by_id
                    and not is_synthetic_email(users_by_id[m.user_id].email)
                )
                else None
            ),
        }
        for m in members
    ]
    return {
        "pool": pool,
        "rows": rows,
        "active_admin_count": _active_admin_count(db, pool.id),
        "roles": [r.value for r in MemberRole],
        "statuses": [s.value for s in MemberStatus],
        "flash": flash,
    }


# ---------------------------------------------------------------------------
# GET /pools/{slug}/members
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def list_members(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "members/list.html", _list_context(db, pool)
    )


# ---------------------------------------------------------------------------
# GET /pools/{slug}/members/invite (form) and POST (create)
# ---------------------------------------------------------------------------
@router.get("/invite", response_class=HTMLResponse)
def invite_form(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "members/invite.html",
        {
            "pool": pool,
            "roles": [r.value for r in MemberRole],
            "errors": [],
            "values": {},
            "login_url": None,
        },
    )


@router.post("/invite", response_class=HTMLResponse)
def invite(
    request: Request,
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("member"),
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    name = (display_name or "").strip()
    e = (email or "").strip().lower()
    errors: list[str] = []
    if not name:
        errors.append("display_name is required")
    if not e or "@" not in e:
        errors.append("a real email address is required")
    try:
        role_enum = MemberRole(role)
    except ValueError:
        errors.append(f"unknown role {role!r}")
        role_enum = MemberRole.member

    templates = request.app.state.templates
    if errors:
        return templates.TemplateResponse(
            request,
            "members/invite.html",
            {
                "pool": pool,
                "roles": [r.value for r in MemberRole],
                "errors": errors,
                "values": {"display_name": name, "email": e, "role": role},
                "login_url": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(timezone.utc)
    user = db.scalars(select(User).where(User.email == e)).one_or_none()
    if user is None:
        user = User(email=e, display_name=name, created_at=now)
        db.add(user)
        db.flush()

    # Refuse duplicate membership in the same pool.
    existing = db.scalars(
        select(Membership)
        .where(Membership.user_id == user.id)
        .where(Membership.pool_id == pool.id)
    ).one_or_none()
    if existing is not None:
        return templates.TemplateResponse(
            request,
            "members/invite.html",
            {
                "pool": pool,
                "roles": [r.value for r in MemberRole],
                "errors": [f"{e} is already a member of this pool"],
                "values": {"display_name": name, "email": e, "role": role},
                "login_url": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    membership = Membership(
        user_id=user.id,
        pool_id=pool.id,
        display_name=name,
        role=role_enum,
        status=MemberStatus.invited,
        joined_at=now,
    )
    db.add(membership)
    db.flush()

    login = LoginToken(
        user_id=user.id,
        token=mint_token(),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_TTL,
    )
    db.add(login)
    db.flush()

    _audit(
        db,
        pool_id=pool.id,
        actor_id=admin.id,
        kind="member.invited",
        payload={
            "target_member_id": membership.id,
            "email": e,
            "role": role_enum.value,
        },
        now=now,
    )
    _audit(
        db,
        pool_id=pool.id,
        actor_id=admin.id,
        kind="auth.magic_link_minted",
        payload={
            "target_member_id": membership.id,
            "login_token_id": login.id,
        },
        now=now,
    )
    db.commit()

    return templates.TemplateResponse(
        request,
        "members/invite.html",
        {
            "pool": pool,
            "roles": [r.value for r in MemberRole],
            "errors": [],
            "values": {},
            "login_url": f"/auth/login/{login.token}",
            "invited_name": name,
        },
    )


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/role — change a member's role
# ---------------------------------------------------------------------------
@router.post("/{member_id}/role", response_class=HTMLResponse)
def change_role(
    member_id: int,
    request: Request,
    role: str = Form(...),
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    target = _load_target(db, pool, member_id)
    try:
        new_role = MemberRole(role)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"unknown role {role!r}"
        ) from exc

    if (
        target.role == MemberRole.admin
        and new_role != MemberRole.admin
        and target.status == MemberStatus.active
        and _active_admin_count(db, pool.id) <= 1
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "can't demote the last active admin",
        )

    old_role = target.role
    if old_role == new_role:
        flash = f"{target.display_name} already has role {new_role.value}"
    else:
        target.role = new_role
        _audit(
            db,
            pool_id=pool.id,
            actor_id=admin.id,
            kind="member.role_changed",
            payload={
                "target_member_id": target.id,
                "old_role": old_role.value,
                "new_role": new_role.value,
            },
            now=datetime.now(timezone.utc),
        )
        db.commit()
        flash = f"{target.display_name}'s role is now {new_role.value}"

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "members/list.html", _list_context(db, pool, flash=flash)
    )


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/status — activate or deactivate
# ---------------------------------------------------------------------------
@router.post("/{member_id}/status", response_class=HTMLResponse)
def change_status(
    member_id: int,
    request: Request,
    new_status: str = Form(..., alias="status"),
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    target = _load_target(db, pool, member_id)
    try:
        next_status = MemberStatus(new_status)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"unknown status {new_status!r}"
        ) from exc

    if (
        target.status == MemberStatus.active
        and next_status != MemberStatus.active
        and target.role == MemberRole.admin
        and _active_admin_count(db, pool.id) <= 1
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "can't deactivate the last active admin",
        )

    old_status = target.status
    if old_status == next_status:
        flash = f"{target.display_name} is already {next_status.value}"
    else:
        target.status = next_status
        _audit(
            db,
            pool_id=pool.id,
            actor_id=admin.id,
            kind=(
                "member.deactivated"
                if next_status == MemberStatus.inactive
                else "member.status_changed"
            ),
            payload={
                "target_member_id": target.id,
                "old_status": old_status.value,
                "new_status": next_status.value,
            },
            now=datetime.now(timezone.utc),
        )
        db.commit()
        flash = f"{target.display_name} is now {next_status.value}"

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "members/list.html", _list_context(db, pool, flash=flash)
    )


# ---------------------------------------------------------------------------
# POST /pools/{slug}/members/{id}/magic-link — re-issue a login URL
# ---------------------------------------------------------------------------
@router.post("/{member_id}/magic-link", response_class=HTMLResponse)
def issue_magic_link(
    member_id: int,
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    admin: Membership = Depends(require_pool_admin),
) -> HTMLResponse:
    target = _load_target(db, pool, member_id)
    if target.status == MemberStatus.inactive:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "deactivated members can't be sent a fresh link; reactivate first",
        )
    now = datetime.now(timezone.utc)
    login = LoginToken(
        user_id=target.user_id,
        token=mint_token(),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_TTL,
    )
    db.add(login)
    db.flush()
    _audit(
        db,
        pool_id=pool.id,
        actor_id=admin.id,
        kind="auth.magic_link_minted",
        payload={
            "target_member_id": target.id,
            "login_token_id": login.id,
        },
        now=now,
    )
    db.commit()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "members/list.html",
        _list_context(
            db,
            pool,
            flash=(
                f"Magic link for {target.display_name}: "
                f"/auth/login/{login.token}"
            ),
        ),
    )

