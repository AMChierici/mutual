# Mutual

**Self-hosted infrastructure for mutual aid pools, ROSCAs, and small-group
risk sharing.**

Mutual is a toolkit for the savings circles, family safety nets, and affinity
groups that millions of people already run on WhatsApp threads and shared
spreadsheets. It's not insurance. It's the software layer for the way humans
have pooled risk for centuries — friendly societies, hui, susu, tanda,
takaful — made auditable, forkable, and yours.

We think insurance has become extractive, opaque, and adversarial. This is
one small move in the other direction. Read the [manifesto](MANIFESTO.md) for
why.

---

## What it is

- A FastAPI + SQLite + HTMX web app you run on a laptop, Pi, or home server
  via Docker
- Member auth via **magic links** (no passwords)
- A weekly contribution ledger, per-claim voting, payout tracking
- Three v0 governance schemes (`auto_approve`, `majority`, `unanimous`),
  pluggable to add more
- A read-only **audit log** of every state change
- Pluggable **actuarial models** in `models/` — pricing and reserving — that
  the dashboard runs against your real pool data
- Outbound **webhooks** so admins can wire any notification channel they
  already use (Signal, Telegram, Discord, email-via-bot, etc.)

## What it isn't

- **It doesn't hold money.** A real bank account, joint Wise/Revolut, or
  cash box holds money. Mutual is the ledger and decision log on top.
- **It isn't regulated insurance.** For homes, cars, and health, keep your
  real policies. Mutual is for the small uncovered gaps and family safety
  nets where formal insurance is too expensive or doesn't apply.
- **It isn't federated.** Affinity groups only — that line matters legally
  and socially. We're not building a re-insurance protocol.
- **No tokens, no chain, no AI hype.** See the manifesto.

---

## Quick start

```bash
git clone https://github.com/YOU/mutual
cd mutual
docker compose up -d
# open http://localhost:8000
```

That's it for the install. The container runs Alembic migrations on startup
so the database is ready before the first request. Visit
`http://localhost:8000` and you'll be redirected to `/setup`.

For a complete first-day walkthrough — including a real 4-person family
example, the weekly rhythm, and how to invite the rest of your group — read
**[`docs/getting-started.md`](docs/getting-started.md)**. It's the right
starting point if you're not a developer.

---

## What members can do

| Role | Can do |
| --- | --- |
| `admin` | Run the app, record the weekly contribution close, mark approved claims paid, configure the outbound webhook, mint magic links for other members. Also acts as treasurer in v0. |
| `member` | Log in via magic link, see the dashboard / claims / past decisions / audit log, submit claims with photo evidence, vote on `voting`-status claims. |
| `observer` | Read everything; can't submit claims or vote. Useful for ex-members, prospective members, or family members who just want visibility. |

The audit log is visible to all roles — mutual aid pools are transparent by
design.

---

## Architecture (one paragraph)

Local-first single-pool-per-install web app. FastAPI for routes; SQLAlchemy 2
ORM over SQLite (Alembic migrations); Jinja templates with HTMX for
interactive bits; no JavaScript framework. Every state change writes one
`AuditEvent` row, and the same change can fire one outbound webhook (POST
JSON, fire-and-log, no retries). Three pluggable Python modules:
`models/pricing/*` (per-member contribution suggestions), `models/reserving/*`
(required reserve and ruin probability), `governance/*` (vote tally schemes).
Full architectural details in [`docs/architecture.md`](docs/architecture.md).

---

## Repository layout

```
api/         FastAPI routes, request/response models, web templates, services
models/      Actuarial models — pricing, reserving (pluggable, BSD-style API)
governance/  Voting-scheme plugins (unanimous / majority / auto_approve)
policies/    Markdown policy templates (forked, not imported)
docs/        getting-started, architecture, legal-notes
data/        SQLite + synthetic test data
tools/       (planned) CLI: anonymize, synthesize, export
tests/       pytest suite (351 tests at v0)
alembic/     migrations
```

---

## Status

**Pre-alpha.** All ten v0 features are wired:

| # | Feature |
| --- | --- |
| 1 | Database layer (SQLAlchemy ORM + Alembic) |
| 2 | Magic-link auth, server-side sessions |
| 3 | Setup wizard (one pool per install) |
| 4 | Contributions (single + weekly bulk close) |
| 5 | Claims (with photo evidence) |
| 6 | Voting + tier-routed governance |
| 7 | Payouts + ledger-tied state machine |
| 8 | Dashboard with chart + actuarial-output tab |
| 9 | Audit log (read-only viewer at `/audit`) |
| 10 | Outbound webhooks |

Plus a public `/login` page and a dashboard chart that lets you toggle
between bucketing contributions by **period** (default — what you typed)
and **recorded date** (when the row was written).

Known gaps documented in [`docs/getting-started.md`](docs/getting-started.md):

- No member-management UI yet — invite via shell snippet
- No built-in email notifications — wire the outbound webhook to a bot
- One pool per install today; multi-pool is in progress (see below)

