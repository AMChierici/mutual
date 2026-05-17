# Getting Started — your first day with Mutual

A non-technical walkthrough for pool organizers. Read this end-to-end before you
type anything; the install itself is short, but the *agreement* before it
matters more than the software.

If you're a developer, see [`architecture.md`](architecture.md) and
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) instead.

## What you'll have at the end

A small web app running on your laptop, Pi, or home server, that your family
or group can open in a browser. It tracks:

- **Who's in the pool** and what role each person has
- **What's been contributed** (weekly, by ISO week)
- **What's been claimed**, **how it was decided**, and **whether it was paid**
- A **rolling 12-month chart** of money in vs out, plus an actuarial-models tab

It does **not** move money. The cash lives in your joint bank, Wise, Revolut,
or cash box. Mutual is the ledger and decision log on top.

## Before you install anything

Have these conversations with your group first. Software does not fix unclear
agreements.

1. **Who is in?** Names, contact, what role each person plays.
2. **Where does the money actually live?** A joint bank account is simplest.
   Whoever has access is the treasurer.
3. **What do you want to cover?** Look at [`policies/`](../policies/) for
   templates. Pick one and edit it together. Print the final version and put it
   on a fridge.
4. **How do you decide?** Unanimous? Majority? Auto-approve under a threshold?
   Mutual ships with three v0 schemes (`auto_approve`, `majority`, `unanimous`).
5. **What's the failsafe?** What happens if the pool runs dry? If two people
   leave at once? If someone files a claim everyone thinks is unreasonable?
   Write the answers down *before* it happens.

## Worked example: 4-person family pool, weekly €15

Concrete enough to copy, small enough to actually run.

| Person | Role | Notes |
| --- | --- | --- |
| Ada (you) | `admin` | Runs the app, handles the bank transfer for payouts |
| Partner | `member` | Same household |
| Parent A | `member` | Different household, joins via family Wise account |
| Parent B | `member` | Same |

Settings during the wizard:

