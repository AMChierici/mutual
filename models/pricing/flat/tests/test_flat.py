from datetime import date

import pytest

from models.base import Claim, Member
from models.pricing.flat import FlatPricing


def _members(n: int):
    return [Member(id=f"m{i}", joined=date(2024, 1, 1)) for i in range(n)]


def test_empty_pool_raises():
    p = FlatPricing()
    with pytest.raises(ValueError):
        p.price([], [], target_payout_capacity=1000)


def test_no_history_falls_back_to_target():
    p = FlatPricing(safety_loading=1.0)
    result = p.price(_members(4), [], target_payout_capacity=4800)
    # expected monthly = 4800/12 = 400; per member = 100
    assert all(v == 100.0 for v in result.premiums.values())
    assert result.period == "monthly"


def test_history_drives_pricing():
    members = _members(2)
    history = [
        Claim(id="c1", member_id="m0", occurred=date(2024, 1, 15), paid=200),
        Claim(id="c2", member_id="m1", occurred=date(2024, 2, 10), paid=400),
    ]
    p = FlatPricing(safety_loading=1.0)
    # 600 over 2 months = 300/month; per member = 150
    result = p.price(members, history, target_payout_capacity=0)
    assert result.premiums["m0"] == 150.0
    assert result.premiums["m1"] == 150.0


def test_safety_loading_applied():
    p = FlatPricing(safety_loading=1.5)
    result = p.price(_members(2), [], target_payout_capacity=2400)
    # 2400/12 = 200; *1.5 = 300; /2 = 150
    assert all(v == 150.0 for v in result.premiums.values())


def test_loading_below_one_rejected():
    with pytest.raises(ValueError):
        FlatPricing(safety_loading=0.9)
