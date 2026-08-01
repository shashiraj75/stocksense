"""
MFE/MAE, stop/target touch detection, and touch-order/ambiguity
classification — Trade Postmortem Sprint 3A, Stages 6/7/8.

Pure functions only: every function here takes an already-validated
PricePathEvidenceBundle (and the trade's own entry/exit prices and
applicable stop/target levels) and returns a typed result. No I/O, no
database access — exhaustively unit-testable with hand-built bundles.

**Boundary contamination policy (Stages 4/6, acceptance gates E/F):**
MFE/MAE are computed ONLY from bars strictly between the entry-date bar
and the exit-date bar — the entry and exit bars themselves are EXCLUDED
from excursion tracking, because this codebase has no tick data to prove
their high/low occurred after entry (or before exit) rather than before
entry (or after exit). A same-day (entry_date == exit_date) trade
therefore has ZERO valid excursion bars and MFE/MAE are honestly
INSUFFICIENT_EVIDENCE, never approximated from the one ambiguous bar
available. Touch DETECTION is less strict than excursion tracking — a
level crossing observed in a boundary bar is still real information, so
it is checked and reported, but classified BOUNDARY_BAR_AMBIGUOUS rather
than a clean touch, since it cannot be attributed with confidence to the
in-trade portion of that session.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle
from services.postmortem.price_path_identity import CALCULATION_RULES_VERSION as RULES_VERSION

# --- Touch types (Stage 7) ---
TOUCH_TYPE_NORMAL = "NORMAL"            # high/low crossed the level within the bar's ordinary range
TOUCH_TYPE_GAP_THROUGH = "GAP_THROUGH"  # the bar's OPEN was already beyond the level (gap open)
TOUCH_TYPE_NOT_TOUCHED = "NOT_TOUCHED"

# --- Touch-order classification (Stage 8) ---
TARGET_BEFORE_STOP = "TARGET_BEFORE_STOP"
STOP_BEFORE_TARGET = "STOP_BEFORE_TARGET"
TARGET_ONLY = "TARGET_ONLY"
STOP_ONLY = "STOP_ONLY"
NEITHER_OBSERVED = "NEITHER_OBSERVED"
BOTH_SAME_BAR_AMBIGUOUS = "BOTH_SAME_BAR_AMBIGUOUS"
BOUNDARY_BAR_AMBIGUOUS = "BOUNDARY_BAR_AMBIGUOUS"
LEVEL_HISTORY_INCOMPLETE = "LEVEL_HISTORY_INCOMPLETE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# --- Evidence completeness for excursion metrics ---
EXCURSION_COMPLETE = "COMPLETE"
EXCURSION_NO_INTERIOR_BARS = "NO_INTERIOR_BARS"
EXCURSION_BASIS_INCOMPATIBLE = "BASIS_INCOMPATIBLE"
EXCURSION_INDETERMINATE_PNL = "INDETERMINATE_PNL"

# --- Stage G level-history inventory finding ---
# Three possible factual findings about whether this codebase can prove
# what stop/target value applied at each point in a trade's holding
# period:
#   A. LEVEL_HISTORY_COMPLETE   — a full timestamped edit history exists.
#   B. LEVEL_HISTORY_ENDPOINTS_ONLY — only the entry-time value
#      (paper_trade_entry_snapshot.user_selected_stop_loss/
#      target_price) and the final value at close
#      (paper_trade_exit_snapshot.final_stop_loss/final_target_price)
#      are known; any edits in between, and exactly when they took
#      effect, are not recorded.
#   C. LEVEL_HISTORY_UNAVAILABLE — neither endpoint is known.
#
# Inspection of entry_snapshot.py and exit_snapshot.py (both already
# built in Sprint 2) confirms finding B is this codebase's actual,
# current state: paper_trades.stop_loss/target_price are live-mutable
# columns with no edit log, so only the entry-time and final-at-close
# values are durably knowable — never a full history. Concretely, this
# means a real trade calling classify_touch_order MUST pass
# level_history_complete=False (finding B, not A) unless the stop and
# target were verified never to have changed between entry and exit —
# LEVEL_HISTORY_INCOMPLETE will be the near-universal real-world outcome
# for any trade whose levels were ever edited, exactly as flagged in the
# Sprint 3A checkpoint.
LEVEL_HISTORY_COMPLETE = "LEVEL_HISTORY_COMPLETE"
LEVEL_HISTORY_ENDPOINTS_ONLY = "LEVEL_HISTORY_ENDPOINTS_ONLY"
LEVEL_HISTORY_UNAVAILABLE = "LEVEL_HISTORY_UNAVAILABLE"

CURRENT_LEVEL_HISTORY_FINDING = LEVEL_HISTORY_ENDPOINTS_ONLY


def _interior_bars(bundle: PricePathEvidenceBundle) -> tuple[PricePathBar, ...]:
    """Bars strictly between the entry-date bar and the exit-date bar,
    PLUS the entry-date bar itself when entry_bar_policy is
    ENTRY_BAR_INCLUDED_FULL (Pre-Stage-H Correction 1 — entry occurred
    exactly at official session open, so the full daily bar is genuine
    post-entry evidence), and symmetrically the exit-date bar when
    exit_bar_policy is EXIT_BAR_INCLUDED_FULL. If entry and exit fall on
    the same session and that policy is not the SAME_DAY_FULL_SESSION
    case, this is empty by construction, never approximated."""
    if not bundle.bars:
        return ()
    entry_date = bundle.requested_window_start
    exit_date = bundle.requested_window_end
    result = [b for b in bundle.bars if entry_date < b.session_date < exit_date]
    if bundle.entry_bar_policy == "ENTRY_BAR_INCLUDED_FULL":
        result.extend(b for b in bundle.bars if b.session_date == entry_date and b not in result)
    if bundle.exit_bar_policy == "EXIT_BAR_INCLUDED_FULL":
        result.extend(b for b in bundle.bars if b.session_date == exit_date and b not in result)
    result.sort(key=lambda b: b.session_date)
    return tuple(result)


def _is_finite_positive(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class ExcursionResult:
    evidence_completeness: str
    mfe_price: float | None = None
    mfe_abs: float | None = None
    mfe_pct: float | None = None
    mfe_timestamp_first_observed: datetime | None = None
    mae_price: float | None = None
    mae_signed_abs: float | None = None
    mae_signed_pct: float | None = None
    mae_magnitude_abs: float | None = None
    mae_magnitude_pct: float | None = None
    mae_timestamp_first_observed: datetime | None = None
    exit_vs_mfe_giveback_abs: float | None = None
    exit_vs_mfe_giveback_pct_of_entry: float | None = None
    captured_mfe_pct: float | None = None
    bar_evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)


def compute_excursion(
    bundle: PricePathEvidenceBundle, *, entry_price: float, exit_price: float | None,
) -> ExcursionResult:
    """Stage 6 — MFE (maximum favorable excursion) and MAE (maximum
    adverse excursion, both signed and magnitude), for a long-only
    trade, computed strictly from interior (non-boundary) bars.

    mfe_price      = max(bar.high for bar in interior_bars)
    mfe_abs         = mfe_price - entry_price
    mfe_pct         = (mfe_price / entry_price - 1) * 100

    mae_price               = min(bar.low for bar in interior_bars)
    mae_signed_abs          = mae_price - entry_price          (negative for an adverse move)
    mae_signed_pct          = (mae_price / entry_price - 1) * 100
    mae_magnitude_abs       = entry_price - mae_price           (positive magnitude)
    mae_magnitude_pct       = (entry_price - mae_price) / entry_price * 100

    captured_mfe_pct = realized favorable move / MFE, expressed as a
    percentage of the MFE itself — null whenever MFE <= 0, P&L is
    indeterminate, the price basis is incompatible, or there are no
    interior bars at all. Never divides by zero."""
    if bundle.price_adjustment_basis == "UNKNOWN_ADJUSTMENT":
        return ExcursionResult(
            evidence_completeness=EXCURSION_BASIS_INCOMPATIBLE,
            limitations=tuple(bundle.limitations) or ("price adjustment basis is incompatible or unknown",),
        )
    if not _is_finite_positive(entry_price):
        return ExcursionResult(
            evidence_completeness=EXCURSION_INDETERMINATE_PNL,
            limitations=("entry_price is not a finite positive number — excursion cannot be computed",),
        )

    interior = _interior_bars(bundle)
    if not interior:
        return ExcursionResult(
            evidence_completeness=EXCURSION_NO_INTERIOR_BARS,
            limitations=(
                "no bars exist strictly between the entry and exit sessions — a same-day or "
                "adjacent-day trade has no evidence window daily bars can safely attribute to "
                "the in-trade period",
            ),
        )

    mfe_bar = max(interior, key=lambda b: b.high)
    mae_bar = min(interior, key=lambda b: b.low)
    mfe_price = mfe_bar.high
    mae_price = mae_bar.low

    mfe_abs = mfe_price - entry_price
    mfe_pct = (mfe_price / entry_price - 1) * 100
    mae_signed_abs = mae_price - entry_price
    mae_signed_pct = (mae_price / entry_price - 1) * 100
    mae_magnitude_abs = entry_price - mae_price
    mae_magnitude_pct = (entry_price - mae_price) / entry_price * 100

    giveback_abs = None
    giveback_pct = None
    captured_mfe_pct = None
    if exit_price is not None and _is_finite_positive(exit_price):
        giveback_abs = mfe_price - exit_price
        giveback_pct = giveback_abs / entry_price * 100
        if mfe_abs > 0:
            realized_move = exit_price - entry_price
            captured_mfe_pct = (realized_move / mfe_abs) * 100

    bar_ids = tuple(f"BAR-{bundle.paper_trade_id}-{b.session_date.isoformat()}" for b in interior)

    return ExcursionResult(
        evidence_completeness=EXCURSION_COMPLETE,
        mfe_price=mfe_price, mfe_abs=mfe_abs, mfe_pct=mfe_pct,
        mfe_timestamp_first_observed=mfe_bar.timestamp,
        mae_price=mae_price, mae_signed_abs=mae_signed_abs, mae_signed_pct=mae_signed_pct,
        mae_magnitude_abs=mae_magnitude_abs, mae_magnitude_pct=mae_magnitude_pct,
        mae_timestamp_first_observed=mae_bar.timestamp,
        exit_vs_mfe_giveback_abs=giveback_abs, exit_vs_mfe_giveback_pct_of_entry=giveback_pct,
        captured_mfe_pct=captured_mfe_pct,
        bar_evidence_ids=bar_ids,
    )


@dataclass(frozen=True)
class TouchResult:
    touched: bool
    touch_type: str
    first_observed_bar: PricePathBar | None
    is_boundary_bar: bool
    evidence_id: str | None


def _detect_touch(bars: tuple[PricePathBar, ...], level: float, *, direction: str, boundary_dates: frozenset) -> TouchResult:
    """direction='target' checks high>=level (favorable ceiling);
    direction='stop' checks low<=level (adverse floor). Returns the
    FIRST bar (by ascending session_date — bars are always pre-sorted)
    where the level was crossed."""
    for bar in bars:
        crossed = (bar.high >= level) if direction == "target" else (bar.low <= level)
        if not crossed:
            continue
        gapped = (bar.open >= level) if direction == "target" else (bar.open <= level)
        touch_type = TOUCH_TYPE_GAP_THROUGH if gapped else TOUCH_TYPE_NORMAL
        is_boundary = bar.session_date in boundary_dates
        evidence_id = f"BAR-{bar.session_date.isoformat()}-{direction}"
        return TouchResult(touched=True, touch_type=touch_type, first_observed_bar=bar, is_boundary_bar=is_boundary, evidence_id=evidence_id)
    return TouchResult(touched=False, touch_type=TOUCH_TYPE_NOT_TOUCHED, first_observed_bar=None, is_boundary_bar=False, evidence_id=None)


def detect_touches(
    bundle: PricePathEvidenceBundle, *, applicable_stop: float | None, applicable_target: float | None,
) -> tuple[TouchResult, TouchResult]:
    """Stage 7 — target-touch and stop-touch detection across the FULL
    bundle (including boundary bars — see module docstring; boundary
    touches are still reported, just flagged `is_boundary_bar=True` so
    downstream ordering logic can classify them conservatively). Returns
    (target_touch, stop_touch)."""
    boundary_dates = frozenset()
    if bundle.bars:
        boundary_dates = frozenset({bundle.bars[0].session_date, bundle.bars[-1].session_date})

    target_touch = (
        _detect_touch(bundle.bars, applicable_target, direction="target", boundary_dates=boundary_dates)
        if applicable_target is not None
        else TouchResult(False, TOUCH_TYPE_NOT_TOUCHED, None, False, None)
    )
    stop_touch = (
        _detect_touch(bundle.bars, applicable_stop, direction="stop", boundary_dates=boundary_dates)
        if applicable_stop is not None
        else TouchResult(False, TOUCH_TYPE_NOT_TOUCHED, None, False, None)
    )
    return target_touch, stop_touch


def classify_touch_order(
    target_touch: TouchResult, stop_touch: TouchResult, *,
    applicable_stop: float | None, applicable_target: float | None,
    level_history_complete: bool,
) -> str:
    """Stage 8 — the exact ordering rules, most-restrictive-first:

    1. Missing level history (stop/target were edited mid-trade and we
       only know the entry/final values, not the full history) — the
       final levels used for touch detection may not have applied for
       the whole holding period, so ordering is LEVEL_HISTORY_INCOMPLETE
       regardless of what the (potentially wrong) levels appear to show.
    2. Neither level is even configured — INSUFFICIENT_EVIDENCE.
    3. Neither touched — NEITHER_OBSERVED.
    4. Only one touched — TARGET_ONLY / STOP_ONLY.
    5. Both touched in the SAME bar — BOTH_SAME_BAR_AMBIGUOUS. Never
       inferred from candle direction or open/close — see module
       docstring and Stage 8's own explicit prohibition.
    6. Either touch happened in a boundary bar — BOUNDARY_BAR_AMBIGUOUS
       (even if the two touches are in different bars, a boundary-bar
       touch's own timing within its session is unverifiable, so it
       cannot be safely ordered against the other touch either).
    7. Different, non-boundary bars — the earlier bar's touch is first."""
    if not level_history_complete:
        return LEVEL_HISTORY_INCOMPLETE
    if applicable_stop is None and applicable_target is None:
        return INSUFFICIENT_EVIDENCE
    if not target_touch.touched and not stop_touch.touched:
        return NEITHER_OBSERVED
    if target_touch.touched and not stop_touch.touched:
        return TARGET_ONLY
    if stop_touch.touched and not target_touch.touched:
        return STOP_ONLY

    # Both touched.
    same_bar = target_touch.first_observed_bar.session_date == stop_touch.first_observed_bar.session_date
    if same_bar:
        return BOTH_SAME_BAR_AMBIGUOUS
    if target_touch.is_boundary_bar or stop_touch.is_boundary_bar:
        return BOUNDARY_BAR_AMBIGUOUS
    if target_touch.first_observed_bar.session_date < stop_touch.first_observed_bar.session_date:
        return TARGET_BEFORE_STOP
    return STOP_BEFORE_TARGET


