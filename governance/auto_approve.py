"""Auto-approve: claims routed to this tier never reach voting (step 5
sets the claim straight to ``approved``). If someone calls ``tally``
anyway — defensive — the answer is approved.
"""
from __future__ import annotations

from governance._outcome import TallyOutcome


def tally(*, approve: int, reject: int, abstain: int, eligible: int) -> TallyOutcome:
    return TallyOutcome.approved
