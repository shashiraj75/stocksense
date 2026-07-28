"""
Trade Postmortem Sprint 3A — unit tests for MFE/MAE/touch-detection/
ambiguity classification (price_path_calculator.py) and bundle
construction (price_path_acquisition.py's pure build_price_path_evidence).

Covers a substantial core of the Stage 15 test matrix: monotonic paths,
reversal/recovery, target/stop-only, ordering on separate bars, same-bar
ambiguity, boundary-bar ambiguity, gap-through, missing/duplicate/out-of-
order bars (via the acquisition layer's own defensive handling), zero/
invalid entry price, indeterminate P&L, split-adjustment mismatch, and
deterministic replay.
"""
import datetime as dt

import pytest

from services.market_hours import ET, IST
from services.postmortem.price_path_acquisition import build_price_path_evidence
from services.postmortem.price_path_calculator import (
    BOTH_SAME_BAR_AMBIGUOUS,
    BOUNDARY_BAR_AMBIGUOUS,
    EXCURSION_BASIS_INCOMPATIBLE,
    EXCURSION_COMPLETE,
    EXCURSION_INDETERMINATE_PNL,
    EXCURSION_NO_INTERIOR_BARS,
    LEVEL_HISTORY_INCOMPLETE,
    NEITHER_OBSERVED,
    STOP_BEFORE_TARGET,
    STOP_ONLY,
    TARGET_BEFORE_STOP,
    TARGET_ONLY,
    TOUCH_TYPE_GAP_THROUGH,
    TOUCH_TYPE_NORMAL,
    classify_touch_order,
    compute_excursion,
    detect_touches,
)


def _bundle(entry, exit_, bars, split_events=()):
    return build_price_path_evidence(
        paper_trade_id=1, user_id="user-aaa", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=entry, exit_timestamp=exit_,
        raw_bars=bars, split_events=list(split_events),
    )


