"""Service-layer tests for recording contributions."""
from __future__ import annotations

import pytest

from api.contributions import (
    BulkContributionRow,
    current_balance,
    record_bulk,
    record_contribution,
)
from api.orm import (
    AuditEvent,
    Contribution,
    LedgerEntry,
    LedgerKind,
    Member,
    MemberRole,
    MemberStatus,
    Pool,
    User,
)


# ---------------------------------------------------------------------------
# current_balance
# ---------------------------------------------------------------------------
def test_current_balance_zero_for_fresh_pool(session, pool):
    assert current_balance(session, pool.id) == 0


def test_current_balance_returns_zero_for_unknown_pool(session):
    assert current_balance(session, 99999) == 0


def test_current_balance_sums_existing_ledger_entries(session, pool):
    session.add(
        LedgerEntry(
            pool_id=pool.id,
            kind=LedgerKind.opening_balance,
            ref_id=pool.id,
            delta=10_000,
            balance_after=10_000,
        )
    )
    session.commit()
    assert current_balance(session, pool.id) == 10_000


# ---------------------------------------------------------------------------
# record_contribution — golden path
# ---------------------------------------------------------------------------
def test_record_contribution_creates_contribution_row(session, pool, admin, members):
    c = record_contribution(
        session,
        pool_id=pool.id,
        member_id=members[0].id,
        amount_cents=10_000,
        period="2026-W01",
        recorded_by=admin.id,
    )
    assert c.id is not None
    assert c.amount == 10_000
    assert c.period == "2026-W01"
    assert c.recorded_by == admin.id


def test_record_contribution_writes_ledger_entry(session, pool, admin, members):
    record_contribution(
        session,
        pool_id=pool.id,
        member_id=members[0].id,
        amount_cents=10_000,
        period="2026-W01",
        recorded_by=admin.id,
    )
    entries = session.query(LedgerEntry).filter_by(pool_id=pool.id).all()
    assert len(entries) == 1
    e = entries[0]
    assert e.kind == LedgerKind.contribution
    assert e.delta == 10_000
    assert e.balance_after == 10_000


def test_record_contribution_writes_audit_event(session, pool, admin, members):
    record_contribution(
        session,
        pool_id=pool.id,
        member_id=members[0].id,
        amount_cents=5_000,
        period="2026-W02",
        recorded_by=admin.id,
    )
    audit = (
        session.query(AuditEvent)
        .filter_by(pool_id=pool.id, kind="contribution.recorded")
        .one()
    )
    assert audit.actor_member_id == admin.id
    assert audit.payload_json["amount_cents"] == 5_000
    assert audit.payload_json["period"] == "2026-W02"
    assert audit.payload_json["member_id"] == members[0].id


def test_record_contribution_running_balance_grows(session, pool, admin, members):
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=1_000, period="2026-W01", recorded_by=admin.id,
    )
    record_contribution(
        session, pool_id=pool.id, member_id=members[1].id,
        amount_cents=2_500, period="2026-W01", recorded_by=admin.id,
    )
    record_contribution(
        session, pool_id=pool.id, member_id=members[2].id,
        amount_cents=500, period="2026-W01", recorded_by=admin.id,
    )
    entries = (
        session.query(LedgerEntry)
        .filter_by(pool_id=pool.id)
        .order_by(LedgerEntry.id)
        .all()
    )
    assert [e.balance_after for e in entries] == [1_000, 3_500, 4_000]
    assert current_balance(session, pool.id) == 4_000


def test_record_contribution_starts_from_existing_balance(session, pool, admin, members):
    # Seed an opening balance
    session.add(
        LedgerEntry(
            pool_id=pool.id, kind=LedgerKind.opening_balance,
            ref_id=pool.id, delta=10_000, balance_after=10_000,
        )
    )
    session.commit()

    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=2_000, period="2026-W01", recorded_by=admin.id,
    )
    assert current_balance(session, pool.id) == 12_000


# ---------------------------------------------------------------------------
# record_contribution — guards
# ---------------------------------------------------------------------------
def test_record_contribution_rejects_zero_amount(session, pool, admin, members):
    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=0, period="2026-W01", recorded_by=admin.id,
        )


def test_record_contribution_rejects_negative_amount(session, pool, admin, members):
    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=-1, period="2026-W01", recorded_by=admin.id,
        )


@pytest.mark.parametrize(
    "bad_period",
    ["2026-1", "26-01", "2026/01", "2026-13", "2026-00", "", "January 2026"],
)
def test_record_contribution_rejects_invalid_period_format(
    session, pool, admin, members, bad_period
):
    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=100, period=bad_period, recorded_by=admin.id,
        )


def test_record_contribution_rejects_unknown_member(session, pool, admin):
    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=pool.id, member_id=99999,
            amount_cents=100, period="2026-W01", recorded_by=admin.id,
        )


