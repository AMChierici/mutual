# Mutual

**Self-hosted infrastructure for mutual aid pools, ROSCAs, and small-group risk sharing.**

Mutual is a toolkit for the savings circles, family safety nets, and affinity-group risk pools that millions of people already run on WhatsApp threads and shared spreadsheets. It's not insurance. It's the software layer for the way humans have pooled risk for centuries — friendly societies, hui, susu, tanda, takaful — made auditable, forkable, and yours.

We think insurance has become extractive, opaque, and adversarial. This is one small move in the other direction.

## What it does

- Tracks contributions and claims for a defined group (family, co-op, gym, collective)
- Runs configurable governance: unanimous, majority, jury-of-N, vetoes
- Pluggable actuarial models for pricing and reserving (write your own, submit a PR)
- Forkable policy templates in plain markdown
- Local-first: SQLite by default, your data stays on your box
- Webhooks for notifications — wire up Signal, Telegram, email, whatever you use

## What it does *not* do

- Hold money. A real bank account holds money. Mutual tracks the ledger.
- Replace regulated insurance. For homes, cars, and health, keep your real policies.
- Federate pools across strangers. Affinity groups only — that line matters legally and socially.

## Quickstart

```bash
git clone https://github.com/YOU/mutual
cd mutual
docker compose up
# open http://localhost:8000
```

First run walks you through creating a pool, adding members, picking a policy template, and setting governance rules.

## For actuaries

We want you. See [`models/README.md`](models/README.md) for the pricing/reserving plugin interface and [`data/synthetic/`](data/synthetic/) for test claim histories. Open issues tagged `actuarial` are looking for owners.

## For developers

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Stack is Python + FastAPI + SQLite + HTMX. Boring on purpose.

## For everyone else

See [`docs/getting-started.md`](docs/getting-started.md) for a non-technical walkthrough, and [`policies/`](policies/) for example pool setups you can fork.

## License

AGPL-3.0. If you run a hosted version, your users get the source.

## Status

Pre-alpha. We're running it on real family pools to find the sharp edges. Don't trust it with money you can't afford to lose track of yet.
