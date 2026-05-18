# Architecture

## Principles

- **Local-first.** SQLite by default, your data on your box.
- **Boring stack.** Python, FastAPI, SQLAlchemy, HTMX. Nothing exotic.
- **Pluggable.** Actuarial models, governance schemes, and policy templates are all swap-in modules.
- **Auditable.** Every claim, vote, and payout is an append-only ledger entry. No silent edits.
- **No tokens, no chain, no surveillance.**

## Data model (target)

Identity is split: a `User` is the global account (one row per real person,
keyed by email) and a `Membership` is that user's role inside a single pool.
One user can have memberships in multiple pools — that's how multi-pool
support works without making people maintain separate logins.

```
User
  id, email (unique), display_name, is_platform_admin, created_at

Pool
  id, slug (unique), name, currency, created_at,
  policy_template_id, governance_config, webhook_url

Membership                              -- v0 called this "Member"
  id, user_id, pool_id, display_name, joined_at, status, role
  (role: member | admin | observer)
  unique(user_id, pool_id)

Contribution
  id, pool_id, member_id (-> memberships.id), amount, period,
  recorded_at, recorded_by

Claim
  id, pool_id, member_id, amount_requested, category, description,
  evidence_uris, occurred_at, submitted_at, status
  (status: submitted | voting | approved | rejected | paid | withdrawn)

Vote
  id, pool_id, claim_id, member_id, decision, reason, cast_at
  (decision: approve | reject | abstain)

Payout
  id, pool_id, claim_id, amount_paid, paid_at, recorded_by, notes

LedgerEntry
  id, pool_id, kind, ref_id, delta, balance_after, recorded_at
  (append-only; every contribution and payout produces one)

AuditEvent
  id, pool_id, actor_member_id (-> memberships.id), kind,
  payload_json, recorded_at

LoginToken / AuthSession
  id, user_id, token, created_at, expires_at, ...
  (account-scoped: one session covers all of the user's pools)
```

The `member_id` columns on Contribution, Claim, Vote, Payout, and
AuditEvent all point at `memberships.id` (a per-pool role), not at the
global person. That keeps the audit trail honest when one human acts
across multiple pools.

## Module boundaries

```
api/         FastAPI routes, request/response models, web templates
models/      Actuarial: pricing, reserving (pluggable)
governance/  Voting schemes (pluggable)
policies/    Markdown policy templates (forked, not imported)
data/        SQLite + synthetic test data
tools/       CLI: anonymize, synthesize, export
```

## Why SQLite

For pools of 5-200 members, SQLite is not a compromise — it's the right choice. Single file, easy backup, runs on a Pi. We expose Postgres as an option for hosted deployments serving multiple pools, but core stays SQLite-first.

## Notifications

Webhooks out, nothing in. Pool admins paste a webhook URL (Signal bot, Telegram bot, Discord, plain email-via-webhook) and Mutual posts events to it. Keeps us out of the messaging-stack rabbit hole and lets pools use whatever their members already use.

## Money

Mutual does not move money in v0. The pool decides how money is held (joint bank account, treasurer's account, cash box) and treasurers record contributions and payouts in the app. v1 may integrate Plaid/Stripe for read-only balance verification. v2 maybe payouts. Maybe.

## Federation

Out of scope for v0. Tempting, dangerous. The legal model breaks the moment pools-of-pools share risk across affinity boundaries. We will revisit when we have 1000 self-installed pools and a real lawyer.
