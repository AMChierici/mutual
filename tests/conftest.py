"""Shared pytest fixtures.

The DB fixture uses an in-memory SQLite with ``StaticPool`` so the same
connection is shared across the test client and the app under test —
otherwise each new connection would see an empty schema.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.auth import SESSION_COOKIE, create_login_token, consume_login_token
from api.db import Base, make_session_factory
from api.orm import Member, MemberRole, MemberStatus, Pool


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_conn, _conn_rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    factory = make_session_factory(engine)
    with factory() as s:
        yield s


@pytest.fixture
def app_with_db(engine):
    """Bind the FastAPI app to the test engine."""
    from api.main import app

    factory = make_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    return app


@pytest_asyncio.fixture
async def client(app_with_db) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Domain seeders — convenient for HTTP tests
# ---------------------------------------------------------------------------
@pytest.fixture
def pool(session) -> Pool:
    p = Pool(name="Test Pool", currency="USD", governance_config={})
    session.add(p)
    session.commit()
    return p


@pytest.fixture
def admin(session, pool) -> Member:
    m = Member(
        pool_id=pool.id,
        display_name="Admin",
        role=MemberRole.admin,
        status=MemberStatus.active,
    )
    session.add(m)
    session.commit()
    return m


@pytest.fixture
def member(session, pool) -> Member:
    m = Member(
        pool_id=pool.id,
        display_name="Bo",
        role=MemberRole.member,
        status=MemberStatus.invited,
    )
    session.add(m)
    session.commit()
    return m


@pytest_asyncio.fixture
async def admin_client(client, session, admin) -> AsyncClient:
    """An HTTP client carrying a valid admin session cookie."""
    tok = create_login_token(session, admin.id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client