**Upgrading from a v0 (pre-multi-account) install:** the next migration
(`alembic upgrade head`) is M1 of the multi-account expansion. It adds a
`users` table, renames `members` → `memberships`, gives each pool a slug,
and rewires auth tokens to bind to a user instead of a single membership.
Existing data is auto-migrated: the one existing pool gets a slug derived
from its name, and members without an email get a placeholder
`user+<id>@local.invalid` so the new `users.email UNIQUE NOT NULL`
constraint holds. UI is unchanged in M1; multi-pool URLs and admin
screens land in the following milestones.

We're running it on real family pools to find the sharp edges. **Don't
trust it with money you can't afford to lose track of yet.**

---

## Contributing

Three ways to help, in order of impact for early-stage Mutual.

### 1. Run a pool and tell us what hurts

The fastest improvements come from real-world friction. Open an issue
describing what your pool is, what felt clunky, and what you wished worked
differently. We tag these `from-the-field`.

### 2. Contribute code

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full guide. Quick orientation:

- **Stack**: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Jinja, HTMX. No
  JS framework.
- **Tests**: pytest + httpx.AsyncClient. Target ≥80% coverage on `api/`
  and `governance/`.
- **TDD by default**: write a failing test first when adding behavior. See
  the existing test files for the pattern (`tests/test_*_service.py` for
  pure logic, `tests/test_*_routes.py` for HTTP).
- **Lint**: `ruff check .`. Run before pushing.
- **Migrations**: `alembic revision --autogenerate -m "..."`, then audit
  the generated file (autogen misses some SQLite quirks — see existing
  migration files for examples).

Local dev setup:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]" alembic
.venv/bin/pytest                     # full suite
.venv/bin/ruff check .               # lint
MUTUAL_DB_PATH=/tmp/mutual.sqlite .venv/bin/alembic upgrade head   # migrate
```

Areas with named gaps in [`docs/architecture.md`](docs/architecture.md) and
issues tagged `help-wanted`:

- `/members` admin page (one-click invite, deactivate)
- Email notifications via Resend / Postmark / SES
- More governance schemes (`supermajority`, `jury`, `parent_veto` —
  stubs are in `governance/README.md`)
- More policy templates in `policies/`
- A `tools/` CLI for export / anonymize / synthesize

### 3. Contribute claim data for public actuarial models

This is the long-term differentiator. Pricing and reserving for tiny pools
is genuinely hard because each pool has too few claims to fit a stable
distribution. The fix is **community priors**: anonymized, aggregated
claim histories from many pools, used as priors that the per-pool model
narrows down with local data.

How you can help today:

1. **Run your pool.** After a few months of real usage you'll have
   contribution and claim history.
2. **Export it anonymized.** A `tools/anonymize` CLI is on the roadmap;
   until it lands, the manual SQL recipe is:

   ```sql
   -- claim history, no member identities, no descriptions
   SELECT
       c.id,
       printf('m%d', dense_rank() OVER (ORDER BY c.member_id)) AS anon_member,
       c.amount_requested,
       c.category,
       c.occurred_at,
       c.status,
       p.amount_paid
   FROM claims c
   LEFT JOIN payouts p ON p.claim_id = c.id;
   ```

   Save the output as CSV. Strip pool name and currency. Keep claim
   amounts and categories — those are what the models train on.
3. **Open a PR adding it under `data/contributed/`** with:
   - The anonymized CSV
   - A short README documenting the pool's size, geography (country
     resolution only), policy summary, and the time window covered
   - Confirmation from your pool that they consented to public release
4. **Or write an actuarial model.** Drop a new pricing or reserving
   plug-in into `models/`. The contracts are tiny and stable
   (see [`models/base.py`](models/base.py)). Existing examples in
   `models/pricing/flat/` and `models/reserving/ruin_probability/` show
   the shape.

We do not collect data automatically. Every contributed dataset is opt-in,
PR-reviewed, and your pool's choice.

### 4. Contribute policy templates

If your pool has a clear, well-tested set of rules — what's covered, what
isn't, who decides — drop a markdown file under `policies/<name>/README.md`
in a PR. Future organizers will fork it.

---

## License

**AGPL-3.0-or-later**. If you run a hosted version, your users get the
source. See [`LICENSE`](LICENSE) (or — at the time of writing — the AGPL
text linked from `pyproject.toml`).

The actuarial models and governance schemes are part of this licensed work;
contribute back if you fork them. Policy templates and contributed claim
data are intended to be CC0 / public domain — file an issue if you want a
template or dataset re-licensed.

---

## Manifesto and reading list

- [`MANIFESTO.md`](MANIFESTO.md) — why this exists
- [`docs/architecture.md`](docs/architecture.md) — what's in the box and why
- [`docs/getting-started.md`](docs/getting-started.md) — first-day walkthrough
- [`docs/legal-notes.md`](docs/legal-notes.md) — what Mutual is and isn't,
  legally
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — house rules
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to send code, data, or policy
