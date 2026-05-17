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

from api.auth import SESSION_COOKIE, consume_login_token, create_login_token
from api.db import Base, make_session_factory
from api.orm import Membership, MemberRole, MemberStatus, Pool, User

# Test-suite alias: many test files still spell this ``Member``. After M1
# the class is ``Membership``; re-export under the old name so test
# imports continue to work without churning every file.
Member = Membership


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
    p = Pool(
        slug="test-pool",
        name="Test Pool",
        currency="USD",
        governance_config={
            "tiers": [
                {"max_amount_cents": 10_000, "scheme": "auto_approve"},
                {"max_amount_cents": 100_000, "scheme": "majority"},
                {"max_amount_cents": None, "scheme": "unanimous"},
            ]
        },
    )
    session.add(p)
    session.commit()
    return p


def _make_member(
    session: Session,
    pool: Pool,
    *,
    display_name: str,
    role: MemberRole,
    status: MemberStatus,
    email: str | None = None,
) -> Membership:
    user = User(
        email=email or f"{display_name.lower()}-{pool.id}@example.test",
        display_name=display_name,
    )
    session.add(user)
    session.flush()
    m = Membership(
        user_id=user.id,
        pool_id=pool.id,
        display_name=display_name,
        role=role,
        status=status,
    )
    session.add(m)
    session.commit()
    return m


@pytest.fixture
def admin(session, pool) -> Membership:
    return _make_member(
        session,
        pool,
        display_name="Admin",
        role=MemberRole.admin,
        status=MemberStatus.active,
    )


@pytest.fixture
def member(session, pool) -> Membership:
    return _make_member(
        session,
        pool,
        display_name="Bo",
        role=MemberRole.member,
        status=MemberStatus.invited,
    )


@pytest_asyncio.fixture
async def admin_client(client, session, admin) -> AsyncClient:
    """An HTTP client carrying a valid admin session cookie."""
    tok = create_login_token(session, admin.user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


@pytest.fixture
def members(session, pool) -> list[Membership]:
    """Three active members for tests that need multiple participants."""
    return [
        _make_member(
            session,
            pool,
            display_name=name,
            role=MemberRole.member,
            status=MemberStatus.active,
        )
        for name in ("Bo", "Cy", "Di")
    ]
