"""FastAPI dependencies shared across routers."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the app's session factory.

    The session factory is configured at app startup (see ``api.main.lifespan``)
    or, in tests, by overriding ``app.state.session_factory`` directly.
    """
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def get_pool_from_slug(
    pool_slug: str = Path(...),
    db: Session = Depends(get_db),
):
    """Resolve a URL ``{pool_slug}`` to a :class:`Pool` or 404.

    Used by every pool-scoped route mounted under ``/pools/{pool_slug}``.
    Authorisation (does *this user* belong to the pool?) is layered on top
    via :func:`api.auth.current_membership_for_pool`.
    """
    # Import inside to avoid a circular import (orm pulls in db pulls in deps).
    from api.orm import Pool

    pool = db.scalars(select(Pool).where(Pool.slug == pool_slug)).one_or_none()
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pool not found")
    return pool

