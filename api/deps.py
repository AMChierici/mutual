"""FastAPI dependencies shared across routers."""
from __future__ import annotations

from typing import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the app's session factory.

    The session factory is configured at app startup (see ``api.main.lifespan``)
    or, in tests, by overriding ``app.state.session_factory`` directly.
    """
    factory = request.app.state.session_factory
    with factory() as session:
        yield session
