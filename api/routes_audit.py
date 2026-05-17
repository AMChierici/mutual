"""Read-only audit-log viewer (pool-scoped)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from api.audit import DEFAULT_LIMIT, list_audit_events
from api.auth import current_membership_for_pool
from api.deps import get_db, get_pool_from_slug
from api.orm import Member, Membership, Pool

router = APIRouter(prefix="/pools/{pool_slug}", tags=["audit"])


@router.get("/audit", response_class=HTMLResponse)
def audit_view(
    request: Request,
    pool: Pool = Depends(get_pool_from_slug),
    db: Session = Depends(get_db),
    member: Membership = Depends(current_membership_for_pool),
):
    events = list_audit_events(db, pool.id)
    members_by_id = {m.id: m for m in db.query(Member).filter_by(pool_id=pool.id).all()}

    rendered = [
        {
            "recorded_at": e.recorded_at,
            "actor_name": (
                members_by_id[e.actor_member_id].display_name
                if e.actor_member_id and e.actor_member_id in members_by_id
                else "system"
            ),
            "kind": e.kind,
            "payload": json.dumps(e.payload_json, sort_keys=True),
        }
        for e in events
    ]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "audit/list.html",
        {
            "pool": pool,
            "events": rendered,
            "limit": DEFAULT_LIMIT,
            "showing": len(rendered),
            "summary": {
                "pool_name": pool.name,
                "currency": pool.currency,
                "member_count": db.query(Member)
                    .filter_by(pool_id=pool.id)
                    .count(),
                "balance_cents": 0,  # not used by audit page
                "pending_claims_count": 0,
            },
            "active_tab": "audit",
            "is_admin": member.role.value == "admin",
        },
    )
