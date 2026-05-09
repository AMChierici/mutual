"""Flat pricing: every member pays the same.

Useful as a baseline and for pools that explicitly want equal contribution
regardless of exposure (many family pools, many mutual aid groups).
"""
from __future__ import annotations

from typing import Sequence

from models.base import Claim, Member, PricingResult


class FlatPricing:
    name = "flat"
    version = "0.1.0"

    def __init__(self, safety_loading: float = 1.2):
        """safety_loading: multiplier on expected claims to build surplus.
        1.0 = break-even in expectation (will go bust ~50% of the time).
        1.2 = 20% loading, common starting point for small pools.
        """
        if safety_loading < 1.0:
            raise ValueError("safety_loading must be >= 1.0")
        self.safety_loading = safety_loading

    def price(
        self,
        members: Sequence[Member],
        history: Sequence[Claim],
        target_payout_capacity: float,
    ) -> PricingResult:
        if not members:
            raise ValueError("Cannot price an empty pool")

        # Expected monthly claims from history; if no history, fall back to target.
        if history:
            months = max(1, _months_spanned(history))
            expected_monthly = sum(c.paid for c in history) / months
        else:
            expected_monthly = target_payout_capacity / 12

        loaded = expected_monthly * self.safety_loading
        per_member = loaded / len(members)

        return PricingResult(
            premiums={m.id: round(per_member, 2) for m in members},
            period="monthly",
            rationale=(
                f"Flat pricing: expected monthly claims ~{expected_monthly:.2f}, "
                f"loaded by {self.safety_loading:.2f}x, split evenly across "
                f"{len(members)} members."
            ),
        )


def _months_spanned(history: Sequence[Claim]) -> int:
    dates = [c.occurred for c in history]
    lo, hi = min(dates), max(dates)
    return max(1, (hi.year - lo.year) * 12 + (hi.month - lo.month) + 1)
