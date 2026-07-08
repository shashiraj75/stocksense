"""
AI Equity Research Analyst — Phase 1: ContractValidator.

Implements the Phase 1 Sprint Specification's ten rejection rules
(V-1…V-10), which realize Engineering Contract §6.5 for the "live"
snapshot type. The validator REJECTS with structured rule ids and
reasons; it never repairs, fills in, re-derives, or substitutes a field
(Contract §3.1: a ContractValidator must not "repair" missing evidence
by inference). An invalid snapshot produces no grounded report — the
caller's correct output is a bounded availability/error response, never
a fabricated fallback.

All violations are collected in one pass (not fail-fast) so a rejection
names everything wrong at once.

Same import boundary as the rest of the package: stdlib plus the
package's own schema module only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from services.research_analyst.intelligence_snapshot import (
    EvidenceItem,
    EvidenceState,
    EvidenceTier,
    CLAIM_TYPES,
    HORIZONS,
    ITEM_HORIZONS,
    LIVE_SNAPSHOT_TYPE,
    LiveIntelligenceSnapshot,
    MARKETS,
    OWNER_DOMAINS,
    SNAPSHOT_CONTRACT_VERSION,
    USER_CONTEXT_DENYLIST,
    compute_snapshot_hash,
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_STATES = {s.value for s in EvidenceState}
_VALID_TIERS = {t.value for t in EvidenceTier}


@dataclass(frozen=True)
class Violation:
    rule_id: str
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    violations: tuple[Violation, ...]


def _enum_value(v) -> str:
    return getattr(v, "value", v)


def _denylisted_keys(mapping: Mapping, prefix: str = "") -> list[str]:
    hits: list[str] = []
    for key, value in mapping.items():
        path = f"{prefix}{key}"
        if str(key) in USER_CONTEXT_DENYLIST:
            hits.append(path)
        if isinstance(value, Mapping):
            hits.extend(_denylisted_keys(value, prefix=f"{path}."))
    return hits


def validate_snapshot(snapshot: LiveIntelligenceSnapshot) -> ValidationResult:
    """Validate one snapshot against rules V-1…V-10. Returns a
    ValidationResult whose violations tuple is empty iff valid."""
    v: list[Violation] = []
    scope = snapshot.scope

    # V-1 — required scope/version/timestamp fields present and non-empty.
    for label, value in (
        ("scope.market", scope.market),
        ("scope.horizon", scope.horizon),
        ("scope.symbol", scope.symbol),
        ("contract_version", snapshot.contract_version),
        ("timestamps.generated_at", snapshot.timestamps.generated_at),
    ):
        if value is None or (isinstance(value, str) and not value.strip()):
            v.append(Violation("V-1", f"required field {label} is missing or empty"))

    # V-2 — enum domains.
    if scope.market and scope.market not in MARKETS:
        v.append(Violation("V-2", f"market {scope.market!r} not in {MARKETS}"))
    if scope.horizon and scope.horizon not in HORIZONS:
        v.append(Violation("V-2", f"horizon {scope.horizon!r} not in {HORIZONS} — reserved horizons are rejected, never mapped"))
    if snapshot.snapshot_type != LIVE_SNAPSHOT_TYPE:
        v.append(Violation("V-2", f"Phase 1 validates snapshot_type '{LIVE_SNAPSHOT_TYPE}' only, got {snapshot.snapshot_type!r}"))
    if snapshot.contract_version and snapshot.contract_version != SNAPSHOT_CONTRACT_VERSION:
        v.append(Violation("V-2", f"unknown contract_version {snapshot.contract_version!r}"))
    for item in snapshot.evidence_items:
        if _enum_value(item.status) not in _VALID_STATES:
            v.append(Violation("V-2", f"evidence {item.evidence_id!r}: unknown status {_enum_value(item.status)!r}"))
        if _enum_value(item.tier) not in _VALID_TIERS:
            v.append(Violation("V-2", f"evidence {item.evidence_id!r}: unknown tier {_enum_value(item.tier)!r}"))
        if item.claim_type not in CLAIM_TYPES:
            v.append(Violation("V-2", f"evidence {item.evidence_id!r}: unknown claim_type {item.claim_type!r}"))
        if item.horizon not in ITEM_HORIZONS:
            v.append(Violation("V-2", f"evidence {item.evidence_id!r}: unknown horizon {item.horizon!r}"))

    # V-3 — every item carries owner + id; ids unique within the snapshot.
    seen_ids: set[str] = set()
    for item in snapshot.evidence_items:
        if not item.owner or not str(item.owner).strip():
            v.append(Violation("V-3", f"evidence item {item.label!r} has no owner"))
        if not item.evidence_id or not str(item.evidence_id).strip():
            v.append(Violation("V-3", f"evidence item {item.label!r} has no evidence_id"))
        elif item.evidence_id in seen_ids:
            v.append(Violation("V-3", f"duplicate evidence_id {item.evidence_id!r}"))
        else:
            seen_ids.add(item.evidence_id)

    # V-4 — a factual claim's value must be a typed scalar, never a
    # structure smuggled in as a "fact".
    for item in snapshot.evidence_items:
        if item.claim_type == "fact" and item.value is not None:
            if not isinstance(item.value, (int, float, bool, str)):
                v.append(Violation("V-4", f"evidence {item.evidence_id!r}: fact value has untyped shape {type(item.value).__name__}"))

    # V-5 — every non-SUPPORTED state carries its reason.
    for item in snapshot.evidence_items:
        if _enum_value(item.status) != EvidenceState.SUPPORTED.value:
            if not item.reason or not str(item.reason).strip():
                v.append(Violation("V-5", f"evidence {item.evidence_id!r}: status {_enum_value(item.status)} requires a reason"))

    # V-6 — no cross-scope mixing: item market must match; item horizon
    # must match or be explicitly horizon-agnostic.
    for item in snapshot.evidence_items:
        if item.market != scope.market:
            v.append(Violation("V-6", f"evidence {item.evidence_id!r}: market {item.market!r} differs from snapshot scope {scope.market!r}"))
        if item.horizon not in (scope.horizon, "not_applicable"):
            v.append(Violation("V-6", f"evidence {item.evidence_id!r}: horizon {item.horizon!r} differs from snapshot scope {scope.horizon!r}"))

    # V-7 — id/hash integrity: well-formed, and the stored hash must equal
    # the recomputed content hash (same canonical serialization the
    # builder used — the two share one function so they can never drift).
    if not snapshot.snapshot_id or not snapshot.snapshot_id.startswith(f"{LIVE_SNAPSHOT_TYPE}:"):
        v.append(Violation("V-7", f"malformed snapshot_id {snapshot.snapshot_id!r}"))
    if not snapshot.snapshot_hash or not _HASH_RE.match(snapshot.snapshot_hash):
        v.append(Violation("V-7", "snapshot_hash missing or not a sha256 hex digest"))
    else:
        recomputed = compute_snapshot_hash(snapshot)
        if recomputed != snapshot.snapshot_hash:
            v.append(Violation("V-7", "snapshot_hash does not match recomputed content hash"))

    # V-8 — user-specific context must never appear in a global live
    # snapshot (portfolio/paper-trade context arrives only via its own
    # future, separately authorized snapshot types).
    for path in _denylisted_keys(snapshot.consumer_context):
        v.append(Violation("V-8", f"user-specific key {path!r} present in a global live snapshot"))
    for item in snapshot.evidence_items:
        segments = re.split(r"[.\[\]]+", str(item.provenance_ref or ""))
        for segment in segments:
            if segment in USER_CONTEXT_DENYLIST:
                v.append(Violation("V-8", f"evidence {item.evidence_id!r}: provenance references user-specific field {segment!r}"))

    # V-9 — owner registry and the fixed owner→domain table.
    for item in snapshot.evidence_items:
        if item.owner not in OWNER_DOMAINS:
            v.append(Violation("V-9", f"evidence {item.evidence_id!r}: owner {item.owner!r} is not a registered owner"))
        elif item.domain not in OWNER_DOMAINS[item.owner]:
            v.append(Violation("V-9", f"evidence {item.evidence_id!r}: owner {item.owner!r} may not own domain {item.domain!r}"))

    # V-10 — owner_statuses must be consistent with the evidence items:
    # same owner set both ways; a SUPPORTED mapping needs at least one
    # SUPPORTED item; an owner whose items are all SUPPORTED cannot be
    # mapped to a non-SUPPORTED status.
    items_by_owner: dict[str, list[EvidenceItem]] = {}
    for item in snapshot.evidence_items:
        items_by_owner.setdefault(item.owner, []).append(item)
    mapped_owners = set(snapshot.availability.owner_statuses.keys())
    for owner in sorted(set(items_by_owner) - mapped_owners):
        v.append(Violation("V-10", f"owner {owner!r} has evidence items but no owner_statuses entry"))
    for owner in sorted(mapped_owners - set(items_by_owner)):
        v.append(Violation("V-10", f"owner {owner!r} mapped in owner_statuses but has no evidence items"))
    for owner, status in snapshot.availability.owner_statuses.items():
        owned = items_by_owner.get(owner)
        if not owned:
            continue
        statuses = {_enum_value(i.status) for i in owned}
        if status == EvidenceState.SUPPORTED.value and EvidenceState.SUPPORTED.value not in statuses:
            v.append(Violation("V-10", f"owner {owner!r} mapped SUPPORTED but has no SUPPORTED evidence item"))
        if status != EvidenceState.SUPPORTED.value and statuses == {EvidenceState.SUPPORTED.value}:
            v.append(Violation("V-10", f"owner {owner!r} mapped {status!r} but every evidence item is SUPPORTED"))

    return ValidationResult(valid=not v, violations=tuple(v))