- Currency: **EUR**
- Starting balance: **€200** (whatever's already in the joint Wise/Revolut)
- Tiers:
  - Up to **€100** → `auto_approve`
  - Up to **€500** → `majority`
  - Anything bigger → `unanimous`
- Policy: edit the `family` template. Common starting line: *"We cover
  unexpected medical co-pays, dental emergencies, vet bills, kids' school
  trips, broken-glasses-type things. We don't cover routine groceries or
  already-budgeted items. Over €500 needs everyone's yes."*

## Install

You need Docker and a few minutes.

```bash
git clone https://github.com/YOU/mutual
cd mutual
docker compose up -d
```

Open `http://localhost:8000` (or whatever IP your server has on your network).

The container runs Alembic migrations automatically on startup, so the SQLite
database is ready before the first request. Data persists in `./data/db/` on
your host machine — it survives `docker compose down` (and `docker compose
down -v`, since it's a bind mount).

## First-run setup (~5 minutes)

`localhost:8000` will redirect you to `/setup`. The wizard walks you through:

1. Pool name, currency, starting balance
2. Members. **At least one must have role `admin` — that's you.** Email is
   optional in v0; you'll send login links out-of-band.
3. Policy template (edit inline, or write your own from blank)
4. Governance tiers (the three from the example above are sensible defaults)

When you submit, you're **logged in immediately** — the wizard sets your
session cookie *and* shows a backup magic-link URL. **Bookmark that URL or
save it in your password manager**: it's how you sign in from a different
device or after clearing cookies.

## Inviting the rest of your family

There's no member-management UI in v0 yet (it's the next thing on the roadmap).
For now, mint each member's magic link from the shell:

```bash
docker compose exec mutual python -c "
from api.db import make_engine, make_session_factory
from api.auth import create_login_token
from api.orm import Member

engine = make_engine()
SessionLocal = make_session_factory(engine)
with SessionLocal() as s:
    print('Members:')
    for m in s.query(Member).all():
        print(f'  {m.id}: {m.display_name} ({m.role.value}, {m.status.value})')
    member_id = int(input('Mint a link for which member id? '))
    tok = create_login_token(s, member_id)
    print(f'URL: http://localhost:8000/auth/login/{tok.token}')
"
```

Copy each URL into Signal/SMS/email/whatever channel you use. Each person
clicks it once, lands on the dashboard, and stays logged in for 30 days.

## Weekly rhythm

This is what every-week feels like:

- **Members move money** — each person sends €15 to the joint Wise account
  (or whatever your group uses) on a chosen day.
- **You record it** — open `/contributions/bulk`. The form pre-fills the
  current ISO week (e.g. `2026-W19`). Type each person's amount, submit. The
  ledger updates and the dashboard chart gets a new green slice.

## Day to day

- **Anyone submits a claim** — `/claims/new`. Amount, category, description,
  date, optional photos of receipts.
  - Under €100: auto-approves immediately.
  - €100–€500: status `voting`, others vote.
  - Over €500: same, needs *everyone's* yes.
- **Voters get nudged** — v0 has no built-in email. Use your group chat to
  ping people: *"new claim, vote at /claims/pending"*. Each voter clicks
  Approve or Reject. Once the threshold is met, the claim flips to
  `approved` (or `rejected`).
- **You pay it** — open the approved claim, click "Mark as paid", note how
  (e.g. "Wise transfer ref XXX"). The ledger reflects it.
- **Look at `/audit`** anytime to see who decided what and when.

## Where to look in the app

Each pool lives under `/pools/{slug}/...`, where `{slug}` is auto-generated
from the pool's name during setup (and admin-editable later). The slug
appears in URLs and audit-event payloads.

| URL | What it is |
| --- | --- |
| `/` | Account home — redirects to your pool list, or to your single pool if you only belong to one |
| `/pools/` | Picker — list of every pool you're a member of |
| `/pools/new` | Wizard to start a *second* pool under your existing account |
| `/pools/{slug}/` | Pool dashboard: balance, 12-month chart, pending claims, member list |
| `/pools/{slug}/models` | Actuarial output: pricing + reserving rationales |
| `/pools/{slug}/claims` | All claims in this pool |
| `/pools/{slug}/claims/new` | Submit a claim |
| `/pools/{slug}/claims/pending` | Claims awaiting *your* vote |
| `/pools/{slug}/audit` | Read-only audit log |
| `/pools/{slug}/settings` | Admin: webhook URL, manual "monthly close" trigger |
| `/login` | Paste a magic link (if you cleared cookies) |

Old v0 bookmarks (`/claims`, `/audit`, `/settings`, etc.) still work for
one release — they 303 to `/pools/{your-slug}/...` automatically.

## Running more than one pool

A single Mutual install can host any number of pools. Some reasons to do it:

- Your family pool and a separate friend-group pool, kept on the same
  server but with separate ledgers and members.
- A "test" pool for trying out a new governance scheme without disturbing
  the real one.

To create a second pool: open `/pools/new`, run the same fields as the
first-run wizard (name, currency, opening balance, governance tiers),
submit. You become the only admin of the new pool; invite the rest from
the future members page (M3, in progress).

A single login (one magic-link session) covers every pool you're a member
of. The header on each pool's dashboard has a "Switch pool" link back to
the picker.

## Common bumps

- **"It says I'm not authenticated and won't let me in."** Your cookie
  expired or never existed in this browser. Mint a fresh link with the shell
  snippet above, paste it into the URL bar.
- **"My balance is wrong."** Look at `/audit`. Every state change is there.
  Most "wrong balance" reports trace to a missing or duplicated contribution
  entry.
- **"`/setup` keeps bouncing me to `/`."** A pool already exists in the DB.
  Either log in (via a magic link) or — if you're sure you want to start
  over — `rm -f data/db/mutual.sqlite` and `docker compose restart`.
- **"Port 8000 is busy."** Edit `docker-compose.yml`, change the host port
  in the `8000:8000` mapping (e.g. `9000:8000`).

## When something feels weird

Open an issue or a discussion on the repo. Most "weird" things are governance
questions that other pools have already worked through — write yours down
even if you solve it yourself, future organizers will thank you.
