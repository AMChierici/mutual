"""Service-layer tests for the dashboard aggregations."""
from __future__ import annotations

from datetime import datetime, timezone


from api.claims import submit_claim
from api.contributions import record_contribution
from api.dashboard import (
    MonthBucket,
    member_contribution_status,
    monthly_buckets,
    overview_summary,
    pending_claims,
)
from api.payouts import record_payout


def _claim(session, pool, admin, amount_cents=5_000):
    return submit_claim(
        session,
        pool_id=pool.id,
        member_id=admin.id,
        amount_cents=amount_cents,
        category="medical",
        description="x",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# monthly_buckets
# ---------------------------------------------------------------------------
def test_monthly_buckets_returns_12_months(session, pool):
    buckets = monthly_buckets(session, pool.id, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    assert len(buckets) == 12


def test_monthly_buckets_oldest_first_and_includes_current(session, pool):
    buckets = monthly_buckets(session, pool.id, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    assert all(isinstance(b, MonthBucket) for b in buckets)
    # Most recent bucket is the current month.
    assert buckets[-1].year_month == "2026-05"
    # 12 months back from May 2026 → June 2025.
    assert buckets[0].year_month == "2025-06"
    # Strictly increasing.
    ym_strs = [b.year_month for b in buckets]
    assert ym_strs == sorted(ym_strs)


def test_monthly_buckets_aggregate_contributions_into_recorded_at_month(
    session, pool, admin, members
):
    # Record one contribution in March 2026 — period W11 (Mon=Mar 9) for the
    # 'period' bucketer, recorded_at also in March for the 'recorded_at' one.
    now_march = datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc)
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=10_000, period="2026-W11",
        recorded_by=admin.id, now=now_march,
    )
    buckets = monthly_buckets(session, pool.id, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    march = next(b for b in buckets if b.year_month == "2026-03")
    other = next(b for b in buckets if b.year_month == "2026-04")
    assert march.contributions_cents == 10_000
    assert other.contributions_cents == 0


# ---------------------------------------------------------------------------
# bucket_by toggle: backfilled contributions land in the period bucket
# (default), or the recorded_at bucket when the viewer asks for that view.
# ---------------------------------------------------------------------------
def test_monthly_buckets_default_bucket_by_period_for_backfill(
    session, pool, admin, members
):
    """5 contributions for distinct historical periods, all entered today.
    Default view (bucket_by='period') shows them in 5 separate columns."""
    now_today = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    # ISO weeks chosen so Monday-of-week lands in each target month:
    #   W02 → Jan 5, W06 → Feb 2, W11 → Mar 9, W15 → Apr 6, W19 → May 4.
    for i, period in enumerate(("2026-W02", "2026-W06", "2026-W11", "2026-W15", "2026-W19")):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=10_000 * (i + 1), period=period,
            recorded_by=admin.id, now=now_today,
        )
    buckets = monthly_buckets(session, pool.id, now=now_today)
    by_ym = {b.year_month: b.contributions_cents for b in buckets}
    assert by_ym["2026-01"] == 10_000
    assert by_ym["2026-02"] == 20_000
    assert by_ym["2026-03"] == 30_000
    assert by_ym["2026-04"] == 40_000
    assert by_ym["2026-05"] == 50_000


def test_monthly_buckets_recorded_at_mode_aggregates_in_today(
    session, pool, admin, members
):
    """Same scenario, opt-in to bucket_by='recorded_at' — all 5 stack up
    in today's column."""
    now_today = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    # ISO weeks chosen so Monday-of-week lands in each target month:
    #   W02 → Jan 5, W06 → Feb 2, W11 → Mar 9, W15 → Apr 6, W19 → May 4.
    for i, period in enumerate(("2026-W02", "2026-W06", "2026-W11", "2026-W15", "2026-W19")):
        record_contribution(
            session, pool_id=pool.id, member_id=members[0].id,
            amount_cents=10_000 * (i + 1), period=period,
            recorded_by=admin.id, now=now_today,
        )
    buckets = monthly_buckets(
        session, pool.id, now=now_today, bucket_by="recorded_at"
    )
    by_ym = {b.year_month: b.contributions_cents for b in buckets}
    assert by_ym["2026-05"] == 10_000 + 20_000 + 30_000 + 40_000 + 50_000
    assert by_ym["2026-01"] == 0
    assert by_ym["2026-04"] == 0


def test_monthly_buckets_payouts_unchanged_by_bucket_mode(session, pool, admin):
    """Payouts have no period field; both modes bucket payouts by
    LedgerEntry.recorded_at. Same number for both views."""
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-W01",
        recorded_by=admin.id,
        now=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    claim = _claim(session, pool, admin)
    record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000,
        recorded_by=admin.id,
        now=datetime(2026, 4, 5, tzinfo=timezone.utc),
        paid_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    period_view = {b.year_month: b.payouts_cents for b in monthly_buckets(session, pool.id, now=now, bucket_by="period")}
    rec_view = {b.year_month: b.payouts_cents for b in monthly_buckets(session, pool.id, now=now, bucket_by="recorded_at")}
    assert period_view["2026-04"] == 5_000
    assert rec_view["2026-04"] == 5_000


def test_monthly_buckets_aggregate_payouts(session, pool, admin):
    # Seed balance + approved claim + payout dated April 2026.
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=100_000, period="2026-W01",
        recorded_by=admin.id,
        now=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    claim = _claim(session, pool, admin)
    record_payout(
        session, claim_id=claim.id, amount_paid_cents=5_000,
        recorded_by=admin.id,
        now=datetime(2026, 4, 5, tzinfo=timezone.utc),
        paid_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
    )
    buckets = monthly_buckets(session, pool.id, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    april = next(b for b in buckets if b.year_month == "2026-04")
    assert april.payouts_cents == 5_000


def test_monthly_buckets_drops_data_older_than_12_months(session, pool, admin, members):
    # Contribution in May 2024 (well outside 12-month window starting June 2025)
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=10_000, period="2024-W05",
        recorded_by=admin.id,
        now=datetime(2024, 5, 1, tzinfo=timezone.utc),
    )
    buckets = monthly_buckets(session, pool.id, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    assert all(b.contributions_cents == 0 for b in buckets)


# ---------------------------------------------------------------------------
# member_contribution_status
# ---------------------------------------------------------------------------
def test_member_contribution_status_includes_all_non_inactive_members(
    session, pool, admin, members
):
    rows = member_contribution_status(session, pool.id)
    names = {r["display_name"] for r in rows}
    assert names == {admin.display_name} | {m.display_name for m in members}


def test_member_contribution_status_totals_contributions(session, pool, admin, members):
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=5_000, period="2026-W01", recorded_by=admin.id,
    )
    record_contribution(
        session, pool_id=pool.id, member_id=members[0].id,
        amount_cents=3_000, period="2026-W02", recorded_by=admin.id,
    )
    rows = {r["member_id"]: r for r in member_contribution_status(session, pool.id)}
    assert rows[members[0].id]["total_cents"] == 8_000
    assert rows[members[0].id]["last_period"] == "2026-W02"
    assert rows[members[1].id]["total_cents"] == 0
    assert rows[members[1].id]["last_period"] is None


def test_member_contribution_status_excludes_inactive(session, pool, admin, members):
    members[1].status = type(members[1].status).inactive
    session.commit()
    names = {r["display_name"] for r in member_contribution_status(session, pool.id)}
    assert members[1].display_name not in names


# ---------------------------------------------------------------------------
# pending_claims
# ---------------------------------------------------------------------------
def test_pending_claims_returns_voting_status_only(session, pool, admin):
    voting = submit_claim(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=80_000, category="x", description="y",
        occurred_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
    )
    auto = _claim(session, pool, admin)  # auto-approved
    ids = {c.id for c in pending_claims(session, pool.id)}
    assert voting.id in ids
    assert auto.id not in ids


# ---------------------------------------------------------------------------
# overview_summary
# ---------------------------------------------------------------------------
def test_overview_summary_bundles_balance_counts_and_currency(
    session, pool, admin, members
):
    record_contribution(
        session, pool_id=pool.id, member_id=admin.id,
        amount_cents=50_000, period="2026-W01", recorded_by=admin.id,
    )
    summary = overview_summary(session, pool.id)
    assert summary["currency"] == pool.currency
    assert summary["balance_cents"] == 50_000
    assert summary["pool_name"] == pool.name
    assert summary["pending_claims_count"] == 0
    assert summary["member_count"] == 1 + len(members)