# ============================================================================
# Stage J4B — Observed numerical-crossing and session-attribution core.
#
# IMPLEMENTED, UNIT-TESTED, NOT REPORT-WIRED, NOT PERSISTED, NOT
# ENDPOINT-VERIFIED. Purely additive: nothing above this line is
# changed, and price_path_generation.py / price_path_claims.py /
# paper_trading.py do not import or call anything below this line in
# this phase — TouchResult, detect_touches, classify_touch_order,
# build_touch_order_claim, and every persisted report/claim/evidence-item
# shape remain byte-for-byte unchanged.
#
# This section describes ONLY what immutable daily OHLC bars prove about
# two SUPPLIED numerical values — it deliberately never calls them
# "stop"/"target LEVELS" being ACTIVE, and never claims a value was
# touched, triggered, achieved, or that the trade should have closed.
# Whether a supplied numerical value was the genuinely active configured
# stop/target throughout the holding period is a separate, later
# (J4C/J4D) governed conclusion this module does not make — see the
# Stage J ADR's J4A/J4A.1/J4B sections for the full layering rationale
# and the still-unresolved level-history write-invariant constraints.
# ============================================================================

# --- Session attribution (Stage J4B, Stage 3) ---
SESSION_ATTRIBUTION_INTERIOR = "INTERIOR"
SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL = "ENTRY_INCLUDED_FULL"
SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL = "EXIT_INCLUDED_FULL"
SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL = "SAME_DAY_INCLUDED_FULL"
SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN = "ENTRY_PARTIAL_UNKNOWN"
SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN = "EXIT_PARTIAL_UNKNOWN"
SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN = "SAME_DAY_PARTIAL_UNKNOWN"

