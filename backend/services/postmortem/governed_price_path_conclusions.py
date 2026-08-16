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

Wave A closure correction (2nd pass) — classify_governed_order no
longer runs a second, weaker eligibility path of its own. Every
non-ORDER_UNAVAILABLE conclusion (including TARGET_ONLY_OBSERVED,
STOP_ONLY_OBSERVED, SAME_BAR_ORDER_UNKNOWN, and NEITHER_OBSERVED — not
only the two ordered patterns) is now gated on the SAME per-level
GovernedLevelTouchConclusion classify_governed_level_touch itself
produces for each side. There is exactly one source of truth for
"is this level's evidence trustworthy enough to say anything at all."

Wave A closure correction (3rd pass) — the 2nd pass's own single-
source claim was not yet fully honest: GOVERNED_TOUCH_NO_COMPATIBLE_
CROSSING could still be produced under ANY level-history status (the
crossed-anywhere check ran unconditionally, before the GOVERNED_
UNMODIFIED gate that only guarded SUPPORTED), and the order classifier
then reinterpreted the raw level_history_status a second time via
`_side_is_definitive_negative(touch, side_level_history_status)` to
decide whether that NO_COMPATIBLE_CROSSING was actually trustworthy —
a second eligibility path in every practical sense, even though it
never produced a different STATUS value. Corrected: NO_COMPATIBLE_
CROSSING is now itself the definitive-negative signal — classify_
governed_level_touch only returns it when the level history is
GOVERNED_UNMODIFIED; any other history state with no crossing returns
GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE instead. `_side_is_definitive_
negative`/`_side_is_trustworthy` now take ONLY a GovernedLevelTouch
Conclusion — no raw level-history, endpoint, basis, snapshot, or
bars-available input — so the order classifier truly cannot
reinterpret anything after the two per-level conclusions are built.

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
    STOP_VALUE,
    TARGET_VALUE,
    SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED,
    SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR,
    SAFE_PATTERN_TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR,
    SAFE_PATTERN_STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR,
)
from services.postmortem.level_history_eligibility import (
    ALL_LEVEL_HISTORY_STATUSES,
    ALL_PRICE_BASIS_STATUSES,
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
INVALID_LEVEL_KIND = "INVALID_LEVEL_KIND"

# Wave A closure correction — the canonical level-kind vocabulary is
# the ALREADY-established J4B repository vocabulary (price_path_
# calculator.TARGET_VALUE / STOP_VALUE), not a new invented pair — this
# is "already canonical repository vocabulary" per the governing
# instruction, so it is reused rather than redefined.
_VALID_LEVEL_KINDS = frozenset({TARGET_VALUE, STOP_VALUE})


def _safe_str_member(value, allowed: frozenset) -> bool:
    """Exact-str-and-membership test — never raises for an unhashable
    value, never treats a str subclass (even with a hostile __eq__) as
    a match. Mirrors price_path_calculator._safe_str_member exactly."""
    if type(value) is not str:
        return False
    try:
        return value in allowed
    except TypeError:
        return False


def _validate_level_kind(level_kind) -> None:
    if not _safe_str_member(level_kind, _VALID_LEVEL_KINDS):
        raise GovernedConclusionContractError(INVALID_LEVEL_KIND, f"level_kind must be one of {sorted(_VALID_LEVEL_KINDS)}")


def _validate_status_member(value, allowed: frozenset, field_name: str) -> None:
    if not _safe_str_member(value, allowed):
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, f"{field_name} is not a recognized value")


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

# Every touch status other than SUPPORTED_TOUCH is a fallback for the
# purposes of this module's own no-fabrication guarantee: none of them
# may be treated by the order classifier as "positive" evidence.
_GOVERNED_TOUCH_FALLBACK_STATUSES = frozenset({
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING, GOVERNED_TOUCH_INCOMPATIBLE_BASIS, GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE, GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
})
# Statuses whose canonical sentence must be the exact CANONICAL_FALLBACK.
_GOVERNED_TOUCH_CANONICAL_FALLBACK_STATUSES = frozenset({
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
        _validate_level_kind(self.level_kind)
        if self.status not in ALL_GOVERNED_TOUCH_STATUSES:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "status is not a recognized governed touch conclusion")
        if type(self.detail) is not str or not self.detail.strip():
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "detail must be a non-empty string")
        if self.status in _GOVERNED_TOUCH_CANONICAL_FALLBACK_STATUSES and self.detail != CANONICAL_FALLBACK:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "fallback conclusions must use the exact canonical fallback sentence")
        if self.status == GOVERNED_TOUCH_SUPPORTED and self.detail == CANONICAL_FALLBACK:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "a positive SUPPORTED_TOUCH must not use the canonical fallback sentence")

    @property
    def is_supported(self) -> bool:
        return self.status == GOVERNED_TOUCH_SUPPORTED


