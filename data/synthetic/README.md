# Synthetic Data

Anonymized and synthetic claim histories for testing actuarial models. Real-pool data, contributed under explicit opt-in, is anonymized through the pipeline in `tools/anonymize.py` (TODO) before landing here.

## Files

- `family_5yr.csv` (TODO) — synthetic 5-year history for a 4-person family pool
- `freelancer_3yr.csv` (TODO) — synthetic 3-year history for a 15-person freelancer collective
- `cycling_club_2yr.csv` (TODO) — synthetic 2-year history for a 30-person cycling pool

## Schema

```
claim_id, member_id, occurred_date, paid_amount, category, pool_type
```

No names. No locations more specific than country. No dates more specific than month for real-pool contributions.

## Generating synthetic data

`tools/synthesize.py` (TODO) generates plausible histories from configurable parameters. Useful for stress-testing models against scenarios you don't have real data for (heavy-tailed claims, regime changes, contagion).

## Contributing real data

Only with explicit, written consent from every pool member. We will not accept dumps. Open an issue if your pool wants to contribute and we'll walk you through the anonymization process.
