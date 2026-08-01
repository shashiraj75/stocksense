"""
Trade Postmortem Sprint 3A, Stage J4C — governed touch and order
conclusions.

Maps a raw J4B ObservedNumericalCrossingSummary (and the two
independent J4C level-history/price-basis eligibility inputs) onto a
GOVERNED conclusion. This is the conservative layer required by Gate
1H/1I: a numerical crossing is never, by itself, described as "the
configured level was hit" unless the specific level's history is
reliably known to be unmodified since entry (or, for MODIFIED_AFTER_
ENTRY levels, unless temporal level history proves the value was
active on that bar — Wave A never has that proof, so a crossing of a
modified level always falls back).

Pure, deterministic, no I/O. Produces no free-text reasoning as a
source of truth — every conclusion is a stable enum value; `detail` is
descriptive only and, for every fallback conclusion, is set to exactly
the canonical sentence below (Gate 1K) so a caller can rely on
string-equality rather than natural-language parsing.
"""
from dataclasses import dataclass

from services.postmortem.price_path_calculator import (
    NumericalLevelCrossingObservation,
    ObservedNumericalCrossingSummary,
    SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED,
    SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR,
    SAFE_PATTERN_TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR,
    SAFE_PATTERN_STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR,
)
from services.postmortem.level_history_eligibility import (
    ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING,
    ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES,
    ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES,
    ENDPOINT_EVIDENCE_MALFORMED_VALUE,
    ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS,
    ENDPOINT_EVIDENCE_NULL_TO_VALUE,
    ENDPOINT_EVIDENCE_VALUE_TO_NULL,
    LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED,
    PRICE_BASIS_COMPATIBLE,
    classify_endpoint_evidence,
)

# Gate 1K — the ONE canonical fallback sentence. Never paraphrased.
CANONICAL_FALLBACK = "Insufficient evidence to determine this factor reliably."


class GovernedConclusionContractError(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


INVALID_GOVERNED_INPUT = "INVALID_GOVERNED_INPUT"

# --- Governed touch conclusions (Gate 1H) ---
GOVERNED_TOUCH_NO_VALUE_SUPPLIED = "NO_VALUE_SUPPLIED"
GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING = "NO_COMPATIBLE_CROSSING"
GOVERNED_TOUCH_SUPPORTED = "SUPPORTED_TOUCH"
GOVERNED_TOUCH_INCOMPATIBLE_BASIS = "INCOMPATIBLE_BASIS"
GOVERNED_TOUCH_NO_BARS = "NO_BARS"
GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE = "CONTRADICTORY_ENDPOINT_EVIDENCE"

ALL_GOVERNED_TOUCH_STATUSES = frozenset({
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED, GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING, GOVERNED_TOUCH_SUPPORTED,
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS, GOVERNED_TOUCH_NO_BARS, GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
})

# Endpoint statuses that already mean "we cannot even classify the
# level's endpoint evidence" — always fall back regardless of level
# history (Gate 1F correction, items 1-4).
_ENDPOINT_EVIDENCE_UNUSABLE = frozenset({
    ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING, ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING, ENDPOINT_EVIDENCE_MALFORMED_VALUE,
})
# Endpoint statuses that, for a GOVERNED_UNMODIFIED level, are logically
# impossible — the flag says "never changed" but the endpoints
# themselves show a difference (items 5-7).
_ENDPOINT_EVIDENCE_CONTRADICTS_UNMODIFIED = frozenset({
    ENDPOINT_EVIDENCE_NULL_TO_VALUE, ENDPOINT_EVIDENCE_VALUE_TO_NULL, ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES,
})


@dataclass(frozen=True)
class GovernedLevelTouchConclusion:
    level_kind: str
    status: str
    detail: str

    def __post_init__(self):
        if self.status not in ALL_GOVERNED_TOUCH_STATUSES:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "status is not a recognized governed touch conclusion")
        if type(self.detail) is not str or not self.detail.strip():
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "detail must be a non-empty string")
        fallback_statuses = frozenset({
            GOVERNED_TOUCH_INCOMPATIBLE_BASIS, GOVERNED_TOUCH_NO_BARS, GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
            GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
        })
        if self.status in fallback_statuses and self.detail != CANONICAL_FALLBACK:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "fallback conclusions must use the exact canonical fallback sentence")


