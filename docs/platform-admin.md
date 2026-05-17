# Platform admin

The cross-pool operator view. One role per install. Lets the person
running the box see every pool, every user, every magic-link issued, and
the running balance per pool — read-only.

This is **not** the same as a pool admin. A pool admin manages members
inside one pool. A platform admin can look across pools, but isn't
implicitly a member of any of them (and can't post claims, vote, or
record payouts on their behalf).

## Bootstrap

The role is bound to a single email address via an env var, read at app
startup:

```bash
# docker-compose.yml or your env file
MUTUAL_PLATFORM_ADMIN_EMAIL=ops@yourdomain.example
```

On every boot, Mutual makes sure the `User` row for that email has
`is_platform_admin=True`. If the user doesn't exist yet, it's created
with no memberships (you'll log in via the normal magic-link flow — the
operator's email need only be added to a pool if you actually want to
take part in one).

To rotate the platform admin: change the env var to a new email and
restart. The old user keeps the flag until you flip it manually (or
remove the env entirely and clear the flag from the DB).

## PII redaction

By default, claim descriptions, evidence URIs, and email addresses are
**redacted** in the `/admin/*` views — emails show only the domain
(`***@example.com`), descriptions show `[redacted]`. The defaults are
on purpose: most operator tasks (verifying ledger health, spot-checking
balances, finding which pool a complaint is about) don't need raw PII.

For forensic / support work that does:

```bash
MUTUAL_PLATFORM_ADMIN_SEES_PII=1
```

Set the env var, restart, do the work, unset it. The admin layout
prints a "PII visible" banner so it's obvious when you're in the unsafe
mode.

## URLs

| URL | What it shows |
| --- | --- |
| `/admin` | Overview: pool count, user count, per-pool balances |
| `/admin/pools` | All pools, with member counts and claim counts |
| `/admin/pools/{id}` | One pool: members, recent claims (redacted), recent audit |
| `/admin/users` | All users, with their membership counts |
| `/admin/users/{id}` | One user: memberships, recent sessions, magic-link history |

## What's intentionally missing

No mutation endpoints in v1. The platform admin can't delete a pool,
force-deactivate a user, or impersonate someone. If you need to take a
destructive action, do it through SQL or via a pool admin acting inside
their pool — the audit trail is clearer that way.

Future versions may add a small set of operator actions (archive an
abandoned pool; impersonate a user with a written justification stored
in audit) — they'll be gated on a separate env var so the read-only
default stays the safe default.
