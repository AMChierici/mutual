"""Protocols every actuarial model implements.

Keep these tiny and stable. Everything that varies between models lives in
their own module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Member:
    id: str
    joined: date
    exposure: float = 1.0  # arbitrary, model-defined units (e.g. bikes, kids)
    attributes: dict | None = None  # free-form, model-specific


@dataclass(frozen=True)
class Claim:
    id: str
    member_id: str
    occurred: date
    paid: float
    category: str | None = None


@dataclass(frozen=True)
class PricingResult:
    premiums: dict[str, float]  # member_id -> per-period contribution
    period: str  # e.g. "monthly"
    rationale: str  # plain-English explanation, shown to members


@dataclass(frozen=True)
class ReservingResult:
    required_reserve: float
    confidence: float  # e.g. 0.95
    rationale: str
    diagnostics: dict


class PricingModel(Protocol):
    name: str
    version: str

    def price(
        self,
        members: Sequence[Member],
        history: Sequence[Claim],
        target_payout_capacity: float,
    ) -> PricingResult: ...


class ReservingModel(Protocol):
    name: str
    version: str

    def reserve(
        self,
        members: Sequence[Member],
        history: Sequence[Claim],
        current_balance: float,
        confidence: float = 0.95,
    ) -> ReservingResult: ...
