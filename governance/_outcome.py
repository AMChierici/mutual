"""Internal: shared TallyOutcome enum.

Lives in its own module so individual scheme modules can import it without
creating a circular import with ``governance/__init__.py`` (which itself
imports the scheme modules to build the registry).
"""
from __future__ import annotations

import enum


class TallyOutcome(enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
