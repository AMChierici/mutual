"""Tests for the root + health endpoints (also exercises app wiring)."""
from __future__ import annotations


async def test_health_returns_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_root_returns_metadata(client):
    r = await client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Mutual"
    assert body["version"] == "0.1.0"
