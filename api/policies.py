"""Read-only access to the policy templates shipped under ``policies/``.

Templates are markdown files; the wizard offers them as starting points.
The admin can paste/edit before saving — the edited text lives on
``Pool.policy_text``, never written back to the source template.
"""
from __future__ import annotations

from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"


def list_policy_templates() -> list[dict[str, str]]:
    if not POLICIES_DIR.exists():
        return []
    out: list[dict[str, str]] = []
    for child in sorted(POLICIES_DIR.iterdir()):
        readme = child / "README.md"
        if child.is_dir() and readme.is_file():
            out.append({
                "id": child.name,
                "title": child.name.replace("-", " ").title(),
            })
    return out


def read_policy_template(template_id: str) -> str:
    """Return the markdown body of a template, or raise FileNotFoundError."""
    if "/" in template_id or template_id in ("", ".", ".."):
        raise FileNotFoundError(template_id)
    path = POLICIES_DIR / template_id / "README.md"
    if not path.is_file():
        raise FileNotFoundError(template_id)
    return path.read_text()