def _raw(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


_D = lambda n: dt.date(2026, 6, n)
_TS = lambda n, hour=15: dt.datetime(2026, 6, n, hour, tzinfo=ET)


@pytest.mark.unit
class TestExcursionFormulas:
    def test_monotonic_favorable_path(self):
        """#1 — price only rises after entry."""
        bars = [_raw(_D(1), 100, 101, 99, 100.5), _raw(_D(2), 100.5, 105, 100, 104),
                _raw(_D(3), 104, 110, 103, 109), _raw(_D(4), 109, 111, 108, 110)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=110.0)
        assert exc.evidence_completeness == EXCURSION_COMPLETE
        assert exc.mfe_price == 110.0  # from interior bar June 3, high=110
        assert exc.mae_price == 100.0  # interior low, June 2

    def test_monotonic_adverse_path(self):
        """#2 — price only falls after entry."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 100, 95, 96),
                _raw(_D(3), 96, 97, 90, 91), _raw(_D(4), 91, 92, 88, 89)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=89.0)
        assert exc.mae_price == 90.0
        assert exc.mae_magnitude_abs == pytest.approx(10.0)
        assert exc.mae_magnitude_pct == pytest.approx(10.0)
        assert exc.mae_signed_abs == pytest.approx(-10.0)

    def test_favorable_then_adverse_reversal(self):
        """#3 — price rises then reverses below entry."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 120, 100, 118),
                _raw(_D(3), 118, 119, 85, 90), _raw(_D(4), 90, 91, 88, 89)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=89.0)
        assert exc.mfe_price == 120.0
        assert exc.mae_price == 85.0
        assert exc.captured_mfe_pct == pytest.approx((89.0 - 100.0) / 20.0 * 100)

    def test_adverse_then_favorable_recovery(self):
        """#4 — price falls then recovers above entry."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 100, 80, 85),
                _raw(_D(3), 85, 115, 84, 112), _raw(_D(4), 112, 113, 110, 111)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=111.0)
        assert exc.mae_price == 80.0
        assert exc.mfe_price == 115.0

    def test_mfe_at_entry_price_when_price_never_favors(self):
        """#32 — MFE never exceeds entry (a straight-down move)."""
        bars = [_raw(_D(1), 100, 100, 99, 99.5), _raw(_D(2), 99.5, 100, 90, 91),
                _raw(_D(3), 91, 92, 88, 89), _raw(_D(4), 89, 90, 87, 88)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=88.0)
        assert exc.mfe_price == 100.0
        assert exc.mfe_abs == pytest.approx(0.0)
        assert exc.captured_mfe_pct is None  # mfe_abs <= 0, never divides by zero

    def test_mae_at_entry_price_when_price_never_adverse(self):
        """#33 — MAE never drops below entry (a straight-up move)."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 110, 100, 108),
                _raw(_D(3), 108, 115, 105, 112), _raw(_D(4), 112, 113, 110, 111)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=111.0)
        assert exc.mae_price == 100.0  # min interior low across June 2 (100) and June 3 (105)
        assert exc.mae_signed_abs == pytest.approx(0.0)  # never went below entry within interior bars

    def test_zero_entry_price_is_indeterminate(self):
        """#34 — a corrupt/zero entry price never divides by zero."""
        bars = [_raw(_D(2), 100, 105, 99, 101)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=0.0, exit_price=101.0)
        assert exc.evidence_completeness == EXCURSION_INDETERMINATE_PNL
        assert exc.mfe_abs is None

    def test_negative_entry_price_is_indeterminate(self):
        bars = [_raw(_D(2), 100, 105, 99, 101)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=-5.0, exit_price=101.0)
        assert exc.evidence_completeness == EXCURSION_INDETERMINATE_PNL

    def test_indeterminate_pnl_no_exit_price_still_computes_excursion_but_no_giveback(self):
        """#35 — an indeterminate exit price (None) must not block MFE/MAE
        themselves (those only need entry_price + bars), but giveback/
        captured_mfe, which need exit_price, become None."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 110, 95, 108), _raw(_D(4), 108, 109, 106, 107)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=None)
        assert exc.evidence_completeness == EXCURSION_COMPLETE
        assert exc.mfe_abs is not None
        assert exc.exit_vs_mfe_giveback_abs is None
        assert exc.captured_mfe_pct is None

    def test_same_day_trade_has_no_interior_bars(self):
        """A same-day entry+exit has zero interior bars — MFE/MAE are
        honestly NO_INTERIOR_BARS, never approximated from the one
        ambiguous (boundary) bar available."""
        bars = [_raw(_D(1), 100, 105, 98, 102)]
        same_day_entry = dt.datetime(2026, 6, 1, 10, tzinfo=ET)
        same_day_exit = dt.datetime(2026, 6, 1, 15, tzinfo=ET)
        bundle = _bundle(same_day_entry, same_day_exit, bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=102.0)
        assert exc.evidence_completeness == EXCURSION_NO_INTERIOR_BARS
        assert exc.mfe_abs is None

    def test_split_in_window_yields_basis_incompatible(self):
        """#27 — a split event in-window makes the whole bundle
        AMBIGUOUS_RESOLUTION/UNKNOWN_ADJUSTMENT, and excursion refuses to
        compute a misleading number."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 50, 55, 48, 52)]  # post-split halved prices
        bundle = _bundle(_TS(1), _TS(4), bars, split_events=[_D(2)])
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=52.0)
        assert exc.evidence_completeness == EXCURSION_BASIS_INCOMPATIBLE