_SAFELY_ATTRIBUTABLE_SESSION_ATTRIBUTIONS = frozenset({
    SESSION_ATTRIBUTION_INTERIOR,
    SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL,
    SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL,
    SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL,
})

_ENTRY_BAR_POLICY_INCLUDED_FULL = "ENTRY_BAR_INCLUDED_FULL"
_ENTRY_BAR_POLICY_PARTIAL_UNKNOWN = "ENTRY_BAR_PARTIAL_UNKNOWN"
_EXIT_BAR_POLICY_INCLUDED_FULL = "EXIT_BAR_INCLUDED_FULL"
_EXIT_BAR_POLICY_PARTIAL_UNKNOWN = "EXIT_BAR_PARTIAL_UNKNOWN"


class SessionAttributionError(ValueError):
    """Raised when a bar's session_date falls outside the requested
    window, or maps to an entry/exit boundary-policy value this function
    does not recognize — fails closed rather than silently treating an
    unrecognized policy as interior (safely-attributable) evidence."""


def classify_bar_session_attribution(bundle: PricePathEvidenceBundle, bar: PricePathBar) -> str:
    """Stage J4B, Stage 3 — pure classification of a single bar's
    session_date against ONLY bundle.requested_window_start/end and
    bundle.entry_bar_policy/exit_bar_policy. Never consults bars[0],
    bars[-1], observed_window_start/end, or raw array position — those
    are acquisition-time/array artifacts, not the governed boundary-
    policy facts this function is required to use (Stage J4A finding #2,
    the exact defect this function corrects for the crossing-observation
    layer without touching detect_touches's own, still-unchanged,
    array-position behavior)."""
    entry_date = bundle.requested_window_start
    exit_date = bundle.requested_window_end
    session_date = bar.session_date

    if entry_date == exit_date:
        if session_date != entry_date:
            raise SessionAttributionError(
                f"bar session_date {session_date} is outside the single-day requested window {entry_date}"
            )
        entry_policy = bundle.entry_bar_policy
        exit_policy = bundle.exit_bar_policy
        if entry_policy == _ENTRY_BAR_POLICY_INCLUDED_FULL and exit_policy == _EXIT_BAR_POLICY_INCLUDED_FULL:
            return SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        if entry_policy == _ENTRY_BAR_POLICY_PARTIAL_UNKNOWN or exit_policy == _EXIT_BAR_POLICY_PARTIAL_UNKNOWN:
            return SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN
        raise SessionAttributionError(
            f"unrecognized same-day boundary-policy combination: entry={entry_policy!r} exit={exit_policy!r}"
        )

    if entry_date < session_date < exit_date:
        return SESSION_ATTRIBUTION_INTERIOR

    if session_date == entry_date:
        if bundle.entry_bar_policy == _ENTRY_BAR_POLICY_INCLUDED_FULL:
            return SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        if bundle.entry_bar_policy == _ENTRY_BAR_POLICY_PARTIAL_UNKNOWN:
            return SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        raise SessionAttributionError(f"unrecognized entry_bar_policy {bundle.entry_bar_policy!r}")

    if session_date == exit_date:
        if bundle.exit_bar_policy == _EXIT_BAR_POLICY_INCLUDED_FULL:
            return SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL
        if bundle.exit_bar_policy == _EXIT_BAR_POLICY_PARTIAL_UNKNOWN:
            return SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN
        raise SessionAttributionError(f"unrecognized exit_bar_policy {bundle.exit_bar_policy!r}")

    raise SessionAttributionError(
        f"bar session_date {session_date} is outside the requested window [{entry_date}, {exit_date}]"
    )


