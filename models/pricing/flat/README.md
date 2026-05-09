# Flat Pricing

**Everyone pays the same.**

## When to use this

- Small affinity pools where charging different premiums would cause more friction than it solves
- Family pools (charging your kids exposure-weighted premiums is weird)
- Pools where exposure is genuinely similar across members
- As a baseline before introducing fancier pricing

## When not to use this

- Pools with very different exposure profiles (e.g. one member has three motorcycles, another has none)
- Pools large enough that the unfairness compounds into resentment
- Anywhere local regulations require risk-based pricing

## How it works

1. Compute expected monthly claims from history (or from `target_payout_capacity / 12` if no history).
2. Multiply by `safety_loading` (default 1.2x) to build surplus.
3. Divide evenly across members.

## Parameters

- `safety_loading` (float, ≥1.0): multiplier on expected claims. 1.2 = 20% buffer. Higher = safer pool, higher contributions.

## Limitations

- No credibility weighting between pool history and prior beliefs.
- Treats new members and 5-year veterans identically.
- Will systematically under-reserve for pools with skewed claim distributions (one big claim dominates the mean). Pair with a robust reserving model.