def test_record_contribution_rejects_member_in_different_pool(session, admin):
    """Belt-and-suspenders for when v1 multi-pool lands."""
    other_pool = Pool(slug="other-pool", name="Other", currency="USD", governance_config={})
    session.add(other_pool)
    session.commit()
    foreign_user = User(email="foreign@example.test", display_name="Foreign")
    session.add(foreign_user)
    session.flush()
    other_member = Member(
        user_id=foreign_user.id, pool_id=other_pool.id, display_name="Foreign",
        role=MemberRole.member, status=MemberStatus.active,
    )
    session.add(other_member)
    session.commit()

    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=admin.pool_id, member_id=other_member.id,
            amount_cents=100, period="2026-W01", recorded_by=admin.id,
        )


def test_record_contribution_does_not_commit_if_amount_invalid(
    session, pool, admin, members
):
    with pytest.raises(ValueError):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=0, period="2026-W01", recorded_by=admin.id,
        )
    assert session.query(Contribution).count() == 0
    assert session.query(LedgerEntry).count() == 0


# ---------------------------------------------------------------------------
# record_bulk
# ---------------------------------------------------------------------------
def test_record_bulk_creates_one_contribution_per_row(session, pool, admin, members):
    summary = record_bulk(
        session,
        pool_id=pool.id,
        period="2026-W01",
        rows=[
            BulkContributionRow(member_id=members[0].id, amount_cents=1_000),
            BulkContributionRow(member_id=members[1].id, amount_cents=2_000),
            BulkContributionRow(member_id=members[2].id, amount_cents=3_000),
        ],
        recorded_by=admin.id,
    )
    assert len(summary.created_contribution_ids) == 3
    assert summary.skipped_member_ids == []
    assert session.query(Contribution).count() == 3
    assert session.query(LedgerEntry).count() == 3
    assert current_balance(session, pool.id) == 6_000


def test_record_bulk_skips_zero_amount_rows(session, pool, admin, members):
    summary = record_bulk(
        session,
        pool_id=pool.id,
        period="2026-W01",
        rows=[
            BulkContributionRow(member_id=members[0].id, amount_cents=1_000),
            BulkContributionRow(member_id=members[1].id, amount_cents=0),
        ],
        recorded_by=admin.id,
    )
    assert len(summary.created_contribution_ids) == 1
    assert members[1].id in summary.skipped_member_ids


def test_record_bulk_skips_member_already_recorded_for_period(
    session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=500, period="2026-W01", recorded_by=admin.id,
    )
    summary = record_bulk(
        session,
        pool_id=pool.id,
        period="2026-W01",
        rows=[
            BulkContributionRow(member_id=members[0].id, amount_cents=1_000),
            BulkContributionRow(member_id=members[1].id, amount_cents=2_000),
        ],
        recorded_by=admin.id,
    )
    assert len(summary.created_contribution_ids) == 1
    assert members[0].id in summary.skipped_member_ids
    # member[0]'s amount was NOT double-recorded
    assert (
        session.query(Contribution)
        .filter_by(member_id=members[0].id, period="2026-W01")
        .count()
        == 1
    )


def test_record_bulk_running_balance_is_correct(session, pool, admin, members):
    record_bulk(
        session,
        pool_id=pool.id,
        period="2026-W01",
        rows=[
            BulkContributionRow(member_id=members[0].id, amount_cents=1_000),
            BulkContributionRow(member_id=members[1].id, amount_cents=2_000),
            BulkContributionRow(member_id=members[2].id, amount_cents=3_000),
        ],
        recorded_by=admin.id,
    )
    entries = (
        session.query(LedgerEntry)
        .filter_by(pool_id=pool.id)
        .order_by(LedgerEntry.id)
        .all()
    )
    assert [e.balance_after for e in entries] == [1_000, 3_000, 6_000]


def test_record_bulk_rejects_invalid_period(session, pool, admin, members):
    with pytest.raises(ValueError):
        record_bulk(
            session,
            pool_id=pool.id,
            period="2026-13",
            rows=[BulkContributionRow(member_id=members[0].id, amount_cents=100)],
            recorded_by=admin.id,
        )


def test_record_bulk_skips_members_in_other_pools(session, pool, admin, members):
    other_pool = Pool(slug="bulk-other", name="Other", currency="USD", governance_config={})
    session.add(other_pool)
    session.commit()
    foreign_user = User(email="bulk-foreign@example.test", display_name="X")
    session.add(foreign_user)
    session.flush()
    foreign = Member(
        user_id=foreign_user.id, pool_id=other_pool.id, display_name="X",
        role=MemberRole.member, status=MemberStatus.active,
    )
    session.add(foreign)
    session.commit()

    summary = record_bulk(
        session,
        pool_id=pool.id,
        period="2026-W01",
        rows=[
            BulkContributionRow(member_id=foreign.id, amount_cents=500),
            BulkContributionRow(member_id=members[0].id, amount_cents=200),
        ],
        recorded_by=admin.id,
    )
    assert foreign.id in summary.skipped_member_ids
    assert len(summary.created_contribution_ids) == 1
