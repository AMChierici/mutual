"""Outbound webhook dispatch — fire-and-log, no retries.

Admin sets one URL per pool (``Pool.webhook_url``). On each lifecycle event
listed in step 10 we POST a small JSON envelope to that URL and write one
``webhook.dispatched`` audit event with the outcome. Network errors,
non-2xx responses, and unexpected transport exceptions all become audit
rows; nothing here ever bubbles up to the calling request.

Transport: stdlib ``urllib`` so we don't pull in a runtime HTTP-client
dependency. Default timeout 5 seconds.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from api.orm import AuditEvent, Pool

DEFAULT_TIMEOUT_SECONDS = 5.0
USER_AGENT = "mutual/0.1"


class InvalidWebhookURL(ValueError):
    """The URL admin tried to set isn't a valid http(s) URL."""


# ---------------------------------------------------------------------------
# URL persistence
# ---------------------------------------------------------------------------
def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidWebhookURL(f"only http(s) URLs are allowed, got {parsed.scheme!r}")
    if not parsed.netloc:
        raise InvalidWebhookURL("URL has no host")


def set_webhook_url(db: Session, pool_id: int, url: str | None) -> None:
    """Persist the pool's webhook URL. ``""`` or ``None`` clears it.

    Raises :class:`InvalidWebhookURL` for non-http(s) URLs.
    """
    pool = db.get(Pool, pool_id)
    if pool is None:
        raise ValueError("pool not found")
    if url:
        _validate_url(url)
        pool.webhook_url = url
    else:
        pool.webhook_url = None
    db.commit()


def get_webhook_url(db: Session, pool_id: int) -> str | None:
    pool = db.get(Pool, pool_id)
    return pool.webhook_url if pool else None


# ---------------------------------------------------------------------------
# HTTP transport (mockable in tests)
# ---------------------------------------------------------------------------
def _post_webhook(
    url: str, body: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> tuple[int | None, str | None]:
    """POST ``body`` (JSON) to ``url``. Returns ``(status_code, error_message)``.

    On success, returns ``(2xx_code, None)``. On any failure path — connect
    error, timeout, non-2xx, or anything else — returns ``(None_or_code,
    error_string)``. Never raises.
    """
    try:
        req = Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — admin-controlled
            return int(resp.status), None
    except URLError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — fire-and-log
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def dispatch_event(
    db: Session, pool_id: int, event: str, payload: dict
) -> None:
    """Send the event to the pool's webhook URL, or no-op if none is set.

    Always audit-logs the outcome (one ``webhook.dispatched`` event per call,
    once a URL is configured). Never raises.
    """
    pool = db.get(Pool, pool_id)
    if pool is None or not pool.webhook_url:
        return

    now = datetime.now(timezone.utc)
    envelope = {
        "event": event,
        "occurred_at": now.isoformat(),
        "pool": {
            "id": pool.id,
            "name": pool.name,
            "currency": pool.currency,
        },
        "payload": payload,
    }
    body = json.dumps(envelope, sort_keys=True)

    try:
        status_code, error = _post_webhook(pool.webhook_url, body)
    except Exception as exc:  # noqa: BLE001 — defensive belt-and-suspenders
        status_code, error = None, f"{type(exc).__name__}: {exc}"

    ok = status_code is not None and 200 <= status_code < 300
    audit_payload: dict = {
        "event": event,
        "url": pool.webhook_url,
        "status_code": status_code,
        "ok": ok,
    }
    if error:
        audit_payload["error"] = error

    db.add(
        AuditEvent(
            pool_id=pool_id,
            actor_member_id=None,
            kind="webhook.dispatched",
            payload_json=audit_payload,
            recorded_at=now,
        )
    )
    db.commit()