def is_safely_attributable_session(session_attribution: str) -> bool:
    """INTERIOR and any INCLUDED_FULL (entry, exit, or same-day) session
    attribution is safely attributable to the holding period; any
    PARTIAL_UNKNOWN attribution is observed but never safely
    attributable."""
    return session_attribution in _SAFELY_ATTRIBUTABLE_SESSION_ATTRIBUTIONS


# --- Numerical crossing observation (Stage J4B, Stage 4) ---
TARGET_VALUE = "TARGET_VALUE"
STOP_VALUE = "STOP_VALUE"

CROSSING_TYPE_NORMAL = "NORMAL"
CROSSING_TYPE_GAP_THROUGH = "GAP_THROUGH"
CROSSING_TYPE_NOT_OBSERVED = "NOT_OBSERVED"

SUPPLIED_AT_CALCULATION = "SUPPLIED_AT_CALCULATION"


@dataclass(frozen=True)
class NumericalLevelCrossingObservation:
    """Stage J4B, Stage 4 — describes ONLY what immutable daily OHLC
    bars prove about a SUPPLIED numerical value. Never claims the
    supplied value was an active configured stop/target level throughout
    the holding period, that it was "touched" in the governed sense, or
    that the trade should have closed — those are distinct, later
    (J4C/J4D) governed conclusions this object does not make.

    `first_observed_*` is the earliest crossing found anywhere in the
    bundle, regardless of session attribution. `first_safely_
    attributable_*` is the earliest crossing whose session attribution
    is safely attributable (INTERIOR or any INCLUDED_FULL) — these two
    may point at different bars, and BOTH are always retained (Stage
    J4A finding #5): an earlier PARTIAL_UNKNOWN-bar crossing is never
    discarded merely because a later safely-attributable bar also
    crosses the same value."""

    level_kind: str
    supplied_level_value: float | None
    supplied_value_basis: str
    value_supplied: bool
    crossed_anywhere: bool

    first_observed_crossing_type: str
    first_observed_session: date | None
    first_observed_bar: PricePathBar | None
    first_observed_evidence_id: str | None
    first_observed_session_attribution: str | None

    first_safely_attributable_crossing_type: str
    first_safely_attributable_session: date | None
    first_safely_attributable_bar: PricePathBar | None
    first_safely_attributable_evidence_id: str | None
    first_safely_attributable_session_attribution: str | None

    partial_boundary_crossing_observed: bool


