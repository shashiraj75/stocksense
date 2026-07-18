"""
DP-025 (governed by DPD-009, DECIDED — DISCLOSURE HOLD) — typed,
JSON-serializable contracts for the pipeline-replay foundation.

SCOPE (corrected wording — see the 2026-07-18 hardening follow-up): this
package is a post-prediction ranking -> eligibility -> issuer
deduplication -> selection -> portfolio-allocation replay foundation. It
does NOT replay production's composite-score or confidence generation.
`CandidateSnapshot` is the already-computed Phase 1-2 OUTPUT (signal,
confidence, technical/fundamental/sentiment/quality scores, reasoning) —
those values are SUPPLIED by the caller, not recomputed. Composite
generation, confidence generation and later confidence adjustments,
target-price computation, and stop-loss/trade-level computation are all
supplied inputs, never replayed by this module. Phase 1-2 point-in-time
production replay (calling the real `PredictionEngine.predict()` /
`_predict_stock()` against historical, point-in-time market data) remains
future work — DP-025 is not fully resolved by this foundation.

This module defines the INPUT (an explicitly supplied, frozen-at-source
historical cross-sectional snapshot) and OUTPUT (a structured replay
result) shapes for `pipeline_replay.replay_snapshot()`. No network, no
persistence, no production side effects — see pipeline_replay.py's module
docstring for the full side-effect prohibition list this package obeys.

This is a foundation, not a completed validation methodology. It does NOT
compute or claim hit rate, return, accuracy, or profitability (that is
explicitly out of scope — see DP-026 through DP-029, all still open).
`ReplayResult.full_pipeline_replay` and `ReplayResult.production_equivalent`
are always `False` for every result this module produces, in every mode,
including a fully successful STRICT_POINT_IN_TIME completion — strict-mode
completion means the supplied snapshot passed this harness's own
DECLARED-integrity requirements, not that historical accuracy or
production equivalence has been independently established.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
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
    # quality_score is the RANKING-COMPATIBLE value — exactly what production
    # passes to _zscore_and_rank(). Production's real contract
    # (_predict_stock(), daily_picks.py): `"quality_score": qf.get("score")
    # if qf.get("score") is not None else 50` — i.e. when genuine quality
    # evidence is unavailable, quality_score is NOT None, it is the neutral
    # fallback 50. quality_score alone therefore cannot distinguish a
    # genuine score of 50 from a fabricated fallback of 50 — see
    # quality_raw_score below, which is what makes that distinction.
    quality_score: float | None
    # None means genuinely missing evidence (production's real contract —
    # see DP-025 test #4). Never coalesce to a numeric default when
    # constructing this field.
    sentiment_score: float | None
    sentiment_available: bool
    quality_available: bool
    # quality_raw_score: the GENUINE pre-fallback quality source value —
    # production's `_quality_raw` / `quality_raw_score` field. None means
    # quality evidence was genuinely unavailable (matches
    # quality_available=False). A genuine numeric 0 is valid evidence and
    # MUST be preserved exactly — never truthiness-coalesced to anything
    # else (this is the DP-009/DP-010 zero-preservation contract applied to
    # the raw provenance field itself). When quality is available,
    # quality_score MUST equal quality_raw_score exactly (production sets
    # score=raw in that case) — see contracts.validate_structure().
    quality_raw_score: float | None = None
    # True unless this candidate was loaded from a JSON fixture that
    # omitted `quality_raw_score` entirely (a pre-2026-07-18 fixture,
    # before this field existed) — see _candidate_from_dict(). When False,
    # quality PROVENANCE IS UNKNOWN: the fixture asserts a quality_score
    # but this module cannot tell whether it is genuine evidence or a
    # neutral fallback. STRICT mode refuses on this (see
    # pipeline_replay._assess_strict_integrity()); DIAGNOSTIC mode proceeds
    # but labels the result with an explicit "unknown quality provenance"
    # warning and never claims production equivalence. Never silently
    # treated as if provenance were known.
    quality_raw_score_provided: bool = True
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
    """One explicitly supplied, frozen-at-source historical cross-sectional
    snapshot — the sole input to replay_snapshot(). "Frozen-at-source"
    describes intent, not a runtime guarantee: this is a plain Python
    dataclass with mutable list/dict fields, and the CLI itself overwrites
    `replay_mode` after loading (see pipeline_replay.py's main()) — nothing
    here is enforced as immutable by the language or this module. Nothing
    in this object is fetched; it must be supplied in full by the caller (a
    fixture file, a future point-in-time export — DP-026 — or a test)."""

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


# Machine-readable stage-coverage contract (2026-07-18 hardening
# follow-up). These constants are the authoritative statement of what this
# foundation does and does not replay — the same list on every result,
# regardless of snapshot content, because it describes this foundation's
# fixed design scope, not per-call execution. Never edit these to make a
# result LOOK more complete than it is; if the harness's actual scope ever
# grows, move an item from SUPPLIED_NOT_REPLAYED to ACTUALLY_REPLAYED only
# once the corresponding production function is genuinely invoked.
STAGES_SUPPLIED_NOT_REPLAYED: tuple[str, ...] = (
    "universe_construction",
    "market_data_acquisition",
    "prediction_engine_composite_calculation",
    "signal_generation",
    "confidence_generation_and_adjustments",
    "target_price_calculation",
    "stop_loss_trade_level_calculation",
)

# The full set of stages this foundation is CAPABLE of replaying when a
# replay actually completes (status COMPLETED or DIAGNOSTIC_COMPLETED). A
# REFUSED_INCOMPLETE result replays none of these — see pipeline_replay.py.
STAGES_REPLAYABLE_ON_COMPLETION: tuple[str, ...] = (
    "cross_sectional_zscore_ranking",
    "quality_eligibility",
    "issuer_deduplication",
    "final_horizon_selection",
    "portfolio_allocation",
    "cash_unallocated_calculation",
)


#: quality_provenance values — see RankedCandidateResult.quality_provenance.
QUALITY_PROVENANCE_GENUINE = "genuine"          # quality_available=True, raw score confirmed
QUALITY_PROVENANCE_FALLBACK_NEUTRAL = "fallback_neutral"  # quality_available=False, score=50 fallback
QUALITY_PROVENANCE_UNKNOWN = "unknown"          # quality_raw_score_provided=False (old fixture)


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
    # Quality-provenance fix (2026-07-18): distinguishes genuine quality
    # evidence from the neutral quality_score=50 fallback (or, for a
    # pre-existing fixture, an unknown relationship) — one of
    # QUALITY_PROVENANCE_GENUINE / _FALLBACK_NEUTRAL / _UNKNOWN. quality_score
    # itself (visible via factor_zscores' inputs, not duplicated here) was
    # still the value actually passed to the real _zscore_and_rank() —
    # this field is provenance metadata only, never a ranking input.
    quality_provenance: str = QUALITY_PROVENANCE_UNKNOWN


@dataclass
class ReplayResult:
    status: ReplayStatus
    replay_mode: ReplayMode
    snapshot_id: str
    as_of: str
    market: str
    horizon: str
    # Truthful, per-result dotted-path provenance: {"invoked": {...},
    # "not_invoked": {...}}. A function only appears under "invoked" if
    # this specific replay actually called it — e.g. optimizer.optimize()
    # is listed under "not_invoked" only for a 0-candidate slate (DP-021:
    # _compute_portfolio_allocation() invokes the optimizer for any
    # non-empty slate, N>=1 — the prior N==1 short-circuit was removed).
    production_provenance: dict[str, dict[str, str]]
    input_integrity: dict[str, str]
    # Machine-readable stage-coverage contract — see
    # STAGES_SUPPLIED_NOT_REPLAYED / STAGES_REPLAYABLE_ON_COMPLETION above.
    # stages_actually_replayed is [] for a REFUSED_INCOMPLETE result (no
    # stage ran) and the full STAGES_REPLAYABLE_ON_COMPLETION list for any
    # completed result — this foundation always runs every stage it's
    # capable of once it proceeds past intake validation.
    stages_supplied_not_replayed: list[str] = field(default_factory=lambda: list(STAGES_SUPPLIED_NOT_REPLAYED))
    stages_actually_replayed: list[str] = field(default_factory=list)
    # Fixed False on EVERY result this module ever produces, in every mode
    # — including a fully successful STRICT_POINT_IN_TIME completion. Never
    # set to True anywhere in this package. See module docstring.
    full_pipeline_replay: bool = False
    production_equivalent: bool = False
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
    """Load one CandidateSnapshot from a JSON-decoded dict. Backward
    compatibility for fixtures predating `quality_raw_score` (2026-07-18):
    the field's PRESENCE in the dict (not its value) determines
    `quality_raw_score_provided` — a fixture that explicitly writes
    `"quality_raw_score": null` correctly declares "confirmed unavailable",
    while a fixture that omits the key entirely is marked
    `quality_raw_score_provided=False` ("unknown provenance"), never
    silently inferred as either genuine or fallback."""
    d = dict(d)
    pit = d.pop("point_in_time_status", {}) or {}
    quality_raw_score_provided = "quality_raw_score" in d
    return CandidateSnapshot(
        point_in_time_status={k: PointInTimeIntegrity(v) for k, v in pit.items()},
        quality_raw_score_provided=quality_raw_score_provided,
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


def _is_finite_number(x) -> bool:
    """True for a real, finite int/float — explicitly False for bool
    (a bool is technically an int subclass in Python but is never a valid
    score value here), NaN, or +-inf."""
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(x)


def validate_structure(snapshot: MarketSnapshot) -> list[str]:
    """
    Deterministic, no-network structural validation (2026-07-18 hardening
    follow-up). These are baseline well-formedness checks, distinct from
    the STRICT-only point-in-time integrity checks in
    pipeline_replay._assess_strict_integrity() — a structurally malformed
    snapshot is unsafe to replay under ANY mode, so every one of these
    rules applies identically in STRICT_POINT_IN_TIME and DIAGNOSTIC. This
    function performs no network-based verification of any kind — it only
    inspects the values already present on the snapshot object.

    Returns a list of structured error strings; empty means the snapshot
    is structurally well-formed enough to attempt a replay.
    """
    errors: list[str] = []

    # 1. market must be IN or US
    if snapshot.market not in ("IN", "US"):
        errors.append(f"market {snapshot.market!r} is not one of ('IN', 'US')")

    # 2. horizon must be short/medium/long
    if snapshot.horizon not in ("short", "medium", "long"):
        errors.append(f"horizon {snapshot.horizon!r} is not one of ('short', 'medium', 'long')")

    # 3 & 4. regime_id must exist in the production regime-label map, and
    # regime_label must match what production would assign that ID.
    try:
        from services.alpha_engine.regime_cluster import REGIME_LABELS
        if snapshot.regime_id not in REGIME_LABELS:
            errors.append(
                f"regime_id {snapshot.regime_id!r} is not in production's REGIME_LABELS "
                f"(valid ids: {sorted(REGIME_LABELS)})"
            )
        elif REGIME_LABELS[snapshot.regime_id] != snapshot.regime_label:
            errors.append(
                f"regime_label {snapshot.regime_label!r} does not match production's label "
                f"for regime_id {snapshot.regime_id!r} ({REGIME_LABELS[snapshot.regime_id]!r})"
            )
    except ImportError:
        errors.append("could not import services.alpha_engine.regime_cluster.REGIME_LABELS to validate regime_id/regime_label")

    # 5. as_of must parse as an ISO-8601 timestamp
    try:
        if not snapshot.as_of:
            raise ValueError("empty")
        datetime.fromisoformat(snapshot.as_of.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        errors.append(f"as_of {snapshot.as_of!r} does not parse as an ISO-8601 timestamp")

    seen_symbols: set[str] = set()
    for c in snapshot.candidates:
        # 6. symbol must be non-empty
        if not c.symbol or not c.symbol.strip():
            errors.append("a candidate has an empty or missing symbol")
            continue

        # 7. candidate symbols must be unique
        if c.symbol in seen_symbols:
            errors.append(f"duplicate candidate symbol {c.symbol!r} — symbols must be unique within a snapshot")
        seen_symbols.add(c.symbol)

        # 8. sentiment_available=False must not silently carry a numeric
        # sentiment score — that is a contradiction, not evidence.
        if not c.sentiment_available and c.sentiment_score is not None:
            errors.append(
                f"candidate {c.symbol!r}: sentiment_available=False but sentiment_score="
                f"{c.sentiment_score!r} is not None (contradictory — production's contract "
                f"requires a numeric score only when data is genuinely available)"
            )
        if c.sentiment_available and c.sentiment_score is None:
            errors.append(f"candidate {c.symbol!r}: sentiment_available=True but sentiment_score is None")

        # 9. Quality provenance — CORRECTED (2026-07-18 quality-provenance
        # fix) to match the exact production contract from _predict_stock():
        #   "quality_score": qf.get("score") if qf.get("score") is not None else 50
        # i.e. quality_available=False does NOT mean quality_score is None
        # — it means quality_score is the neutral fallback 50, while
        # quality_raw_score (the genuine pre-fallback value) is None. The
        # OLD version of this rule incorrectly required quality_score to be
        # None when unavailable; that never matched production and has been
        # replaced below.
        #
        # If quality_raw_score_provided is False (a pre-2026-07-18 fixture
        # that never declared this field at all), these consistency checks
        # are skipped here — provenance is UNKNOWN, not necessarily
        # inconsistent, and STRICT-mode-only refusal for that specific
        # condition lives in pipeline_replay._assess_strict_integrity()
        # (mirroring how the other point-in-time-only categories work).
        if c.quality_raw_score_provided:
            if c.quality_available:
                if c.quality_raw_score is None:
                    errors.append(f"candidate {c.symbol!r}: quality_available=True but quality_raw_score is None")
                elif not _is_finite_number(c.quality_raw_score):
                    errors.append(f"candidate {c.symbol!r}: quality_raw_score={c.quality_raw_score!r} is not a finite numeric value")
                elif c.quality_score is None or not _is_finite_number(c.quality_score):
                    errors.append(f"candidate {c.symbol!r}: quality_available=True but quality_score={c.quality_score!r} is not a finite numeric value")
                elif not math.isclose(c.quality_score, c.quality_raw_score, rel_tol=1e-9, abs_tol=1e-9):
                    errors.append(
                        f"candidate {c.symbol!r}: quality_available=True requires quality_score == "
                        f"quality_raw_score (production sets score=raw when available); got "
                        f"quality_score={c.quality_score!r}, quality_raw_score={c.quality_raw_score!r}"
                    )
            else:
                if c.quality_raw_score is not None:
                    errors.append(
                        f"candidate {c.symbol!r}: quality_available=False but quality_raw_score="
                        f"{c.quality_raw_score!r} is not None (unavailable quality evidence must "
                        f"carry no raw value)"
                    )
                if c.quality_score is None or not _is_finite_number(c.quality_score) or not math.isclose(c.quality_score, 50.0, rel_tol=0, abs_tol=1e-9):
                    errors.append(
                        f"candidate {c.symbol!r}: quality_available=False requires quality_score == 50 "
                        f"(the neutral ranking fallback, never fabricated as genuine evidence); got "
                        f"quality_score={c.quality_score!r}"
                    )

        # 10. score fields must be finite numeric values or explicit None
        for field_name, value in (
            ("confidence", c.confidence),
            ("technical_score", c.technical_score),
            ("fundamental_score", c.fundamental_score),
            ("quality_score", c.quality_score),
        ):
            if value is not None and not _is_finite_number(value):
                errors.append(f"candidate {c.symbol!r}: {field_name}={value!r} is not a finite numeric value or None")
        if c.sentiment_score is not None and not _is_finite_number(c.sentiment_score):
            errors.append(f"candidate {c.symbol!r}: sentiment_score={c.sentiment_score!r} is not a finite numeric value or None")

    # 11. returns series must contain only finite numeric values when
    # supplied. Inconsistent lengths across symbols is NOT a hard
    # structural error here (optimizer replay already degrades gracefully
    # to no covariance matrix in that case) — pipeline_replay.py surfaces
    # that specific condition as an explicit warning instead, per the
    # single documented rule this module follows for that case.
    if snapshot.returns_by_symbol:
        for symbol, series in snapshot.returns_by_symbol.items():
            for value in series:
                if not _is_finite_number(value):
                    errors.append(f"returns_by_symbol[{symbol!r}] contains a non-finite value: {value!r}")
                    break

    return errors
