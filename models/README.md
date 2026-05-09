# Models

Pluggable actuarial modules. Two kinds: **pricing** (what should a member contribute?) and **reserving** (how much surplus does the pool need to stay solvent?).

Both implement small Python protocols defined in `models/base.py`. A model is a folder with:

```
models/pricing/my_model/
    __init__.py        # implements PricingModel
    README.md          # plain-English explainer, one page
    tests/             # pytest, runs against data/synthetic/
```

## Why small-pool actuarial work is interesting

Classical insurance math assumes large N and weak correlation between claims. Mutual pools violate both:

- N is small (typically 5-200 members)
- Claims correlate (one storm hits five families in the same neighborhood)
- Membership is not random — affinity selection introduces structure
- Premiums are politically constrained (a family can't price-discriminate the way an insurer does)

The ruin probability math gets weird. The credibility theory gets weird. The reserving gets *very* weird. There is genuine open work here.

## Currently shipped

- `pricing/flat/` — everyone pays the same. Trivial, useful as baseline.
- `pricing/exposure_weighted/` — premium scales with declared exposure (e.g. number of bikes for a cycling pool).
- `reserving/simple_buffer/` — hold N months of expected claims. Crude, transparent.
- `reserving/ruin_probability/` — Monte Carlo ruin probability under configurable claim distributions.

## Wanted

See open issues tagged `actuarial`. Highlights:

- Bühlmann-Straub credibility for small pools (#TBD)
- Bootstrap-based reserving for n < 50 (#TBD)
- Catastrophe correlation modeling for geographically clustered pools (#TBD)
- Fairness-constrained pricing (premiums that satisfy group fairness criteria) (#TBD)