def _no_value_observation(level_kind: str) -> NumericalLevelCrossingObservation:
    return NumericalLevelCrossingObservation(
        level_kind=level_kind, supplied_level_value=None, supplied_value_basis=SUPPLIED_AT_CALCULATION,
        value_supplied=False, crossed_anywhere=False,
        first_observed_crossing_type=CROSSING_TYPE_NOT_OBSERVED, first_observed_session=None,
        first_observed_bar=None, first_observed_evidence_id=None, first_observed_session_attribution=None,
        first_safely_attributable_crossing_type=CROSSING_TYPE_NOT_OBSERVED, first_safely_attributable_session=None,
        first_safely_attributable_bar=None, first_safely_attributable_evidence_id=None,
        first_safely_attributable_session_attribution=None,
        partial_boundary_crossing_observed=False,
    )


def _crossing_evidence_id(paper_trade_id: int, session_date: date, level_kind: str) -> str:
    """Deterministic — contains paper_trade_id, session_date, and level
    kind, per Stage J4B, Stage 4's explicit requirement."""
    return f"NUMERICAL-CROSSING-{paper_trade_id}-{session_date.isoformat()}-{level_kind}"


def observe_numerical_level_crossing(
    bundle: PricePathEvidenceBundle, supplied_level_value: float | None, level_kind: str,
) -> NumericalLevelCrossingObservation:
    """Stage J4B, Stage 5 — pure, no I/O: no provider call, no current
    quote, no stock-universe lookup, no database or network access.
    Scans bundle.bars in deterministic ascending session-date order.
    Never accepts, reads, or is influenced by any level-history input —
    this function's signature has no such parameter by construction."""
    if level_kind not in (TARGET_VALUE, STOP_VALUE):
        raise SessionAttributionError(f"unrecognized level_kind {level_kind!r}")

    if supplied_level_value is None:
        return _no_value_observation(level_kind)

    ordered_bars = sorted(bundle.bars, key=lambda b: b.session_date)

    first_observed = None
    first_observed_type = CROSSING_TYPE_NOT_OBSERVED
    first_observed_attribution = None
    first_safe = None
    first_safe_type = CROSSING_TYPE_NOT_OBSERVED
    first_safe_attribution = None
    partial_boundary_crossing_observed = False

    for bar in ordered_bars:
        if level_kind == TARGET_VALUE:
            crossed = bar.high >= supplied_level_value
            gapped = bar.open >= supplied_level_value
        else:
            crossed = bar.low <= supplied_level_value
            gapped = bar.open <= supplied_level_value
        if not crossed:
            continue

        attribution = classify_bar_session_attribution(bundle, bar)
        crossing_type = CROSSING_TYPE_GAP_THROUGH if gapped else CROSSING_TYPE_NORMAL
        safely_attributable = is_safely_attributable_session(attribution)

        if first_observed is None:
            first_observed = bar
            first_observed_type = crossing_type
            first_observed_attribution = attribution

        if not safely_attributable:
            partial_boundary_crossing_observed = True
        elif first_safe is None:
            first_safe = bar
            first_safe_type = crossing_type
            first_safe_attribution = attribution

    return NumericalLevelCrossingObservation(
        level_kind=level_kind, supplied_level_value=supplied_level_value, supplied_value_basis=SUPPLIED_AT_CALCULATION,
        value_supplied=True, crossed_anywhere=first_observed is not None,
        first_observed_crossing_type=first_observed_type,
        first_observed_session=(first_observed.session_date if first_observed is not None else None),
        first_observed_bar=first_observed,
        first_observed_evidence_id=(
            _crossing_evidence_id(bundle.paper_trade_id, first_observed.session_date, level_kind)
            if first_observed is not None else None
        ),
        first_observed_session_attribution=first_observed_attribution,
        first_safely_attributable_crossing_type=first_safe_type,
        first_safely_attributable_session=(first_safe.session_date if first_safe is not None else None),
        first_safely_attributable_bar=first_safe,
        first_safely_attributable_evidence_id=(
            _crossing_evidence_id(bundle.paper_trade_id, first_safe.session_date, level_kind)
            if first_safe is not None else None
        ),
        first_safely_attributable_session_attribution=first_safe_attribution,
        partial_boundary_crossing_observed=partial_boundary_crossing_observed,
    )


