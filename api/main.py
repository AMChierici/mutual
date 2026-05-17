"""Mutual API entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, select

from api.auth import SESSION_COOKIE, resolve_session
from api.db import make_engine, make_session_factory
from api.orm import Membership, MemberStatus, Pool
from api.routes_account import router as account_router
from api.routes_audit import router as audit_router
from api.routes_auth import router as auth_router
from api.routes_claims import router as claims_router
from api.routes_contributions import router as contributions_router
from api.routes_dashboard import router as dashboard_router
from api.routes_login import router as login_router
from api.routes_settings import router as settings_router
from api.routes_setup import router as setup_router

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"


def run_migrations(engine: Engine) -> None:
    """Bring the database up to ``alembic head``.

    Idempotent — a no-op if the DB is already current. Runs on every app
    startup so self-hosters don't have to remember a separate
    ``alembic upgrade`` step after pulling a new build.
    """
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    run_migrations(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(
    title="Mutual",
    description="Self-hosted infrastructure for mutual aid pools.",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
app.include_router(auth_router)
app.include_router(login_router)
app.include_router(setup_router)
app.include_router(account_router)
app.include_router(contributions_router)
app.include_router(claims_router)
app.include_router(dashboard_router)
app.include_router(audit_router)
app.include_router(settings_router)


# ---------------------------------------------------------------------------
# Legacy single-pool path redirects.
# M2 moved every pool-scoped route under /pools/{slug}/... — old bookmarks
# from a v0 install should still work. The redirect resolves the current
# user's first active membership and rewrites the URL into the new shape.
# ---------------------------------------------------------------------------
LEGACY_PATH_REWRITE = {
    "/claims": "claims",
    "/contributions": "contributions",
    "/settings": "settings",
    "/audit": "audit",
    "/models": "models",
}


@app.middleware("http")
async def legacy_pool_path_redirect(request: Request, call_next):
    path = request.url.path
    # Match exact path or one that starts with a legacy prefix + "/".
    rewrite_to = None
    for legacy_prefix, new_suffix in LEGACY_PATH_REWRITE.items():
        if path == legacy_prefix or path.startswith(legacy_prefix + "/"):
            tail = path[len(legacy_prefix):]
            rewrite_to = f"{new_suffix}{tail}"
            break
    if rewrite_to is None:
        return await call_next(request)

    # Only redirect for logged-in users who have at least one active
    # membership. Anonymous / no-pool requests fall through to the normal
    # router which will return 404 or redirect to /login.
    factory = request.app.state.session_factory
    target_slug: str | None = None
    with factory() as db:
        cookie = request.cookies.get(SESSION_COOKIE)
        sess = resolve_session(db, cookie)
        if sess is not None:
            membership = db.scalars(
                select(Membership)
                .where(Membership.user_id == sess.user_id)
                .where(Membership.status == MemberStatus.active)
                .order_by(Membership.id)
            ).first()
            if membership is not None:
                pool = db.get(Pool, membership.pool_id)
                if pool is not None:
                    target_slug = pool.slug
    if target_slug is None:
        return await call_next(request)

    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        f"/pools/{target_slug}/{rewrite_to}{query}", status_code=303
    )


@app.exception_handler(HTTPException)
async def html_aware_http_exception_handler(request: Request, exc: HTTPException):
    """For 401s that came from a browser (Accept includes text/html), redirect
    the visitor to /login instead of dumping JSON. Keeps the API contract for
    JSON / curl clients (Accept: */* or application/json) untouched.
    """
    if exc.status_code == 401:
        accept = request.headers.get("accept", "").lower()
        if "text/html" in accept:
            return RedirectResponse("/login", status_code=303)
    return await http_exception_handler(request, exc)


@app.get("/health")
def health():
    return {"ok": True}
