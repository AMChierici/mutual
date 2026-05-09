"""Voting schemes — pluggable per-tier (see ``Pool.governance_config``).

Every scheme exports a single ``tally`` callable with the signature::

    tally(*, approve: int, reject: int, abstain: int, eligible: int) -> TallyOutcome

It looks at the running vote totals plus the eligible voter count and answers
one of three things: ``approved`` / ``rejected`` / ``pending``. Schemes are
pure functions; they do not touch the database.

Schemes are registered by string name in ``_REGISTRY`` so the wizard's
``governance_config`` can refer to them by name.
"""
from __future__ import annotations

from typing import Callable

from governance import auto_approve as _auto_approve
from governance import majority as _majority
from governance import unanimous as _unanimous
from governance._outcome import TallyOutcome

__all__ = ["TallyOutcome", "get_scheme", "list_schemes"]


Tally = Callable[..., TallyOutcome]


_REGISTRY: dict[str, Tally] = {
    "unanimous": _unanimous.tally,
    "majority": _majority.tally,
    "auto_approve": _auto_approve.tally,
}


def get_scheme(name: str) -> Tally:
    if name not in _REGISTRY:
        raise KeyError(f"unknown governance scheme: {name!r}")
    return _REGISTRY[name]


def list_schemes() -> list[str]:
    return list(_REGISTRY.keys())
