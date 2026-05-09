# Contributing to Mutual

Three kinds of contribution, three different paths in.

## I'm a developer

Stack: Python 3.11+, FastAPI, SQLAlchemy, SQLite (Postgres optional), HTMX + minimal JS, Pytest.

```bash
git clone https://github.com/YOU/mutual
cd mutual
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn api.main:app --reload
```

Open issues are tagged `good-first-issue`, `bug`, `feature`, and `architecture`. PRs need: a test, a one-line CHANGELOG entry, and (for anything user-facing) a screenshot or a paragraph in `docs/`.

## I'm an actuary or applied statistician

You are the reason this project can be more than vibes. The contribution surface:

- **Pricing models** in `models/pricing/`. Compute fair premiums given member profiles and historical claims. Each model implements `PricingModel` protocol (see `models/base.py`).
- **Reserving models** in `models/reserving/`. Estimate IBNR, required surplus, ruin probability for small pools (n < 200). Classical actuarial models often misbehave at small N — this is interesting work.
- **Validation** against synthetic histories in `data/synthetic/`. Each PR runs the full suite.
- **Documentation**. We need plain-English explainers for non-actuaries who will pick which model to use. A model without a one-page README does not get merged.

Open issues tagged `actuarial`. If you want to pair with a developer to ship your model, comment on the issue and we'll match you.

## I'm a pool organizer / domain expert

We need policy templates from real-world groups: what's covered, what isn't, contribution structure, governance rules, edge cases the group has already hit. Format is markdown in `policies/`. See `policies/family/` for the canonical example.

You don't need to write code. Open a PR with the markdown, or open an issue describing your group and we'll help you draft it.

## Code of conduct

Be kind. We are building a thing about people helping each other; act like it. The full CoC is in `CODE_OF_CONDUCT.md`.

## What we will not merge

- Tokens, coins, on-chain anything in core. (Want to build a crypto extension as a separate package? Go for it. Not in core.)
- Features that enable opening pools to strangers without affinity verification. The legal and social model breaks immediately.
- Surveillance features. No member scoring, no claim-likelihood profiling, no sharing data with third parties.
- AI-generated PRs without human review and tests.
