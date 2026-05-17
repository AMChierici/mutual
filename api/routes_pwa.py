"""PWA glue: manifest + service worker served from the app root.

The web manifest *could* be served from /static, but the service worker
must live at the app root for its scope to cover every URL — and serving
both from the same place keeps the references in ``base.html`` symmetric.

The SW route sets ``Cache-Control: no-cache`` because the browser's own
service-worker registration cache will otherwise keep stale copies and
make rolling out a new SW painful for self-hosters. The static asset
the SW caches (CSS, manifest, icons) are versioned inside the SW via
``CACHE_VERSION`` — bump it to force a refresh.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

router = APIRouter(include_in_schema=False)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@router.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@router.get("/service-worker.js")
def service_worker() -> Response:
    body = (STATIC_DIR / "service-worker.js").read_bytes()
    return Response(
        content=body,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )
