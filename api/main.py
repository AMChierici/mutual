"""Mutual API entry point.

This is a stub. The full app — pools, members, contributions, claims, votes,
payouts — is left for the first wave of contributors. See `docs/architecture.md`
for the data model and `CONTRIBUTING.md` for how to pick up an issue.
"""
from fastapi import FastAPI

app = FastAPI(
    title="Mutual",
    description="Self-hosted infrastructure for mutual aid pools.",
    version="0.1.0",
)


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