def _endpoint_consistency_status(
    *, level_history_status: str, endpoint_status: str, observed_value: float | None, entry_value, exit_value,
):
    """Returns None when endpoint evidence is consistent enough to
    proceed to the safe-attribution check; otherwise returns the
    GOVERNED_TOUCH_* fallback status that applies. Gate 1F correction,
    items 1-9."""
    if endpoint_status in _ENDPOINT_EVIDENCE_UNUSABLE:
        return GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE

    if level_history_status != LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED:
        # Endpoint evidence is only cross-checked against a GOVERNED_
        # UNMODIFIED level (the only status that claims the level never
        # changed) — legacy/modified/contradictory already fall back
        # for their own reason, checked by the caller.
        return None

    if endpoint_status in _ENDPOINT_EVIDENCE_CONTRADICTS_UNMODIFIED:
        # The flag says "never changed" but the endpoints disagree with
        # each other — a logically impossible governed state (items 5-7).
        return GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE

    if endpoint_status == ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS:
        # No level was configured throughout — cannot support a touch of
        # a DIFFERENT supplied numerical value (item 9).
        return GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE

    if endpoint_status == ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES:
        # Supported only when the observed/supplied crossing value is
        # consistent with that (unchanging) endpoint value (item 8).
        if observed_value is None or entry_value is None or observed_value != entry_value:
            return GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE
        return None

    return None


def classify_governed_level_touch(
    *, level_kind: str, observation: NumericalLevelCrossingObservation, level_history_status: str,
    price_basis_status: str, bars_available: bool,
    entry_snapshot_present: bool, exit_snapshot_present: bool, entry_value=None, exit_value=None,
) -> GovernedLevelTouchConclusion:
    if not isinstance(observation, NumericalLevelCrossingObservation):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "observation must be a NumericalLevelCrossingObservation")
    if not isinstance(bars_available, bool):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "bars_available must be an exact bool")
    if not isinstance(entry_snapshot_present, bool) or not isinstance(exit_snapshot_present, bool):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "entry_snapshot_present/exit_snapshot_present must be exact bool")

    if not bars_available:
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_NO_BARS, detail=CANONICAL_FALLBACK)

    if price_basis_status != PRICE_BASIS_COMPATIBLE:
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_INCOMPATIBLE_BASIS, detail=CANONICAL_FALLBACK)

    if not observation.value_supplied:
        return GovernedLevelTouchConclusion(
            level_kind=level_kind, status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="no numerical level value was supplied for this factor",
        )

    if not observation.crossed_anywhere:
        return GovernedLevelTouchConclusion(
            level_kind=level_kind, status=GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
            detail="no compatible crossing of the supplied value was observed in the acquired evidence window",
        )

    # Gate 1F correction — endpoint evidence is now consulted explicitly,
    # BEFORE the safe-attribution check, for every crossing-observed
    # case (not only GOVERNED_UNMODIFIED — a missing/malformed snapshot
    # always falls back regardless of level history, items 1-4).
    endpoint_status = classify_endpoint_evidence(
        entry_snapshot_present=entry_snapshot_present, exit_snapshot_present=exit_snapshot_present,
        entry_value=entry_value, exit_value=exit_value,
    )
    endpoint_fallback = _endpoint_consistency_status(
        level_history_status=level_history_status, endpoint_status=endpoint_status,
        observed_value=observation.supplied_level_value, entry_value=entry_value, exit_value=exit_value,
    )
    if endpoint_fallback is not None:
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=endpoint_fallback, detail=CANONICAL_FALLBACK)

    if level_history_status == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED and observation.first_safely_attributable_session is not None:
        return GovernedLevelTouchConclusion(
            level_kind=level_kind, status=GOVERNED_TOUCH_SUPPORTED,
            detail="crossing observed on a safely attributable bar for a level reliably unmodified since entry, "
                   "consistent with the entry/exit endpoint evidence",
        )

    # Crossing observed but either the level's history cannot be
    # trusted (UNKNOWN_OR_LEGACY, MODIFIED_AFTER_ENTRY, CONTRADICTORY,
    # REQUIRED_EVIDENCE_MISSING) or only partial-boundary evidence
    # exists — always fall back rather than fabricate a positive claim.
    return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE, detail=CANONICAL_FALLBACK)


# --- Governed order conclusions (Gate 1I) ---
GOVERNED_ORDER_NEITHER_OBSERVED = "NEITHER_OBSERVED"
GOVERNED_ORDER_TARGET_ONLY_OBSERVED = "TARGET_ONLY_OBSERVED"
GOVERNED_ORDER_STOP_ONLY_OBSERVED = "STOP_ONLY_OBSERVED"
GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP = "TARGET_SAFELY_BEFORE_STOP"
GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET = "STOP_SAFELY_BEFORE_TARGET"
GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN = "SAME_BAR_ORDER_UNKNOWN"
GOVERNED_ORDER_UNAVAILABLE = "ORDER_UNAVAILABLE"

ALL_GOVERNED_ORDER_STATUSES = frozenset({
    GOVERNED_ORDER_NEITHER_OBSERVED, GOVERNED_ORDER_TARGET_ONLY_OBSERVED, GOVERNED_ORDER_STOP_ONLY_OBSERVED,
    GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP, GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
    GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN, GOVERNED_ORDER_UNAVAILABLE,
})


