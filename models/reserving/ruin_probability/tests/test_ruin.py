from datetime import date

from models.base import Claim, Member
from models.reserving.ruin_probability import RuinProbabilityReserving


def _members(n: int):
    return [Member(id=f"m{i}", joined=date(2024, 1, 1)) for i in range(n)]


def _history(n: int, paid: float = 100.0):
    return [
        Claim(id=f"c{i}", member_id=f"m{i % 3}", occurred=date(2024, 1 + (i % 12), 15), paid=paid)
        for i in range(n)
    ]


def test_no_history_uses_fallback():
    r = RuinProbabilityReserving(monthly_premium_inflow=200, seed=42)
    out = r.reserve(_members(4), [], current_balance=0)
    assert out.diagnostics["method"] == "fallback_no_history"
    assert out.required_reserve == 1200  # 6 * 200


def test_well_funded_pool_needs_no_extra():
    """A pool sitting on a huge balance relative to claim history should not need more."""
    r = RuinProbabilityReserving(simulations=2000, monthly_premium_inflow=500, seed=42)
    out = r.reserve(_members(5), _history(12, paid=50), current_balance=100_000)
    assert out.required_reserve <= 100_000
    assert out.diagnostics["ruin_probability_at_current_balance"] < 0.05


def test_underfunded_pool_needs_more():
    """A pool with no balance and ongoing claims should need a meaningful reserve."""
    r = RuinProbabilityReserving(simulations=2000, monthly_premium_inflow=10, seed=42)
    out = r.reserve(_members(5), _history(24, paid=100), current_balance=0)
    assert out.required_reserve > 0
    assert out.diagnostics["ruin_probability_at_current_balance"] > 0.05


def test_diagnostics_include_required_fields():
    r = RuinProbabilityReserving(simulations=500, seed=1)
    out = r.reserve(_members(3), _history(6), current_balance=1000)
    for key in (
        "ruin_probability_at_current_balance",
        "claim_freq_per_month",
        "n_historical_claims",
        "horizon_months",
        "simulations",
    ):
        assert key in out.diagnostics


def test_reproducible_with_seed():
    r1 = RuinProbabilityReserving(simulations=500, seed=7)
    r2 = RuinProbabilityReserving(simulations=500, seed=7)
    h = _history(10)
    m = _members(3)
    assert r1.reserve(m, h, 500).required_reserve == r2.reserve(m, h, 500).required_reserve