# --- Observed crossing summary (Stage J4B, Stage 6) ---
PATTERN_NO_NUMERICAL_VALUES_SUPPLIED = "NO_NUMERICAL_VALUES_SUPPLIED"
PATTERN_NEITHER_NUMERICAL_VALUE_CROSSED = "NEITHER_NUMERICAL_VALUE_CROSSED"
PATTERN_TARGET_VALUE_ONLY_CROSSED = "TARGET_VALUE_ONLY_CROSSED"
PATTERN_STOP_VALUE_ONLY_CROSSED = "STOP_VALUE_ONLY_CROSSED"
PATTERN_BOTH_VALUES_SAME_BAR = "BOTH_VALUES_SAME_BAR"
PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR = "TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR"
PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR = "STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR"

SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED = "NO_NUMERICAL_VALUES_SUPPLIED"
SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE = "NEITHER_SAFELY_ATTRIBUTABLE"
SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE = "TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE"
SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE = "STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE"
SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR = "BOTH_VALUES_SAME_SAFE_BAR"
SAFE_PATTERN_TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR = "TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR"
SAFE_PATTERN_STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR = "STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR"


@dataclass(frozen=True)
class ObservedNumericalCrossingSummary:
    """Stage J4B, Stage 6 — derivable SOLELY from the two crossing
    observations passed in; accepts no level-history input whatsoever.
    Deliberately never uses NO_LEVELS_CONFIGURED, TARGET_ONLY,
    STOP_ONLY, TARGET_BEFORE_STOP, or STOP_BEFORE_TARGET — those are
    governed touch conclusions reserved for the later, level-history-
    aware J4C/J4D phase, not bare observations about the bars alone."""

    target_observation: NumericalLevelCrossingObservation
    stop_observation: NumericalLevelCrossingObservation
    any_observation_pattern: str
    safely_attributable_pattern: str
    partial_boundary_observation_present: bool


