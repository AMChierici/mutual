"""Unanimous: every eligible member must approve. One reject kills it."""
from __future__ import annotations

from governance._outcome import TallyOutcome


def tally(*, approve: int, reject: int, abstain: int, eligible: int) -> TallyOutcome:
    if reject > 0:
        return TallyOutcome.rejected
    if approve == eligible:
        return TallyOutcome.approved
    return TallyOutcome.pending
