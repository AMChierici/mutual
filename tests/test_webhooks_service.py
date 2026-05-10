"""Service-layer tests for webhook dispatch."""
from __future__ import annotations

import json

import pytest

import api.webhooks as webhooks_module
from api.orm import AuditEvent
from api.webhooks import (
    InvalidWebhookURL,
    dispatch_event,
    get_webhook_url,
    set_webhook_url,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def sink(monkeypatch):
    """Capture every call to ``_post_webhook`` instead of hitting the network."""
    calls: list[dict] = []

    def fake(url: str, body: str, timeout: float = 5.0):
        calls.append({"url": url, "body": body, "timeout": timeout})
        return 200, None  # (status_code, error_message)

    monkeypatch.setattr(webhooks_module, "_post_webhook", fake)
    return calls


@pytest.fixture
def failing_sink(monkeypatch):
    def fake(url, body, timeout=5.0):
        return None, "connection refused"

    monkeypatch.setattr(webhooks_module, "_post_webhook", fake)


# ---------------------------------------------------------------------------
# set_webhook_url / get_webhook_url
# ---------------------------------------------------------------------------
def test_get_webhook_url_returns_none_when_unset(session, pool):
    assert get_webhook_url(session, pool.id) is None


def test_set_webhook_url_persists_https(session, pool):
    set_webhook_url(session, pool.id, "https://example.com/hook")
    assert get_webhook_url(session, pool.id) == "https://example.com/hook"


def test_set_webhook_url_accepts_http(session, pool):
    set_webhook_url(session, pool.id, "http://localhost:9000/hook")
    assert get_webhook_url(session, pool.id) == "http://localhost:9000/hook"


def test_set_webhook_url_clearing_with_empty_string(session, pool):
    set_webhook_url(session, pool.id, "https://example.com/hook")
    set_webhook_url(session, pool.id, "")
    assert get_webhook_url(session, pool.id) is None


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "file:///etc/passwd", "ftp://x", "not a url"],
)
def test_set_webhook_url_rejects_non_http(session, pool, bad):
    with pytest.raises(InvalidWebhookURL):
        set_webhook_url(session, pool.id, bad)


# ---------------------------------------------------------------------------
# dispatch_event
# ---------------------------------------------------------------------------
def test_dispatch_event_no_url_is_noop_no_audit(session, pool):
    dispatch_event(session, pool.id, "claim.submitted", {"claim_id": 1})
    assert session.query(AuditEvent).filter_by(kind="webhook.dispatched").count() == 0


def test_dispatch_event_posts_envelope_and_audits_success(session, pool, sink):
    set_webhook_url(session, pool.id, "https://example.com/hook")
    dispatch_event(session, pool.id, "claim.submitted", {"claim_id": 42, "amount_cents": 1000})

    assert len(sink) == 1
    body = json.loads(sink[0]["body"])
    assert body["event"] == "claim.submitted"
    assert body["pool"]["id"] == pool.id
    assert body["pool"]["name"] == pool.name
    assert body["pool"]["currency"] == pool.currency
    assert body["payload"]["claim_id"] == 42
    assert "occurred_at" in body

    audit = session.query(AuditEvent).filter_by(kind="webhook.dispatched").one()
    assert audit.payload_json["event"] == "claim.submitted"
    assert audit.payload_json["status_code"] == 200
    assert audit.payload_json["ok"] is True


def test_dispatch_event_audits_failure_without_raising(session, pool, failing_sink):
    set_webhook_url(session, pool.id, "https://example.com/hook")
    # Must not raise even though the post "fails".
    dispatch_event(session, pool.id, "claim.submitted", {"claim_id": 1})

    audit = session.query(AuditEvent).filter_by(kind="webhook.dispatched").one()
    assert audit.payload_json["ok"] is False
    assert audit.payload_json["status_code"] is None
    assert "connection refused" in audit.payload_json["error"]


def test_dispatch_event_swallows_unexpected_exceptions(session, pool, monkeypatch):
    """Even if the transport throws, dispatch must not bubble."""
    def boom(url, body, timeout=5.0):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(webhooks_module, "_post_webhook", boom)
    set_webhook_url(session, pool.id, "https://example.com/hook")
    dispatch_event(session, pool.id, "claim.submitted", {"claim_id": 1})  # must not raise

    audit = session.query(AuditEvent).filter_by(kind="webhook.dispatched").one()
    assert audit.payload_json["ok"] is False
    assert "kaboom" in audit.payload_json["error"]


def test_dispatch_event_unknown_pool_is_noop(session, sink):
    dispatch_event(session, 99999, "claim.submitted", {"claim_id": 1})
    assert sink == []


def test_dispatch_event_includes_5s_default_timeout(session, pool, sink):
    set_webhook_url(session, pool.id, "https://example.com/hook")
    dispatch_event(session, pool.id, "claim.submitted", {})
    assert sink[0]["timeout"] == 5.0
