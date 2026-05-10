"""Read-only audit-log viewer."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.audit import DEFAULT_LIMIT, list_audit_events
from api.auth import current_member
from api.deps import get_db
from api.orm import Member, Pool

router = APIRouter(tags=["audit"])


@router.get("/audit", response_class=HTMLResponse)
def audit_view(
    request: Request,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
):
    pool = db.scalars(select(Pool)).first()
    if pool is None:
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)

    if member.pool_id != pool.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not in this pool")

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
