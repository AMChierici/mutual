"""Tests for the three v0 governance schemes."""
from __future__ import annotations

import pytest

from governance import TallyOutcome, get_scheme


# ---------------------------------------------------------------------------
# unanimous
# ---------------------------------------------------------------------------
@pytest.fixture
def unanimous():
    return get_scheme("unanimous")


def test_unanimous_all_approve_passes(unanimous):
    assert unanimous(approve=5, reject=0, abstain=0, eligible=5) == TallyOutcome.approved


def test_unanimous_partial_approve_pending(unanimous):
    assert unanimous(approve=3, reject=0, abstain=0, eligible=5) == TallyOutcome.pending


def test_unanimous_any_reject_kills_immediately(unanimous):
    assert unanimous(approve=4, reject=1, abstain=0, eligible=5) == TallyOutcome.rejected


def test_unanimous_no_votes_pending(unanimous):
    assert unanimous(approve=0, reject=0, abstain=0, eligible=5) == TallyOutcome.pending


def test_unanimous_abstain_blocks_unanimity(unanimous):
    """An abstain isn't an approve. Stays pending until someone changes."""
    assert unanimous(approve=4, reject=0, abstain=1, eligible=5) == TallyOutcome.pending


# ---------------------------------------------------------------------------
# majority (> 50% of eligible)
# ---------------------------------------------------------------------------
@pytest.fixture
def majority():
    return get_scheme("majority")


def test_majority_pool_of_5_needs_3_yes(majority):
    assert majority(approve=2, reject=0, abstain=0, eligible=5) == TallyOutcome.pending
    assert majority(approve=3, reject=0, abstain=0, eligible=5) == TallyOutcome.approved


def test_majority_pool_of_5_needs_3_no(majority):
    assert majority(approve=0, reject=2, abstain=0, eligible=5) == TallyOutcome.pending
    assert majority(approve=0, reject=3, abstain=0, eligible=5) == TallyOutcome.rejected


def test_majority_pool_of_4_needs_3_yes(majority):
    """4-person pool: 2 is exactly half, not majority."""
    assert majority(approve=2, reject=0, abstain=0, eligible=4) == TallyOutcome.pending
    assert majority(approve=3, reject=0, abstain=0, eligible=4) == TallyOutcome.approved


def test_majority_even_split_pending(majority):
    assert majority(approve=2, reject=2, abstain=0, eligible=5) == TallyOutcome.pending


def test_majority_no_votes_pending(majority):
    assert majority(approve=0, reject=0, abstain=0, eligible=5) == TallyOutcome.pending


def test_majority_solo_pool_one_vote_decides(majority):
    """1-person pool — 1 approve is > 50%."""
    assert majority(approve=1, reject=0, abstain=0, eligible=1) == TallyOutcome.approved


# ---------------------------------------------------------------------------
# auto_approve
# ---------------------------------------------------------------------------
@pytest.fixture
def auto_approve():
    return get_scheme("auto_approve")


def test_auto_approve_always_passes(auto_approve):
    """Defensive: claims routed to auto_approve never reach voting (step 5
    sets them straight to approved). If someone calls tally anyway, the
    answer is approved."""
    assert auto_approve(approve=0, reject=0, abstain=0, eligible=5) == TallyOutcome.approved
    assert auto_approve(approve=0, reject=5, abstain=0, eligible=5) == TallyOutcome.approved


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_unknown_scheme_raises_keyerror():
    with pytest.raises(KeyError):
        get_scheme("oligarchy")


def test_known_schemes_listed():
    from governance import list_schemes
    assert set(list_schemes()) == {"unanimous", "majority", "auto_approve"}
