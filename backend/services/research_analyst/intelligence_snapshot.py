"""
AI Equity Research Analyst — Phase 1: IntelligenceSnapshot contract.

Epic 008, Phase 1 (see the Phase 1 Sprint Specification and the frozen
Epic 008B Engineering Contract §6). This module owns the snapshot and
evidence-item SCHEMA only: frozen dataclasses, the seven-state evidence
taxonomy, the owner registry, canonical serialization, and the
deterministic content hash.

Hard boundaries (Engineering Contract §5.2, Phase 1 spec §11 rule 3,
enforced by the AST regression test, not just this docstring):
  - no I/O of any kind — no network, DB, filesystem, or provider import;
  - no import of prediction_engine, any engine, adapter, or router;
  - stdlib only.

Phase 1 implements only ``snapshot_type = "live"``. The other snapshot
types (persisted / portfolio_context / screening_context / simulation_
context) are named in ``SNAPSHOT_TYPES`` as reserved vocabulary but are
NOT constructable through the Phase 1 builder and are rejected by the
Phase 1 validator — each arrives only with its own future, separately
approved phase.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields as _dc_fields
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

SNAPSHOT_CONTRACT_VERSION = "1.0"
BUILDER_VERSION = "1.0"

LIVE_SNAPSHOT_TYPE = "live"
# Reserved vocabulary only — Phase 1 constructs/accepts "live" alone.
SNAPSHOT_TYPES = (
    "live",
    "persisted",
    "portfolio_context",
    "screening_context",
    "simulation_context",
)

MARKETS = ("IN", "US")
# The platform's three real horizons. The Engineering Contract also names
# an "investment" horizon, but no owning engine output exists for it today
# — the Phase 1 spec (§8) reserves it and REJECTS it rather than silently
# mapping it to "long". Widening this tuple is a contract change.
HORIZONS = ("short", "medium", "long")
# Evidence items may additionally be horizon-agnostic (macro/scope items).
ITEM_HORIZONS = HORIZONS + ("not_applicable",)

OVERALL_STATUSES = ("COMPLETE", "PARTIAL", "STALE", "UNAVAILABLE", "INVALID")

CLAIM_TYPES = ("fact", "owner_conclusion", "risk", "counter_evidence", "invalidation", "limitation")

# Fixed market→currency/exchange mapping tables — scope METADATA supplied
# by a lookup, not a data claim and not a calculation (Phase 1 spec §8).
MARKET_CURRENCY = MappingProxyType({"IN": "INR", "US": "USD"})
MARKET_EXCHANGE = MappingProxyType({"IN": "NSE", "US": "US-composite"})

# Registered owner → permitted evidence domains (Engineering Contract §5.1;
# Phase 1 spec §9). The builder must not read outside this table and the
# validator rejects any owner/domain pairing not listed here (V-9).
# "QualityFactorsLegacy" is the pre-existing quality_factors parallel
# factor inside PredictionEngine's own output — named distinctly so it can
# NEVER be conflated with the Business Quality Engine.
OWNER_DOMAINS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "PredictionEngine": ("decision", "risk"),
    "BusinessQuality": ("quality",),
    "FinancialStrength": ("financial_strength",),
    "GrowthIntelligence": ("growth",),
    "ValuationIntelligence": ("valuation",),
    "MarketRegime": ("macro",),
    "GlobalContext": ("macro",),
    "QualityFactorsLegacy": ("quality",),
    "SnapshotBuilder": ("scope",),
})

# Exact key names that must never appear in a global "live" snapshot's
# consumer_context or in an evidence item's provenance path — user-specific
# data belongs only in the future, separately authorized context snapshots
# (Engineering Contract §6.5, §12.2; validator rule V-8).
USER_CONTEXT_DENYLIST = (
    "user_id",
    "holdings",
    "trade_id",
    "portfolio_id",
    "paper_trade_id",
    "session_id",
    "access_token",
)

USER_DECISION_BOUNDARY = (
    "This is research and explanation, not a guarantee or an instruction to trade."
)


class EvidenceState(str, Enum):
    """The seven evidence states of Engineering Contract §8.2. Distinct,
    never collapsed, never converted into an implied direction. STALE is
    reserved in Phase 1: schema-valid, but the Phase 1 builder has no
    freshness-policy owner and therefore no code path that emits it."""

    SUPPORTED = "SUPPORTED"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    STALE = "STALE"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class EvidenceTier(str, Enum):
    """Contract §6.4 tiers. Phase 1's builder emits CONFIRMED for
    owner-supplied values and UNKNOWN for non-supported states; LIKELY
    (bounded inference) is reserved for the future assembler phase —
    the builder performs no inference, so it can never emit it."""

    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SnapshotScope:
    symbol: str
    instrument_id: str
    market: str
    exchange: str
    currency: str
    horizon: str


@dataclass(frozen=True)
class SnapshotTimestamps:
    """Owner-supplied instants only. The builder never calls a clock for
    content fields — a missing generated_at stays None and is a validator
    rejection (V-1), never a now() substitution (Phase 1 spec §11 rule 8)."""

    generated_at: str | None
    data_as_of: str | None
    expires_at: str | None = None


@dataclass(frozen=True)
class SnapshotAvailability:
    overall_status: str
    owner_statuses: Mapping[str, str]


@dataclass(frozen=True)
class EvidenceItem:
    """One auditable atomic claim (Engineering Contract §6.4). Evidence
    items are traceability units — counting, ordering, or grouping them
    must never produce a synthetic score (Contract §8.1)."""

    evidence_id: str
    owner: str
    domain: str
    status: EvidenceState
    tier: EvidenceTier
    claim_type: str
    label: str
    value: Any
    display_value: str | None
    source_reference: str | None
    as_of: str | None
    captured_at: str | None
    market: str
    horizon: str
    provenance_ref: str
    reason: str | None = None
    invalidation_ref: str | None = None


@dataclass(frozen=True)
class LiveIntelligenceSnapshot:
    """The Phase 1 snapshot. Immutable after construction: the dataclass
    is frozen, sequence fields are tuples, and mapping fields are built by
    the builder as read-only ``MappingProxyType`` views over dicts it
    alone constructed (deep copies of the source — never references into
    the shared prediction cache). No ctypes-level guarantee is claimed;
    immutability is by construction and asserted by tests."""

    contract_version: str
    snapshot_id: str
    snapshot_hash: str
    snapshot_type: str
    scope: SnapshotScope
    timestamps: SnapshotTimestamps
    availability: SnapshotAvailability
    decision_context: Mapping[str, Any]
    engine_outputs: Mapping[str, Any]
    market_context: Mapping[str, Any]
    evidence_items: tuple[EvidenceItem, ...]
    provenance: Mapping[str, Any]
    consumer_context: Mapping[str, Any]
    disclosures: tuple[str, ...]


def deep_freeze(value: Any) -> Any:
    """Recursively convert dicts to read-only mapping views and lists to
    tuples. The wrapped dicts are constructed inside this function, so no
    writable reference to them escapes."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    return value


