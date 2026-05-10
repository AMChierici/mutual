"""Audit log query helpers.

Audit events themselves are written by the service layers (setup,
contributions, claims, voting, payouts, auth). This module just reads them
back out for the read-only viewer at /audit.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.orm import AuditEvent

DEFAULT_LIMIT = 200


def list_audit_events(
    db: Session, pool_id: int, *, limit: int = DEFAULT_LIMIT
) -> list[AuditEvent]:
    """Newest first, capped at ``limit``."""
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.pool_id == pool_id)
            .order_by(AuditEvent.recorded_at.desc(), AuditEvent.id.desc())
            .limit(limit)
        ).all()
    )