# Mandatory Correction 2 (single-source eligibility) — a per-level
# DEFINITIVE NEGATIVE is derivable entirely from the touch conclusion's
# own status; no raw level-history, endpoint, basis, snapshot, or
# bars-available input is consulted here. This is safe ONLY because
# classify_governed_level_touch (Mandatory Correction 1, above) already
# guarantees GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING is never returned
# unless the level history was GOVERNED_UNMODIFIED and every other
# eligibility check already passed — by the time a conclusion exists,
# its status alone fully encodes that fact. GOVERNED_TOUCH_NO_VALUE_
# SUPPLIED is a definitive negative unconditionally (nothing was ever
# configured for that side, so there is no historical validity in
# question at all). No other touch status — including NO_COMPATIBLE_
# CROSSING under unknown/legacy/modified/contradictory history, which
# now itself resolves to GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE — is ever
# a definitive negative.
def _side_is_definitive_negative(touch: GovernedLevelTouchConclusion) -> bool:
    return touch.status in (GOVERNED_TOUCH_NO_VALUE_SUPPLIED, GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING)


def _side_is_trustworthy(touch: GovernedLevelTouchConclusion) -> bool:
    return touch.is_supported or _side_is_definitive_negative(touch)


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
    """The single shared per-level eligibility function — classify_
    governed_order calls this exact function for BOTH the target and
    the stop side and treats its result as the sole source of truth for
    whether that side's evidence is trustworthy (Wave A closure
    correction, Mandatory Correction 1). There is no second, separate
    eligibility path anywhere in this module."""
    _validate_level_kind(level_kind)
    if type(observation) is not NumericalLevelCrossingObservation:
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "observation must be exactly a NumericalLevelCrossingObservation")
    if type(bars_available) is not bool:
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "bars_available must be an exact bool")
    if type(entry_snapshot_present) is not bool or type(exit_snapshot_present) is not bool:
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "entry_snapshot_present/exit_snapshot_present must be exact bool")
    _validate_status_member(level_history_status, ALL_LEVEL_HISTORY_STATUSES, "level_history_status")
    _validate_status_member(price_basis_status, ALL_PRICE_BASIS_STATUSES, "price_basis_status")

    if not bars_available:
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_NO_BARS, detail=CANONICAL_FALLBACK)

    if price_basis_status != PRICE_BASIS_COMPATIBLE:
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_INCOMPATIBLE_BASIS, detail=CANONICAL_FALLBACK)

    if not observation.value_supplied:
        return GovernedLevelTouchConclusion(
            level_kind=level_kind, status=GOVERNED_TOUCH_NO_VALUE_SUPPLIED, detail="no numerical level value was supplied for this factor",
        )

    # Wave A closure correction (2nd pass) — endpoint evidence is now
    # consulted BEFORE the crossed_anywhere check, not after it. A level
    # that never crosses is not automatically a trustworthy negative:
    # missing snapshots or a governed-unmodified level whose endpoints
    # contradict each other make that level's evidence untrustworthy
    # regardless of whether a crossing happened. Only NO_VALUE_SUPPLIED
    # (checked above) is exempt — with no supplied value at all, there
    # is nothing to endpoint-cross-check.
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

    if not observation.crossed_anywhere:
        # Mandatory Correction 1 (single-source eligibility) —
        # NO_COMPATIBLE_CROSSING is a DEFINITIVE governed negative: it
        # asserts the checked value was the level's one true value for
        # the whole evidence window and it genuinely never crossed. That
        # assertion is only honest when the level history is reliably
        # GOVERNED_UNMODIFIED. Under any other valid history state
        # (unknown/legacy, modified-after-entry without temporal proof,
        # or contradictory/required-evidence-missing) the level's true
        # value for that window is not established, so a non-crossing
        # observation of THIS supplied value proves nothing — fall back
        # instead of fabricating a negative claim.
        if level_history_status == LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED:
            return GovernedLevelTouchConclusion(
                level_kind=level_kind, status=GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
                detail="no compatible crossing of the supplied value was observed in the acquired evidence window, "
                       "for a level reliably unmodified since entry",
            )
        return GovernedLevelTouchConclusion(level_kind=level_kind, status=GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE, detail=CANONICAL_FALLBACK)

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
        if self.status != GOVERNED_ORDER_UNAVAILABLE and self.detail == CANONICAL_FALLBACK:
            raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "a non-fallback order conclusion must not use the canonical fallback sentence")


