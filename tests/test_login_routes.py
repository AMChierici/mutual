"""HTTP-level tests for the public /login landing page and the
"unauthenticated browser → redirect" 401 handler.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# GET /login (no auth required)
# ---------------------------------------------------------------------------
async def test_get_login_no_session_renders_form(client, pool):
    r = await client.get("/login")
    assert r.status_code == 200
    body = r.text
    assert 'name="link"' in body
    assert "magic" in body.lower()
    # Form posts back to /login
    assert 'action="/login"' in body


async def test_get_login_no_pool_redirects_to_setup(client):
    r = await client.get("/login", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/setup"


# ---------------------------------------------------------------------------
# POST /login → token extraction → 303 to /auth/login/{token}
# ---------------------------------------------------------------------------
async def test_post_login_with_full_url_redirects(client, pool):
    r = await client.post(
        "/login",
        data={"link": "http://localhost:8000/auth/login/abc123_TOKEN"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login/abc123_TOKEN"


async def test_post_login_with_https_url_redirects(client, pool):
    r = await client.post(
        "/login",
        data={"link": "https://mutual.example.com/auth/login/xYz-9_8"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login/xYz-9_8"


async def test_post_login_with_url_with_trailing_slash(client, pool):
    r = await client.post(
        "/login",
        data={"link": "http://localhost:8000/auth/login/abc/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login/abc"


async def test_post_login_with_bare_token_redirects(client, pool):
    r = await client.post(
        "/login",
        data={"link": "abc123_TOKEN"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login/abc123_TOKEN"


async def test_post_login_with_whitespace_is_trimmed(client, pool):
    r = await client.post(
        "/login",
        data={"link": "  abc123  "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login/abc123"


async def test_post_login_with_empty_input_re_renders_with_error(client, pool):
    r = await client.post("/login", data={"link": ""})
    assert r.status_code == 400
    # Form re-rendered with an error
    assert 'name="link"' in r.text
    assert "doesn't look like" in r.text.lower() or "magic link" in r.text.lower()


async def test_post_login_with_garbage_re_renders_with_error(client, pool):
    r = await client.post("/login", data={"link": "not a token; bad chars!"})
    assert r.status_code == 400


async def test_post_login_with_non_login_url_rejected(client, pool):
    """A URL that isn't a magic link shouldn't be coerced into a token."""
    r = await client.post(
        "/login",
        data={"link": "http://example.com/random/path"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 401 redirect for HTML-accepting unauthenticated visitors
# ---------------------------------------------------------------------------
async def test_dashboard_unauth_html_redirects_to_login(client, pool):
    r = await client.get(
        "/",
        headers={"accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_dashboard_unauth_non_html_still_returns_json_401(client, pool):
    """API clients (Accept: */* or JSON) keep getting JSON 401 — the
    existing API contract from steps 2-9 is unchanged."""
    r = await client.get("/", headers={"accept": "*/*"}, follow_redirects=False)
    assert r.status_code == 401


async def test_audit_unauth_html_redirects_to_login(client, pool):
    r = await client.get(
        "/audit",
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_claims_pending_unauth_html_redirects_to_login(client, pool):
    r = await client.get(
        "/claims/pending",
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
