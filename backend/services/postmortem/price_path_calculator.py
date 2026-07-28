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
from datetime import datetime

from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle

RULES_VERSION = "1.0.0"

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
    """Bars strictly between the entry-date bar and the exit-date bar —
    see module docstring's boundary-contamination policy. If entry and
    exit fall on the same session, or on adjacent sessions with nothing
    between them, this is empty by construction, never approximated."""
    if not bundle.bars:
        return ()
    entry_date = bundle.requested_window_start
    exit_date = bundle.requested_window_end
    return tuple(b for b in bundle.bars if entry_date < b.session_date < exit_date)


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