def _jsonable(value: Any) -> Any:
    """Canonical plain-python form for serialization. Handles the frozen
    structures this module defines; anything exotic falls back to str()
    at json.dumps time (see canonical_serialization)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _jsonable(getattr(value, f.name)) for f in _dc_fields(value)}
    return value


def snapshot_to_dict(snapshot: LiveIntelligenceSnapshot, *, include_hash: bool = True) -> dict:
    """Plain-dict form of a snapshot, suitable for json.dumps. With
    include_hash=False, the snapshot_hash field is omitted — the exact
    form the content hash is computed over."""
    out = _jsonable(snapshot)
    if not include_hash:
        out.pop("snapshot_hash", None)
    return out


def canonical_serialization(payload: dict) -> str:
    """One canonical JSON form: sorted keys, minimal separators, str()
    fallback for any non-JSON-native leaf. Both the builder (stamping the
    hash) and the validator (recomputing it, rule V-7) must go through
    this exact function so the two can never drift."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_snapshot_hash(snapshot: LiveIntelligenceSnapshot) -> str:
    """Deterministic SHA-256 content hash over every field except
    snapshot_hash itself. Same content → same hash; any single field
    change → different hash."""
    payload = snapshot_to_dict(snapshot, include_hash=False)
    return hashlib.sha256(canonical_serialization(payload).encode("utf-8")).hexdigest()
