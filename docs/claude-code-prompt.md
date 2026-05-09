# Claude Code Starter Prompt

Paste the prompt below into Claude Code, in the root of the cloned `mutual` repo. It is structured so Claude Code does the boring CRUD work, leaving you free to focus on policy, governance, and actuarial design with your group and contributors.

---

## Prompt

You are working on **Mutual**, an open-source self-hosted toolkit for small-group mutual aid pools. The repo skeleton is already in place: `README.md`, `MANIFESTO.md`, `CONTRIBUTING.md`, working actuarial models in `models/` (with passing tests), policy templates in `policies/`, governance README in `governance/`, and a stub FastAPI app in `api/main.py`.

Read `docs/architecture.md` first. It defines the data model and module boundaries. Stay inside them.

Your job: build the v0 web application end-to-end, covering exactly the features listed below. Do not add features I haven't asked for. Do not add a JS framework — HTMX + Jinja templates only. Do not add authentication beyond magic-link logins. Do not touch payments. Do not add a token, a chain, or any "AI" features.

### Build, in order

1. **Database layer.** SQLAlchemy models matching the schema in `docs/architecture.md` exactly: `Pool`, `Member`, `Contribution`, `Claim`, `Vote`, `Payout`, `LedgerEntry`, `AuditEvent`. SQLite by default, path from `MUTUAL_DB_PATH` env var. Alembic migrations. One pool per install in v0; multi-pool is a later issue.

2. **Magic-link auth.** Members get a private login link sent to their email or copied by the admin out-of-band (email is optional in v0, the link itself is the credential). Sessions are server-side, stored in DB. No passwords.

3. **Setup wizard.** First-run flow: create pool, set currency, add members, choose policy template (offer the markdown files in `policies/` as starting points and let the admin paste/edit), choose governance config (which scheme applies to which claim tier), set starting balance.

4. **Contributions.** Treasurer can record contributions per member per period. Bulk entry for monthly close. Every contribution writes a `LedgerEntry`.

5. **Claims.** Any member can submit: amount, category, description, occurred_date, optional photo uploads (saved to local disk, paths in DB). Claim enters the right governance flow based on tier.

6. **Voting.** Three governance schemes in v0, all in `governance/`: `unanimous`, `majority`, `auto_approve` (under threshold). Wire them into the claim state machine. Voting UI: list of pending claims for the logged-in member, one-click approve/reject with optional reason.

7. **Payouts.** When a claim is approved, treasurer marks it paid (with date and notes). Writes a `LedgerEntry` and a `Payout`.

8. **Dashboard.** Per-pool: current balance, last 12 months of contributions and claims (chart), pending claims, member list with contribution status. A "model output" tab that runs `models.pricing.flat` and `models.reserving.ruin_probability` against current data and shows the rationales.

9. **Audit log.** Every state change writes an `AuditEvent`. Read-only viewer at `/audit`.

10. **Webhooks out.** One webhook URL per pool, configured by admin. Posts JSON on: claim submitted, claim approved, claim rejected, claim paid, monthly close due. No retries in v0, just fire-and-log.

### Constraints

- Tests for every route. Use `pytest` and `httpx.AsyncClient`. Aim for ≥80% coverage on `api/`.
- Keep templates in `api/web/templates/`, static in `api/web/static/`. HTMX from a CDN is fine.
- Every PR-equivalent commit message should describe one self-contained step from the list above.
- After each step, run `pytest` and `ruff check .` — fix what breaks before moving on.
- When a step is genuinely ambiguous, stop and ask me one specific question. Don't guess.

### What I will do

- Review your commits at each step
- Make policy and governance decisions you flag
- Pull in actuary friends to write more `models/` plugins as you go

### What you should not do

- Refactor the actuarial models — they have passing tests, leave them alone
- Add features beyond the list, even if they seem obvious
- Add docker-compose services beyond what's already there
- Add tracking, telemetry, or analytics of any kind

Start with step 1. Show me the diff. We'll iterate.

---

## Tips when running this prompt

- **Run it in chunks.** Don't ask Claude Code to do all 10 steps in one shot. Kick off step 1, review, commit, then step 2.
- **Push back on scope creep.** If Claude Code starts adding helpful extras, tell it no. The whole point of the constraint list is to keep v0 shippable.
- **Have your group test step 4 onward.** Once contributions and claims work, run a real (small) cycle with your family before adding more features. Most v0 mistakes show up in the first real claim.