def classify_governed_order(
    *, summary: ObservedNumericalCrossingSummary, target_level_history_status: str, stop_level_history_status: str,
    target_price_basis_status: str, stop_price_basis_status: str, target_bars_available: bool, stop_bars_available: bool,
    target_entry_snapshot_present: bool = True, target_exit_snapshot_present: bool = True,
    target_entry_value=None, target_exit_value=None,
    stop_entry_snapshot_present: bool = True, stop_exit_snapshot_present: bool = True,
    stop_entry_value=None, stop_exit_value=None,
) -> GovernedOrderConclusion:
    """Mandatory Correction 1/2 — derives a GovernedLevelTouchConclusion
    for EACH side via classify_governed_level_touch (the exact same
    function used standalone for a single-level touch conclusion), then
    consults ONLY those two conclusions' own status values (never the
    raw level_history_status/endpoint/basis/bars-available inputs again)
    to decide every governed order conclusion: the two ordered
    (target-before-stop/stop-before-target) and same-bar patterns
    require BOTH per-level conclusions SUPPORTED; the single-sided
    (target-only/stop-only) and neither-observed patterns require the
    relevant side(s) SUPPORTED and/or the other side(s) a definitive
    negative, per _side_is_trustworthy/_side_is_definitive_negative."""
    if type(summary) is not ObservedNumericalCrossingSummary:
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "summary must be exactly an ObservedNumericalCrossingSummary")
    if type(target_bars_available) is not bool or type(stop_bars_available) is not bool:
        raise GovernedConclusionContractError(INVALID_GOVERNED_INPUT, "bars_available flags must be exact bool")
    _validate_status_member(target_level_history_status, ALL_LEVEL_HISTORY_STATUSES, "target_level_history_status")
    _validate_status_member(stop_level_history_status, ALL_LEVEL_HISTORY_STATUSES, "stop_level_history_status")
    _validate_status_member(target_price_basis_status, ALL_PRICE_BASIS_STATUSES, "target_price_basis_status")
    _validate_status_member(stop_price_basis_status, ALL_PRICE_BASIS_STATUSES, "stop_price_basis_status")

    # The ONE shared per-level eligibility path (Mandatory Correction 1)
    # — every governed order conclusion below is gated on these two
    # results, never on a re-derived or looser check.
    target_touch = classify_governed_level_touch(
        level_kind=TARGET_VALUE, observation=summary.target_observation,
        level_history_status=target_level_history_status, price_basis_status=target_price_basis_status,
        bars_available=target_bars_available,
        entry_snapshot_present=target_entry_snapshot_present, exit_snapshot_present=target_exit_snapshot_present,
        entry_value=target_entry_value, exit_value=target_exit_value,
    )
    stop_touch = classify_governed_level_touch(
        level_kind=STOP_VALUE, observation=summary.stop_observation,
        level_history_status=stop_level_history_status, price_basis_status=stop_price_basis_status,
        bars_available=stop_bars_available,
        entry_snapshot_present=stop_entry_snapshot_present, exit_snapshot_present=stop_exit_snapshot_present,
        entry_value=stop_entry_value, exit_value=stop_exit_value,
    )

    pattern = summary.safely_attributable_pattern

    if pattern == SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED:
        # Correction 2E — a definitive NEITHER_OBSERVED still requires
        # complete, noncontradictory evidence for both sides (here, that
        # simply means neither side raised a genuine evidence gap).
        if not (_side_is_trustworthy(target_touch) and _side_is_trustworthy(stop_touch)):
            return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)
        return GovernedOrderConclusion(status=GOVERNED_ORDER_NEITHER_OBSERVED, detail="no numerical level value was supplied for either factor")

    if pattern == SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE:
        if summary.partial_boundary_observation_present:
            # Only partial-boundary evidence exists — retained as an
            # observation, but it must never become a definitive order
            # (Gate 1I "partial-boundary evidence", Correction 2F).
            return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)
        if not (_side_is_trustworthy(target_touch) and _side_is_trustworthy(stop_touch)):
            return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)
        return GovernedOrderConclusion(status=GOVERNED_ORDER_NEITHER_OBSERVED, detail="no compatible crossing of either supplied value was safely observed")

    if pattern == SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE:
        # Correction 2A — TARGET_ONLY requires the target side to be a
        # SUPPORTED governed touch, and the stop side to be a definitive
        # negative per its own conclusion status alone — never an
        # unknown/legacy/modified/missing-snapshot/malformed/
        # contradictory/basis-incompatible stop side.
        if target_touch.is_supported and _side_is_definitive_negative(stop_touch):
            return GovernedOrderConclusion(status=GOVERNED_ORDER_TARGET_ONLY_OBSERVED, detail="only the target value was safely observed to cross")
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    if pattern == SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE:
        # Correction 2B — exact symmetric rule.
        if stop_touch.is_supported and _side_is_definitive_negative(target_touch):
            return GovernedOrderConclusion(status=GOVERNED_ORDER_STOP_ONLY_OBSERVED, detail="only the stop value was safely observed to cross")
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    if pattern == SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR:
        # Correction 2C — SAME_BAR_ORDER_UNKNOWN only when BOTH per-level
        # governed touch conclusions are SUPPORTED; never infer
        # target-first or stop-first from candle direction or from the
        # mere presence of both observations on one bar (Gate 1I).
        if target_touch.is_supported and stop_touch.is_supported:
            return GovernedOrderConclusion(
                status=GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
                detail="both values were safely observed to cross within the same OHLC bar — intrabar order is unknown",
            )
        return GovernedOrderConclusion(status=GOVERNED_ORDER_UNAVAILABLE, detail=CANONICAL_FALLBACK)

    # Ordered pattern (target-before-stop or stop-before-target).
    # Correction 2D — requires BOTH per-level governed touch conclusions
    # SUPPORTED (which already implies both levels are GOVERNED_
    # UNMODIFIED, both endpoint contracts are complete/consistent, and
    # both price bases are compatible — classify_governed_level_touch
    # never returns SUPPORTED otherwise).
    if not (target_touch.is_supported and stop_touch.is_supported):
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
