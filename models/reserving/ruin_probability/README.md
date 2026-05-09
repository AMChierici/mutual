# Ruin Probability Reserving (Bootstrap)

**How much money does the pool need to keep on hand so it doesn't go bust?**

## What it does

Simulates the next N months forward thousands of times. In each simulation, premiums come in monthly, claims arrive as a Poisson process with rate fitted from history, and claim sizes are bootstrapped (sampled with replacement) from past claims. Counts how often the pool goes negative.

Returns the reserve required to keep that probability below `1 - confidence` (default 5%).

## When to use

- Small pools (5-200 members) where parametric distributions are unstable
- Pools with at least ~10 historical claims (below that, the bootstrap is too narrow)
- Anywhere you want an honest "what's our chance of going bust" number

## When not to use

- Pools with thousands of claims — use a parametric model, it'll be tighter
- Pools where claims are heavily correlated (one storm hits everyone). This model assumes independence; correlated claims need a different approach (planned: `correlated_ruin/`).
- Brand-new pools with no history — the model falls back to a 6-month buffer placeholder, but you should re-price aggressively in the first year.

## Parameters

- `horizon_months` (int): how far forward to simulate. 12 is standard.
- `simulations` (int): number of paths. 5000 is fine; 50000 is overkill but runs in seconds.
- `monthly_premium_inflow` (float): expected premiums coming in per month.
- `confidence` (float, in `reserve()`): target probability of *not* going bust over the horizon.

## Caveats an actuary should know

- Bootstrap severity assumes the historical claim distribution is representative of the future. For pools that just hit a regime change (new members, new coverage), this is wrong.
- Poisson frequency assumption is fine for low-rate independent claims, breaks under contagion (epidemics, regional disasters).
- We do not model premium default (members not paying). `pricing/` modules should account for that, or we add a `default_rate` parameter here.
- No discounting. At horizons of 1 year and reserves measured in thousands, this is in the noise.

## Open questions / wanted improvements

- Add Bühlmann-Straub credibility weighting between pool history and a prior
- Add correlation structure (copula? shared shock model?)
- Add member-level heterogeneity (some members claim more than others)
- Confidence intervals on the reserve estimate itself (currently point estimate)
