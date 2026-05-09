"""Majority: more than half of *eligible* voters must approve (or reject).

Uses ``2 * approve > eligible`` instead of float division to keep the math
exact across pool sizes. Slow voters effectively count as 'pending', which
prevents premature decisions on low turnout.
"""
from __future__ import annotations

from governance._outcome import TallyOutcome


def tally(*, approve: int, reject: int, abstain: int, eligible: int) -> TallyOutcome:
    if 2 * approve > eligible:
        return TallyOutcome.approved
    if 2 * reject > eligible:
        return TallyOutcome.rejected
    return TallyOutcome.pending
