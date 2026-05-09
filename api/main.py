"""Mutual API entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.db import make_engine, make_session_factory
from api.routes_auth import router as auth_router
from api.routes_setup import router as setup_router

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
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


@app.get("/")
def root():
    return {
        "name": "Mutual",
        "version": "0.1.0",
        "status": "pre-alpha",
        "docs": "/docs",
        "manifesto": "https://github.com/YOU/mutual/blob/main/MANIFESTO.md",
    }


@app.get("/health")
def health():
    return {"ok": True}