@dataclass(frozen=True)
class GovernedOrderConclusion:
    status: str
    detail: str

    def __post_init__(self):
        if self.status not in ALL_GOVERNED_ORDER_STATUSES:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "status is not a recognized governed order conclusion")
        if type(self.detail) is not str or not self.detail.strip():
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "detail must be a non-empty string")
        if self.status == GOVERNED_ORDER_UNAVAILABLE and self.detail != CANONICAL_FALLBACK:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "ORDER_UNAVAILABLE must use the exact canonical fallback sentence")


def classify_governed_order(
    *, summary: ObservedNumericalCrossingSummary, target_level_history_status: str, stop_level_history_status: str,
    target_price_basis_status: str, stop_price_basis_status: str, target_bars_available: bool, stop_bars_available: bool,
    target_entry_snapshot_present: bool = True, target_exit_snapshot_present: bool = True,
    target_entry_value=None, target_exit_value=None,
    stop_entry_snapshot_present: bool = True, stop_exit_snapshot_present: bool = True,
    stop_entry_value=None, stop_exit_value=None,
) -> GovernedOrderConclusion:
    if not isinstance(summary, ObservedNumericalCrossingSummary):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "summary must be an ObservedNumericalCrossingSummary")
    if not isinstance(target_bars_available, bool) or not isinstance(stop_bars_available, bool):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "bars_available flags must be exact bool")

    if not target_bars_available or not stop_bars_available:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)
    if target_price_basis_status != PRICE_BASIS_COMPATIBLE or stop_price_basis_status != PRICE_BASIS_COMPATIBLE:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    # Gate 1F correction — each level's endpoint evidence must
    # independently satisfy active-level eligibility before an ordered
    # (target-before-stop / stop-before-target) conclusion is permitted;
    # missing or contradictory evidence for EITHER level makes order
    # unavailable, checked below only for the ordered-pattern branch
    # (same-bar/neither/single-sided conclusions don't depend on it).
    target_endpoint_status = classify_endpoint_evidence(
        entry_snapshot_present=target_entry_snapshot_present, exit_snapshot_present=target_exit_snapshot_present,
        entry_value=target_entry_value, exit_value=target_exit_value,
    )
    stop_endpoint_status = classify_endpoint_evidence(
        entry_snapshot_present=stop_entry_snapshot_present, exit_snapshot_present=stop_exit_snapshot_present,
        entry_value=stop_entry_value, exit_value=stop_exit_value,
    )
    target_endpoint_fallback = _endpoint_consistency_status(
        level_history_status=target_level_history_status, endpoint_status=target_endpoint_status,
        observed_value=summary.target_observation.supplied_level_value,
        entry_value=target_entry_value, exit_value=target_exit_value,
    )
    stop_endpoint_fallback = _endpoint_consistency_status(
        level_history_status=stop_level_history_status, endpoint_status=stop_endpoint_status,
        observed_value=summary.stop_observation.supplied_level_value,
        entry_value=stop_entry_value, exit_value=stop_exit_value,
    )

    pattern = summary.safely_attributable_pattern

    if pattern == SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_NEITHER_OBSERVED, detail="no numerical level value was supplied for either factor")

    if pattern == SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE:
        if summary.partial_boundary_observation_present:
            # Only partial-boundary evidence exists — retained as an
            # observation, but it must never become a definitive order
            # (Gate 1I "partial-boundary evidence").
            return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)
        return GovernedOrderConclusion(status=GOVERNED_ORDER_NEITHER_OBSERVED, detail="no compatible crossing of either supplied value was safely observed")

    if pattern == SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_TARGET_ONLY_OBSERVED, detail="only the target value was safely observed to cross")

    if pattern == SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_STOP_ONLY_OBSERVED, detail="only the stop value was safely observed to cross")

    if pattern == SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR:
        # Never infer target-first or stop-first from candle direction
        # or from the mere presence of both observations on one bar
        # (Gate 1I).
        return GovernedOrderConclusion(
            status=GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
            detail="both values were safely observed to cross within the same OHLC bar — intrabar order is unknown",
        )

    # Ordered pattern (target-before-stop or stop-before-target). A
    # governed positive order conclusion is only supported when BOTH
    # levels are reliably known to have been unmodified from entry
    # through that observed bar — otherwise the historically active
    # value on that bar cannot be trusted, so order falls back.
    both_governed_unmodified = (
        target_level_history_status == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED
        and stop_level_history_status == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED
    )
    if not both_governed_unmodified:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    # Gate 1F correction — even with both levels GOVERNED_UNMODIFIED,
    # contradictory or unusable endpoint evidence for EITHER level still
    # makes the order conclusion unavailable.
    if target_endpoint_fallback is not None or stop_endpoint_fallback is not None:
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    if pattern == SAFE_PATTERN_TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR:
        return GovernedOrderConclusion(
            status=GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP,
            detail="target value safely observed to cross on an earlier bar than the stop value, both levels reliably unmodified since entry",
        )
    return GovernedOrderConclusion(
        status=GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
        detail="stop value safely observed to cross on an earlier bar than the target value, both levels reliably unmodified since entry",
    )
