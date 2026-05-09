"""Monte Carlo ruin probability reserving for small pools.

Given current balance, expected premium inflows, and a claim distribution
fitted from history, simulate forward and return the reserve required to
keep ruin probability below the target.

Small-pool note: with n < 50 and few historical claims, the fitted
distribution is very uncertain. We bootstrap from history rather than
assuming a parametric form, which is more honest at small N. For pools
with >100 historical claims, a parametric fit (lognormal, gamma) is
usually better — see `reserving/parametric_ruin/` (TODO).
"""
from __future__ import annotations

import random
from typing import Sequence

from models.base import Claim, Member, ReservingResult


class RuinProbabilityReserving:
    name = "ruin_probability"
    version = "0.1.0"

    def __init__(
        self,
        horizon_months: int = 12,
        simulations: int = 5000,
        monthly_premium_inflow: float = 0.0,
        seed: int | None = None,
    ):
        self.horizon_months = horizon_months
        self.simulations = simulations
        self.monthly_premium_inflow = monthly_premium_inflow
        self.seed = seed

    def reserve(
        self,
        members: Sequence[Member],
        history: Sequence[Claim],
        current_balance: float,
        confidence: float = 0.95,
    ) -> ReservingResult:
        if not history:
            # No data: fall back to a crude buffer of 6 months of premium inflow.
            crude = 6 * self.monthly_premium_inflow
            return ReservingResult(
                required_reserve=crude,
                confidence=confidence,
                rationale=(
                    "No claim history; falling back to 6 months of expected "
                    "premium inflow as a placeholder reserve. Re-run after "
                    "real claims accumulate."
                ),
                diagnostics={"method": "fallback_no_history"},
            )

        rng = random.Random(self.seed)
        months = max(1, _months_spanned(history))
        claim_freq_per_month = len(history) / months
        claim_amounts = [c.paid for c in history]

        ruin_count = 0
        terminal_balances: list[float] = []

        for _ in range(self.simulations):
            balance = current_balance
            ruined = False
            for _m in range(self.horizon_months):
                balance += self.monthly_premium_inflow
                # Poisson-ish: sample number of claims this month
                n_claims = _poisson(claim_freq_per_month, rng)
                for _c in range(n_claims):
                    balance -= rng.choice(claim_amounts)  # bootstrap
                if balance < 0 and not ruined:
                    ruined = True
            if ruined:
                ruin_count += 1
            terminal_balances.append(balance)

        ruin_prob = ruin_count / self.simulations

        # Required reserve: binary-search the starting balance that gets ruin_prob
        # to (1 - confidence). For v0 we compute it analytically from the simulated
        # shortfall distribution.
        target_ruin = 1 - confidence
        if ruin_prob <= target_ruin:
            required = current_balance
        else:
            # Find the percentile of (current_balance - terminal) that covers our target
            shortfalls = sorted(current_balance - b for b in terminal_balances)
            idx = int((1 - target_ruin) * len(shortfalls))
            idx = min(idx, len(shortfalls) - 1)
            required = max(0.0, shortfalls[idx])

        return ReservingResult(
            required_reserve=round(required, 2),
            confidence=confidence,
            rationale=(
                f"Bootstrap Monte Carlo over {self.simulations} simulated "
                f"{self.horizon_months}-month paths. Observed claim frequency "
                f"{claim_freq_per_month:.2f}/month, severity bootstrapped from "
                f"{len(history)} historical claims. Ruin probability at current "
                f"balance: {ruin_prob:.3f}."
            ),
            diagnostics={
                "ruin_probability_at_current_balance": ruin_prob,
                "claim_freq_per_month": claim_freq_per_month,
                "n_historical_claims": len(history),
                "horizon_months": self.horizon_months,
                "simulations": self.simulations,
            },
        )


def _poisson(rate: float, rng: random.Random) -> int:
    """Knuth's Poisson sampler. Fine for the small rates we expect here."""
    import math

    L = math.exp(-rate)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _months_spanned(history: Sequence[Claim]) -> int:
    dates = [c.occurred for c in history]
    lo, hi = min(dates), max(dates)
    return max(1, (hi.year - lo.year) * 12 + (hi.month - lo.month) + 1)
