"""
DP-025 (governed by DPD-009, DECIDED — DISCLOSURE HOLD) — typed,
JSON-serializable contracts for the pipeline-replay foundation.

This module defines the INPUT (a frozen historical cross-sectional
snapshot) and OUTPUT (a structured replay result) shapes for
`pipeline_replay.replay_snapshot()`. No network, no persistence, no
production side effects — see pipeline_replay.py's module docstring for
the full side-effect prohibition list this package obeys.

This is a foundation, not a completed validation methodology. It does NOT
compute or claim hit rate, return, accuracy, or profitability (that is
explicitly out of scope — see DP-026 through DP-029, all still open). It
proves only that production's composite -> confidence -> ranking ->
selection -> portfolio-allocation logic can be replayed deterministically
against a supplied, honestly-labelled snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum


class ReplayMode(str, Enum):
    """STRICT_POINT_IN_TIME is the default and the only mode whose output
    may ever be treated as evidence of anything. DIAGNOSTIC exists purely
    for engineering comparison against synthetic/incomplete fixtures and
    every diagnostic result is labelled as such."""
    STRICT_POINT_IN_TIME = "strict_point_in_time"
    DIAGNOSTIC = "diagnostic"


class PointInTimeIntegrity(str, Enum):
    """Per-category input-integrity classification. STRICT mode requires
    POINT_IN_TIME for every mandatory category; anything else causes a
    structured refusal in STRICT mode."""
    POINT_IN_TIME = "point_in_time"      # verified historical, as-of the snapshot date
    SYNTHETIC = "synthetic"              # fabricated/engineering fixture data
    CURRENT_DAY_MASQUERADING = "current_day_masquerading_as_historical"
    MISSING = "missing"
    UNKNOWN_PROVENANCE = "unknown_provenance"


class ReplayStatus(str, Enum):
    COMPLETED = "completed"
    DIAGNOSTIC_COMPLETED = "diagnostic_completed"
    REFUSED_INCOMPLETE = "refused_incomplete"


# Mandatory-for-STRICT-mode input-integrity categories. Every one of these
# must be PointInTimeIntegrity.POINT_IN_TIME (snapshot-level, in
# `MarketSnapshot.point_in_time_integrity`) for strict replay to proceed.
STRICT_MODE_REQUIRED_CATEGORIES: tuple[str, ...] = (
    "provenance",
    "timestamp",
    "technical",
    "fundamental",
    "quality",
    # sentiment is deliberately excluded here: production's own missing-
    # evidence contract already treats an absent sentiment reading as
    # legitimate (z = 0.0, never fabricated) — see CandidateSnapshot's
    # sentiment_score/sentiment_available fields and DP-025's test #4.
)


@dataclass
class CandidateSnapshot:
    """One historical candidate row — the frozen, already-computed output
    of production's Phase 1-2 (PredictionEngine.predict() /
    _predict_stock()), which this foundation does NOT re-run (that would
    require live network/news access). Field names deliberately mirror the
    dict keys `_zscore_and_rank()` and `_passes_quality_gate()` already
    expect, so no field gets silently dropped or renamed in translation."""

    symbol: str
    signal: str  # "BUY" | "HOLD" | "SELL" | "REJECTED"
    confidence: float | None
    technical_score: float | None
    fundamental_score: float | None
    quality_score: float | None
    # None means genuinely missing evidence (production's real contract —
    # see DP-025 test #4). Never coalesce to a numeric default when
    # constructing this field.
    sentiment_score: float | None
    sentiment_available: bool
    quality_available: bool
    # Indicator/reason items required by _passes_quality_gate — same shape
    # PredictionEngine's `reasoning` list already uses:
    # [{"indicator": "Risk/Reward", "reason": "...", ...}, ...]
    reasoning: list[dict] = field(default_factory=list)
    cap_tier: str | None = None  # "large" | "mid" | "small" | None
    # Informational only in this foundation — NOT wired into
    # _deduplicate_by_issuer, which always uses the real, hardcoded
    # _US_ISSUER_GROUP mapping to guarantee production-equivalence. See
    # pipeline_replay.py's module docstring for why.
    issuer_group_hint: str | None = None
    price: float | None = None
    target: float | None = None
    stop_loss: float | None = None
    # Per-candidate point-in-time integrity, keyed by category
    # ("technical", "fundamental", "sentiment", "quality"). Only consulted
    # when the snapshot-level category isn't already POINT_IN_TIME.
    point_in_time_status: dict[str, PointInTimeIntegrity] = field(default_factory=dict)


@dataclass
class MarketSnapshot:
    """One immutable, historical cross-sectional snapshot — the sole input
    to replay_snapshot(). Nothing in this object is fetched; it must be
    supplied in full by the caller (a fixture file, a future point-in-time
    export — DP-026 — or a test)."""

    snapshot_id: str
    as_of: str  # ISO-8601 timestamp of the historical decision point
    market: str  # "IN" | "US"
    horizon: str  # "short" | "medium" | "long"
    source: str  # provenance description, e.g. "manual_fixture", "historical_export_v0"
    replay_mode: ReplayMode
    candidates: list[CandidateSnapshot]
    regime_id: int
    regime_label: str
    regime_weight_multipliers: dict[str, float]
    ic_weights: dict[str, float]
    # Optional: per-symbol daily return series for optimizer replay. All
    # series must be the same length (T). Symbols not selected into the
    # final slate are simply unused; if any SELECTED symbol is missing
    # from this dict, the optimizer replay falls back to None (identity
    # covariance) for that horizon's allocation, mirroring
    # optimizer.optimize()'s own graceful degradation.
    returns_by_symbol: dict[str, list[float]] | None = None
    # Snapshot-level point-in-time integrity, keyed by category (see
    # STRICT_MODE_REQUIRED_CATEGORIES for the mandatory set). Categories not
    # present default to PointInTimeIntegrity.UNKNOWN_PROVENANCE.
    point_in_time_integrity: dict[str, PointInTimeIntegrity] = field(default_factory=dict)
    # Explicit synthetic/non-point-in-time flags — free-form, human-readable
    # keys the snapshot author uses to self-disclose known limitations
    # (e.g. {"fundamentals_are_current_day_not_historical": True}). Purely
    # additive disclosure; STRICT-mode refusal is driven by
    # point_in_time_integrity, not by this dict.
    synthetic_flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class RejectedCandidate:
    symbol: str
    stage: str  # "signal_filter" | "quality_gate" | "issuer_dedup" | "final_selection"
    reason_code: str  # stable, machine-readable — see REJECTION_CODES below
    detail: str = ""


# Stable, machine-readable rejection reason codes — never a free-text
# message alone, so a downstream consumer can group/count without string
# matching.
REJECTION_NOT_BUY_SIGNAL = "NOT_BUY_SIGNAL"
REJECTION_CONFIDENCE_BELOW_FLOOR = "CONFIDENCE_BELOW_25"
REJECTION_RISK_REWARD_OR_GOVERNANCE = "RISK_REWARD_OR_GOVERNANCE_FLAG"
REJECTION_LIQUIDITY_DISTRESS = "LIQUIDITY_DISTRESS_FLAG"
REJECTION_SHORT_TERM_OVERBOUGHT = "SHORT_TERM_OVERBOUGHT"
REJECTION_ISSUER_DUPLICATE = "ISSUER_DUPLICATE"
REJECTION_NOT_SELECTED = "NOT_SELECTED_IN_FINAL_SLATE"


@dataclass
class RankedCandidateResult:
    symbol: str
    signal: str
    confidence: float | None
    ranking_alpha: float
    combined_alpha: float
    factor_zscores: dict[str, float]
    meta_alpha: float | None
    meta_alpha_used_for_ranking: bool


@dataclass
class ReplayResult:
    status: ReplayStatus
    replay_mode: ReplayMode
    snapshot_id: str
    as_of: str
    market: str
    horizon: str
    # Dotted-path provenance of every production function this replay
    # actually invoked — proof the harness called the real implementation,
    # not a reimplementation.
    production_provenance: dict[str, str]
    input_integrity: dict[str, str]
    missing_requirements: list[str] = field(default_factory=list)
    ranked_universe: list[RankedCandidateResult] = field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = field(default_factory=list)
    issuer_duplicates_suppressed: int = 0
    selected_slate: list[str] = field(default_factory=list)
    portfolio_weights: dict[str, float] = field(default_factory=dict)
    cash_unallocated_pct: float | None = None
    warnings: list[str] = field(default_factory=list)
    unresolved_methodology_limitations: list[str] = field(default_factory=list)
    # Explicit, always-False-in-this-foundation dependency flags — this
    # task creates a foundation only; none of DP-026-029 are resolved by
    # it, and this makes that machine-checkable rather than a claim buried
    # in prose.
    dp_dependency_resolved: dict[str, bool] = field(default_factory=lambda: {
        "DP-026_point_in_time_fundamentals": False,
        "DP-027_survivorship_bias": False,
        "DP-028_overlapping_windows": False,
        "DP-029_transaction_costs": False,
    })
    result_hash: str = ""

    def to_json_dict(self) -> dict:
        """Deterministic, JSON-serializable representation (enums -> their
        .value, dataclasses -> plain dicts). Does NOT include result_hash
        (compute that from this dict's own serialization instead)."""
        def _default(o):
            if isinstance(o, Enum):
                return o.value
            raise TypeError(f"not JSON-serializable: {type(o)!r}")
        d = asdict(self)
        d.pop("result_hash", None)
        # asdict() already recursed into nested dataclasses/enums as plain
        # values where possible; re-serialize through json to normalise
        # Enum members (asdict does not convert them) and guarantee the
        # dict is genuinely JSON-safe end to end.
        return json.loads(json.dumps(d, default=_default, sort_keys=True))

    def compute_hash(self) -> str:
        """Stable SHA-256 hex digest of the deterministic JSON
        representation (sorted keys) — same snapshot + same replay_mode
        must always produce the same hash. Used by determinism tests."""
        payload = json.dumps(self.to_json_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_from_dict(d: dict) -> CandidateSnapshot:
    d = dict(d)
    pit = d.pop("point_in_time_status", {}) or {}
    return CandidateSnapshot(
        point_in_time_status={k: PointInTimeIntegrity(v) for k, v in pit.items()},
        **d,
    )


def snapshot_from_dict(d: dict) -> MarketSnapshot:
    """Load a MarketSnapshot from a plain JSON-decoded dict — the shape a
    local fixture file or the CLI reads from disk. Never fetches anything;
    the caller supplies the full dict. Raises on structurally malformed
    input rather than silently defaulting a missing mandatory field."""
    d = dict(d)
    candidates = [_candidate_from_dict(c) for c in d.pop("candidates", [])]
    pit = d.pop("point_in_time_integrity", {}) or {}
    return MarketSnapshot(
        candidates=candidates,
        replay_mode=ReplayMode(d.pop("replay_mode", ReplayMode.STRICT_POINT_IN_TIME.value)),
        point_in_time_integrity={k: PointInTimeIntegrity(v) for k, v in pit.items()},
        **d,
    )
