"""Mutual API entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from api.db import make_engine, make_session_factory
from api.routes_audit import router as audit_router
from api.routes_auth import router as auth_router
from api.routes_claims import router as claims_router
from api.routes_contributions import router as contributions_router
from api.routes_dashboard import router as dashboard_router
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
app.include_router(setup_router)
app.include_router(contributions_router)
app.include_router(claims_router)
app.include_router(dashboard_router)
app.include_router(audit_router)
app.include_router(settings_router)


@app.get("/health")
def health():
    return {"ok": True}