@pytest.mark.unit
class TestTouchDetectionAndOrdering:
    def test_target_only(self):
        """#5"""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 112, 99, 110), _raw(_D(4), 110, 111, 108, 109)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=80.0, applicable_target=108.0)
        order = classify_touch_order(tt, st, applicable_stop=80.0, applicable_target=108.0, level_history_complete=True)
        assert tt.touched is True and st.touched is False
        assert order == TARGET_ONLY

    def test_stop_only(self):
        """#6"""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 85, 90), _raw(_D(4), 90, 92, 88, 89)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=92.0, applicable_target=150.0)
        order = classify_touch_order(tt, st, applicable_stop=92.0, applicable_target=150.0, level_history_complete=True)
        assert st.touched is True and tt.touched is False
        assert order == STOP_ONLY

    def test_neither_touched(self):
        """#7"""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 103, 98, 101), _raw(_D(4), 101, 102, 99, 100)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=50.0, applicable_target=200.0)
        order = classify_touch_order(tt, st, applicable_stop=50.0, applicable_target=200.0, level_history_complete=True)
        assert order == NEITHER_OBSERVED

    def test_target_before_stop_separate_bars(self):
        """#8"""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 112, 99, 108),
                _raw(_D(3), 108, 109, 85, 90), _raw(_D(5), 90, 92, 88, 89)]
        bundle = _bundle(_TS(1), _TS(5), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=110.0)
        order = classify_touch_order(tt, st, applicable_stop=88.0, applicable_target=110.0, level_history_complete=True)
        assert order == TARGET_BEFORE_STOP

    def test_stop_before_target_separate_bars(self):
        """#9"""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 85, 90),
                _raw(_D(3), 90, 115, 89, 112), _raw(_D(5), 112, 113, 110, 111)]
        bundle = _bundle(_TS(1), _TS(5), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=110.0)
        order = classify_touch_order(tt, st, applicable_stop=88.0, applicable_target=110.0, level_history_complete=True)
        assert order == STOP_BEFORE_TARGET

    def test_both_touched_in_one_daily_bar_is_ambiguous(self):
        """#10/#11 — a single daily bar touching both stop and target is
        ALWAYS ambiguous; never inferred from open/close direction."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 115, 85, 90), _raw(_D(4), 90, 92, 88, 89)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=110.0)
        order = classify_touch_order(tt, st, applicable_stop=88.0, applicable_target=110.0, level_history_complete=True)
        assert order == BOTH_SAME_BAR_AMBIGUOUS

    def test_entry_boundary_bar_touch_is_ambiguous(self):
        """#13 — a touch observed only in the entry-date bar is boundary-
        ambiguous (cannot prove it happened after entry)."""
        bars = [_raw(_D(1), 100, 115, 99, 110), _raw(_D(2), 110, 111, 108, 109), _raw(_D(4), 109, 110, 107, 108)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=50.0, applicable_target=112.0)
        order = classify_touch_order(tt, st, applicable_stop=50.0, applicable_target=112.0, level_history_complete=True)
        # target touched only in entry bar; stop never touched -> ordering
        # logic reports TARGET_ONLY at the order level, but the touch
        # itself must be flagged boundary — verified directly here.
        assert tt.is_boundary_bar is True
        assert order == TARGET_ONLY  # single-level touches aren't reclassified by order(); boundary flag carries the caveat

    def test_exit_boundary_bar_touch_flagged_boundary(self):
        """#14 — a touch observed only in the exit-date bar."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 98, 100), _raw(_D(4), 100, 101, 82, 85)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=150.0)
        assert st.is_boundary_bar is True

    def test_gap_above_target(self):
        """#15 — the bar's OPEN was already above target (gap open)."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 115, 116, 114, 115.5), _raw(_D(4), 115, 116, 114, 115)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=50.0, applicable_target=110.0)
        assert tt.touch_type == TOUCH_TYPE_GAP_THROUGH

    def test_gap_below_stop(self):
        """#16 — the bar's OPEN was already below stop (gap down)."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 85, 86, 84, 85.5), _raw(_D(4), 85, 86, 84, 85)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=90.0, applicable_target=150.0)
        assert st.touch_type == TOUCH_TYPE_GAP_THROUGH

    def test_normal_touch_type_when_not_gapped(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 112, 99, 108), _raw(_D(4), 108, 109, 106, 107)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, _ = detect_touches(bundle, applicable_stop=50.0, applicable_target=110.0)
        assert tt.touch_type == TOUCH_TYPE_NORMAL

    def test_missing_stop_level(self):
        """#29"""
        bars = [_raw(_D(2), 100, 112, 99, 108)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=None, applicable_target=110.0)
        assert st.touched is False and st.touch_type == "NOT_TOUCHED"

    def test_missing_target_level(self):
        """#30"""
        bars = [_raw(_D(2), 100, 101, 85, 90)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=None)
        assert tt.touched is False

    def test_neither_level_configured_is_insufficient_evidence(self):
        bars = [_raw(_D(2), 100, 101, 99, 100)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=None, applicable_target=None)
        order = classify_touch_order(tt, st, applicable_stop=None, applicable_target=None, level_history_complete=True)
        from services.postmortem.price_path_calculator import INSUFFICIENT_EVIDENCE
        assert order == INSUFFICIENT_EVIDENCE

    def test_edited_levels_without_complete_history_is_level_history_incomplete(self):
        """#31 — when only entry+final levels are known (not a full edit
        history), ordering must never pretend the final level applied for
        the whole trade."""
        bars = [_raw(_D(2), 100, 112, 85, 90)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        tt, st = detect_touches(bundle, applicable_stop=88.0, applicable_target=110.0)
        order = classify_touch_order(tt, st, applicable_stop=88.0, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE


@pytest.mark.unit
class TestAcquisitionBarHygiene:
    def test_missing_bars_flagged_partial(self):
        """#17 — provider returns fewer bars than the requested window."""
        bars = [_raw(_D(2), 100, 105, 99, 101)]  # missing day 1, 3, 4
        bundle = _bundle(_TS(1), _TS(4), bars)
        from services.postmortem.price_path_evidence import STATUS_PARTIAL
        assert bundle.data_completeness == STATUS_PARTIAL

    def test_duplicate_bars_deduplicated_not_rejected(self):
        """#18 — the acquisition layer defensively de-duplicates before
        constructing the immutable bundle (which itself would reject a
        true duplicate)."""
        bars = [_raw(_D(2), 100, 105, 99, 101), _raw(_D(2), 100, 106, 99, 102)]  # duplicate date, second wins
        bundle = _bundle(_TS(1), _TS(4), bars)
        matching = [b for b in bundle.bars if b.session_date == _D(2)]
        assert len(matching) == 1
        assert matching[0].high == 106.0  # later row in raw_bars wins

    def test_out_of_order_raw_bars_sorted_before_bundle_construction(self):
        """#19 — a provider returning bars out of order must not produce
        an out-of-order (and therefore rejected) bundle."""
        bars = [_raw(_D(3), 108, 109, 106, 107), _raw(_D(2), 100, 112, 99, 108)]
        bundle = _bundle(_TS(1), _TS(4), bars)
        dates = [b.session_date for b in bundle.bars]
        assert dates == sorted(dates)

    def test_nan_bar_skipped_not_fatal(self):
        """#20 — the documented prediction_engine.py NaN placeholder-row
        bug this codebase already hit once must not crash the whole
        bundle when it appears in price-path acquisition."""
        bars = [
            _raw(_D(2), 100, 105, 99, 101),
            {"date": _D(3), "open": float("nan"), "high": float("nan"), "low": float("nan"), "close": float("nan"), "volume": None},
        ]
        bundle = _bundle(_TS(1), _TS(4), bars)
        assert all(b.session_date != _D(3) for b in bundle.bars)

    def test_weekend_absence_not_flagged_as_missing(self):
        """Non-trading days that the provider simply never returns
        (because they're not trading days) must not be counted as
        'missing' — only unexplained gaps within the observed range."""
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(5), 100, 101, 99, 100)]  # skips weekend 2-4
        bundle = _bundle(_TS(1), _TS(5), bars)
        assert bundle.missing_bar_count is None  # never fabricated as a count


