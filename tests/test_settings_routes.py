"""HTTP-level tests for the admin settings page and the monthly-close trigger."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

import api.webhooks as webhooks_module
from api.auth import SESSION_COOKIE, consume_login_token, create_login_token


@pytest.fixture
def sink(monkeypatch):
    calls: list[dict] = []

    def fake(url, body, timeout=5.0):
        calls.append({"url": url, "body": body})
        return 200, None

    monkeypatch.setattr(webhooks_module, "_post_webhook", fake)
    return calls


@pytest_asyncio.fixture
async def member_client(client, session, members) -> AsyncClient:
    tok = create_login_token(session, members[0].user_id)
    auth_session = consume_login_token(session, tok.token)
    client.cookies.set(SESSION_COOKIE, auth_session.token)
    return client


# ---------------------------------------------------------------------------
# GET /pools/{slug}/settings
# ---------------------------------------------------------------------------
async def test_get_settings_unauthenticated_is_401(client, pool):
    r = await client.get(f"/pools/{pool.slug}/settings")
    assert r.status_code == 401


async def test_get_settings_non_admin_is_403(member_client, pool):
    r = await member_client.get(f"/pools/{pool.slug}/settings")
    assert r.status_code == 403


async def test_get_settings_admin_renders_form(admin_client, pool):
    r = await admin_client.get(f"/pools/{pool.slug}/settings")
    assert r.status_code == 200
    assert 'name="webhook_url"' in r.text
    assert f"/pools/{pool.slug}/settings/webhook" in r.text


async def test_get_settings_shows_existing_url(admin_client, session, pool):
    from api.webhooks import set_webhook_url
    set_webhook_url(session, pool.id, "https://example.com/hook")
    r = await admin_client.get(f"/pools/{pool.slug}/settings")
    assert r.status_code == 200
    assert "https://example.com/hook" in r.text


# ---------------------------------------------------------------------------
# POST /pools/{slug}/settings/webhook
# ---------------------------------------------------------------------------
async def test_post_webhook_admin_persists_url(admin_client, session, pool):
    r = await admin_client.post(
        f"/pools/{pool.slug}/settings/webhook",
        data={"webhook_url": "https://example.com/hook"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    from api.webhooks import get_webhook_url
    assert get_webhook_url(session, pool.id) == "https://example.com/hook"


async def test_post_webhook_admin_can_clear_url(admin_client, session, pool):
    from api.webhooks import set_webhook_url
    set_webhook_url(session, pool.id, "https://example.com/hook")
    r = await admin_client.post(
        f"/pools/{pool.slug}/settings/webhook",
        data={"webhook_url": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    session.expire_all()
    from api.webhooks import get_webhook_url
    assert get_webhook_url(session, pool.id) is None


async def test_post_webhook_rejects_non_http_url(admin_client, pool):
    r = await admin_client.post(
        f"/pools/{pool.slug}/settings/webhook",
        data={"webhook_url": "javascript:alert(1)"},
    )
    assert r.status_code == 400


async def test_post_webhook_non_admin_is_403(member_client, pool):
    r = await member_client.post(
        f"/pools/{pool.slug}/settings/webhook",
        data={"webhook_url": "https://example.com/hook"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /pools/{slug}/webhooks/monthly-close
# ---------------------------------------------------------------------------
async def test_monthly_close_admin_fires_event(
    admin_client, session, pool, sink
):
    from api.webhooks import set_webhook_url
    set_webhook_url(session, pool.id, "https://example.com/hook")
    r = await admin_client.post(
        f"/pools/{pool.slug}/webhooks/monthly-close", follow_redirects=False
    )
    assert r.status_code in (200, 303)
    assert len(sink) == 1
    import json as _json
    body = _json.loads(sink[0]["body"])
    assert body["event"] == "monthly_close.due"
    assert body["payload"]["period"]  # YYYY-MM


async def test_monthly_close_non_admin_is_403(member_client, pool):
    r = await member_client.post(f"/pools/{pool.slug}/webhooks/monthly-close")
    assert r.status_code == 403


async def test_monthly_close_unauthenticated_is_401(client, pool):
    r = await client.post(f"/pools/{pool.slug}/webhooks/monthly-close")
    assert r.status_code == 401


async def test_monthly_close_no_url_configured_is_noop(admin_client, pool, sink):
    """No webhook configured → no call, no error."""
    r = await admin_client.post(
        f"/pools/{pool.slug}/webhooks/monthly-close", follow_redirects=False
    )
    assert r.status_code in (200, 303)
    assert sink == []
