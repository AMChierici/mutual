# Governance

Pluggable voting and approval rules. Each scheme implements a small protocol; pools pick which one applies to which claim tier.

## Currently shipped

- `unanimous/` — everyone must approve. Used for high-stakes claims in tight pools.
- `majority/` — simple majority of eligible voters.
- `supermajority/` — configurable threshold (default 2/3).
- `jury/` — randomly selected N members vote; rest of pool is uninvolved. Reduces social pressure on small pools.
- `parent_veto/` — designated members can override any decision (family pools).
- `auto_approve/` — claims under a threshold approved automatically and logged.

## Wanted

- Quadratic voting for pools where intensity of preference matters
- Conviction voting for slow-moving structural changes
- Liquid democracy (delegate your vote to a trusted member)

See `governance/base.py` for the protocol.
