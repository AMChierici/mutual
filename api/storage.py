"""Local-disk storage for claim evidence uploads.

Path is read from ``MUTUAL_UPLOADS_DIR``; if unset, defaults to
``<repo>/data/uploads``. Files are namespaced under
``<uploads>/claims/<claim_id>/`` so a single ``rm -rf`` per claim is enough
when admins want to clean up.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

UPLOADS_SUBDIR_CLAIMS = "claims"


def _default_uploads_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "uploads"


def get_uploads_dir() -> Path:
    env = os.environ.get("MUTUAL_UPLOADS_DIR")
    return Path(env) if env else _default_uploads_dir()


def claim_evidence_dir(claim_id: int) -> Path:
    return get_uploads_dir() / UPLOADS_SUBDIR_CLAIMS / str(claim_id)


def safe_filename(name: str) -> str:
    """Return a filesystem-safe form of ``name``: no path components, no
    weird characters, capped length, never empty.
    """
    name = Path(name).name  # strip leading directories
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]
    return sanitized or "file"
