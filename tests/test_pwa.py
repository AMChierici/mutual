"""PWA-glue tests (M5).

Lighthouse-style installability checks are still manual (you need a
real browser to verify the install prompt). These tests cover the
deterministic bits:

* /manifest.webmanifest exists, parses as JSON, and has the keys
  Chrome requires for an installable PWA.
* /service-worker.js exists, is served with the right Content-Type,
  carries Cache-Control: no-cache so a new SW rolls out cleanly,
  is registered with Service-Worker-Allowed: /,
  and its body does NOT intercept POST requests (audit-log integrity).
* base.html references the manifest and registers the SW.
* The PNG icons referenced by the manifest exist and are valid.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "api" / "web" / "static"


# ---------------------------------------------------------------------------
# /manifest.webmanifest
# ---------------------------------------------------------------------------
async def test_manifest_served_at_root_with_correct_mime(client):
    r = await client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")


async def test_manifest_payload_has_required_fields(client):
    r = await client.get("/manifest.webmanifest")
    data = json.loads(r.text)
    # The fields Chrome requires for the install prompt.
    for key in ("name", "short_name", "start_url", "display", "icons"):
        assert key in data, f"manifest missing {key!r}"
    assert data["display"] in ("standalone", "fullscreen", "minimal-ui")
    # Must include at least a 192px icon and a 512px icon.
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert "192x192" in sizes
    assert "512x512" in sizes
    # Must include a maskable icon.
    assert any(
        "maskable" in icon.get("purpose", "") for icon in data["icons"]
    )


# ---------------------------------------------------------------------------
# /service-worker.js
# ---------------------------------------------------------------------------
async def test_service_worker_served_at_root(client):
    r = await client.get("/service-worker.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")


async def test_service_worker_no_cache_header(client):
    """Stale SW = stale app shell. The browser must always re-check this
    file so a new deploy reaches every device."""
    r = await client.get("/service-worker.js")
    assert "no-cache" in r.headers["cache-control"].lower()


async def test_service_worker_allows_root_scope(client):
    r = await client.get("/service-worker.js")
    # Allows registration with scope='/' even though the file lives at /.
    # Harmless when the path matches, essential if you ever move the SW.
    assert r.headers.get("service-worker-allowed") == "/"


async def test_service_worker_does_not_intercept_writes(client):
    """The SW must NEVER cache or rewrite POST/PUT/DELETE.

    Audit log integrity requires those to hit the server live. We
    enforce this by reading the SW source and asserting the fetch
    handler short-circuits on non-GET, which mirrors what production
    behaviour we want."""
    body = (STATIC / "service-worker.js").read_text()
    # Look for the explicit guard.
    assert "req.method !== 'GET'" in body or "req.method != 'GET'" in body


# ---------------------------------------------------------------------------
# base.html wiring
# ---------------------------------------------------------------------------
async def test_base_template_links_manifest(client, pool):
    """Any HTML page should expose the manifest and register the SW.
    /login renders the paste-the-link form once a pool exists."""
    r = await client.get("/login")
    assert r.status_code == 200
    body = r.text
    assert 'rel="manifest"' in body
    assert "/manifest.webmanifest" in body
    assert "/service-worker.js" in body
    assert 'name="theme-color"' in body


# ---------------------------------------------------------------------------
# Icon files exist and are valid PNGs
# ---------------------------------------------------------------------------
def test_icon_files_exist_and_are_pngs():
    for name in (
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
    ):
        path = STATIC / "icons" / name
        assert path.is_file(), f"missing {path}"
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"
        # IHDR holds width/height starting at byte 16.
        import struct
        width, height = struct.unpack(">II", data[16:24])
        assert width == height
        if "192" in name:
            assert width == 192
        else:
            assert width == 512