def _classify_pattern(
    target_hit: bool, stop_hit: bool, target_date: date | None, stop_date: date | None, *,
    neither: str, target_only: str, stop_only: str, same_bar: str, target_before_stop: str, stop_before_target: str,
) -> str:
    if not target_hit and not stop_hit:
        return neither
    if target_hit and not stop_hit:
        return target_only
    if stop_hit and not target_hit:
        return stop_only
    if target_date == stop_date:
        return same_bar
    if target_date < stop_date:
        return target_before_stop
    return stop_before_target


def summarize_observed_numerical_crossings(
    target_observation: NumericalLevelCrossingObservation,
    stop_observation: NumericalLevelCrossingObservation,
) -> ObservedNumericalCrossingSummary:
    """Stage J4B, Stage 6 — pure, no I/O, no level-history input of any
    kind (no parameter for it exists on this function's signature)."""
    partial_boundary_present = (
        target_observation.partial_boundary_crossing_observed
        or stop_observation.partial_boundary_crossing_observed
    )

    if not target_observation.value_supplied and not stop_observation.value_supplied:
        any_pattern = PATTERN_NO_NUMERICAL_VALUES_SUPPLIED
        safe_pattern = SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED
    else:
        any_pattern = _classify_pattern(
            target_observation.crossed_anywhere, stop_observation.crossed_anywhere,
            target_observation.first_observed_session, stop_observation.first_observed_session,
            neither=PATTERN_NEITHER_NUMERICAL_VALUE_CROSSED, target_only=PATTERN_TARGET_VALUE_ONLY_CROSSED,
            stop_only=PATTERN_STOP_VALUE_ONLY_CROSSED, same_bar=PATTERN_BOTH_VALUES_SAME_BAR,
            target_before_stop=PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR,
            stop_before_target=PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR,
        )
        target_safe = target_observation.first_safely_attributable_session is not None
        stop_safe = stop_observation.first_safely_attributable_session is not None
        safe_pattern = _classify_pattern(
            target_safe, stop_safe,
            target_observation.first_safely_attributable_session, stop_observation.first_safely_attributable_session,
            neither=SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE, target_only=SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
            stop_only=SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE, same_bar=SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR,
            target_before_stop=SAFE_PATTERN_TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR,
            stop_before_target=SAFE_PATTERN_STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR,
        )

    return ObservedNumericalCrossingSummary(
        target_observation=target_observation, stop_observation=stop_observation,
        any_observation_pattern=any_pattern, safely_attributable_pattern=safe_pattern,
        partial_boundary_observation_present=partial_boundary_present,
    )