@pytest.mark.unit
class TestDeterministicReplay:
    def test_same_evidence_produces_identical_hash(self):
        """#36"""
        bars = [_raw(_D(2), 100, 112, 99, 108)]
        b1 = _bundle(_TS(1), _TS(4), bars)
        b2 = _bundle(_TS(1), _TS(4), bars)
        assert b1.evidence_hash == b2.evidence_hash

    def test_changed_bar_evidence_changes_hash(self):
        """#37"""
        bars_a = [_raw(_D(2), 100, 112, 99, 108)]
        bars_b = [_raw(_D(2), 100, 999, 99, 108)]
        b1 = _bundle(_TS(1), _TS(4), bars_a)
        b2 = _bundle(_TS(1), _TS(4), bars_b)
        assert b1.evidence_hash != b2.evidence_hash

    def test_exact_insufficient_evidence_sentence(self):
        """#45"""
        from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE
        from services.postmortem.price_path_claims import build_mfe_claim
        bars = []
        bundle = _bundle(dt.datetime(2026, 6, 1, 10, tzinfo=ET), dt.datetime(2026, 6, 1, 15, tzinfo=ET), bars)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=101.0)
        _, claim = build_mfe_claim(1, exc)
        assert claim.claim_text == INSUFFICIENT_EVIDENCE_SENTENCE == "Insufficient evidence to determine this factor reliably."
