"""
Trade Postmortem Sprint 3A, Stages E and G — entry/exit boundary-bar
handling and stop/target level-history completeness. Both are already
enforced architecturally by price_path_calculator.py (interior-only
excursion, boundary-flagged touches, an explicit level_history_complete
parameter feeding LEVEL_HISTORY_INCOMPLETE) — this file proves each of
the checkpoint's enumerated scenarios against that existing design
rather than introducing new logic.

Daily-bar-only acquisition (Stage 1's source decision C) cannot
distinguish "entry before open" from "entry at open" from "entry
mid-session" — all four intraday entry-timing scenarios collapse onto
the identical daily-bar evidence and the identical
ENTRY_BAR_PARTIAL_UNKNOWN policy. This is documented explicitly, not
silently glossed over: see TestEntryTimingCollapsesToSameEvidence.
"""
import datetime as dt

import pytest

from services.postmortem.price_path_calculator import (
    BOTH_SAME_BAR_AMBIGUOUS,
    BOUNDARY_BAR_AMBIGUOUS,
    CURRENT_LEVEL_HISTORY_FINDING,
    EXCURSION_NO_INTERIOR_BARS,
    LEVEL_HISTORY_ENDPOINTS_ONLY,
    LEVEL_HISTORY_INCOMPLETE,
    STOP_ONLY,
    TARGET_ONLY,
    classify_touch_order,
    compute_excursion,
    detect_touches,
)
from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle, STATUS_COMPLETE, UNADJUSTED

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _bar(session_date, *, o, h, l, c):
    return PricePathBar(
        timestamp=dt.datetime(session_date.year, session_date.month, session_date.day, tzinfo=IST),
        interval="1d", open=o, high=h, low=l, close=c, volume=1000.0,
        session_date=session_date, source_id="yfinance_daily",
        adjustment_basis=UNADJUSTED, verification_level="DIRECTLY_OBSERVED",
    )


def _bundle(bars, *, entry_date, exit_date):
    return PricePathEvidenceBundle(
        evidence_bundle_version="1.0.0", paper_trade_id=1, user_id="u", symbol="TCS", market="IN",
        source_id="yfinance_daily", source_type="EXTERNAL_UNOFFICIAL_DAILY", source_version="1.0.0",
        provider_symbol="TCS.NS", price_adjustment_basis=UNADJUSTED, bar_interval="1d",
        market_timezone="Asia/Kolkata",
        entry_timestamp=dt.datetime(entry_date.year, entry_date.month, entry_date.day, 10, tzinfo=IST),
        exit_timestamp=dt.datetime(exit_date.year, exit_date.month, exit_date.day, 15, tzinfo=IST),
        entry_bar_policy="ENTRY_BAR_PARTIAL_UNKNOWN", exit_bar_policy="EXIT_BAR_PARTIAL_UNKNOWN",
        requested_window_start=entry_date, requested_window_end=exit_date,
        observed_window_start=bars[0].session_date if bars else None,
        observed_window_end=bars[-1].session_date if bars else None,
        bars_expected=len(bars), bars_observed=len(bars), missing_bar_count=None,
        data_completeness=STATUS_COMPLETE, freshness_basis="acquired_at_generation_time",
        acquisition_timestamp=dt.datetime(2026, 6, 10, tzinfo=IST), source_manifest={},
        bars=tuple(bars),
    )


@pytest.mark.unit
class TestEntryTimingCollapsesToSameEvidence:
    """Scenarios 1-4 of Stage E's list (entry before open / at open /
    during session / after close) are documented here as a single,
    explicit N/A: daily bars carry no intraday timestamp, so this
    codebase cannot and does not attempt to distinguish them. Every
    entry-day bar receives ENTRY_BAR_PARTIAL_UNKNOWN regardless of
    intraday timing, and is excluded from excursion computation either
    way (see _interior_bars in price_path_calculator.py)."""

    def test_entry_day_bar_never_contributes_to_mfe_or_mae_regardless_of_intraday_timing(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [
            _bar(dt.date(2026, 6, 1), o=100, h=999, l=1, c=100),  # entry-day extreme values would dominate if included
            _bar(dt.date(2026, 6, 3), o=100, h=110, l=95, c=105),
            _bar(dt.date(2026, 6, 5), o=105, h=888, l=2, c=105),  # exit-day extreme values would dominate if included
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=105.0)
        assert exc.mfe_price == 110.0
        assert exc.mae_price == 95.0


@pytest.mark.unit
class TestExitBoundaryScenarios:
    def test_exit_exactly_at_session_close_excluded_from_excursion(self):
        """Scenario 5."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(exit_date, o=100, h=500, l=1, c=100)]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=100.0)
        assert exc.evidence_completeness == EXCURSION_NO_INTERIOR_BARS

    def test_exit_during_session_same_evidence_as_exit_at_close(self):
        """Scenario 6 — collapses onto scenario 5's evidence for the same
        daily-bar-only reason as entry timing."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(exit_date, o=100, h=500, l=1, c=100)]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=100.0)
        assert exc.evidence_completeness == EXCURSION_NO_INTERIOR_BARS


@pytest.mark.unit
class TestSameDayEntryAndExit:
    def test_same_day_trade_has_zero_excursion_evidence(self):
        """Scenario 7."""
        d = dt.date(2026, 6, 1)
        bars = [_bar(d, o=100, h=105, l=98, c=102)]
        bundle = _bundle(bars, entry_date=d, exit_date=d)
        exc = compute_excursion(bundle, entry_price=100.0, exit_price=102.0)
        assert exc.evidence_completeness == EXCURSION_NO_INTERIOR_BARS


@pytest.mark.unit
class TestTouchesOnBoundaryBars:
    def test_target_touched_only_on_entry_boundary_bar_is_flagged_ambiguous(self):
        """Scenario 8."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [
            _bar(entry_date, o=100, h=115, l=99, c=101),  # target=110 touched here, on the entry boundary bar
            _bar(dt.date(2026, 6, 2), o=101, h=103, l=100, c=102),
            _bar(exit_date, o=102, h=104, l=101, c=103),
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=90.0, applicable_target=110.0)
        assert target_touch.touched is True
        assert target_touch.is_boundary_bar is True
        order = classify_touch_order(
            target_touch, stop_touch, applicable_stop=90.0, applicable_target=110.0, level_history_complete=True,
        )
        assert order == TARGET_ONLY  # single-sided touch, boundary flag preserved on the TouchResult itself

    def test_stop_touched_only_on_entry_boundary_bar_is_flagged_ambiguous(self):
        """Scenario 9."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [
            _bar(entry_date, o=100, h=101, l=85, c=99),  # stop=90 touched here, on the entry boundary bar
            _bar(dt.date(2026, 6, 2), o=99, h=101, l=97, c=100),
            _bar(exit_date, o=100, h=102, l=99, c=101),
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=90.0, applicable_target=110.0)
        assert stop_touch.touched is True
        assert stop_touch.is_boundary_bar is True
        order = classify_touch_order(
            target_touch, stop_touch, applicable_stop=90.0, applicable_target=110.0, level_history_complete=True,
        )
        assert order == STOP_ONLY

    def test_both_touched_on_same_boundary_bar_is_ambiguous(self):
        """Scenario 10."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [
            _bar(entry_date, o=100, h=115, l=85, c=100),  # both stop and target crossed on the entry boundary bar
            _bar(dt.date(2026, 6, 2), o=100, h=101, l=99, c=100),
            _bar(exit_date, o=100, h=102, l=99, c=101),
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=90.0, applicable_target=110.0)
        order = classify_touch_order(
            target_touch, stop_touch, applicable_stop=90.0, applicable_target=110.0, level_history_complete=True,
        )
        assert order == BOTH_SAME_BAR_AMBIGUOUS

    def test_extreme_only_on_exit_boundary_bar_is_flagged(self):
        """Scenario 11."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 3)
        bars = [
            _bar(entry_date, o=100, h=101, l=99, c=100),
            _bar(dt.date(2026, 6, 2), o=100, h=102, l=99, c=101),
            _bar(exit_date, o=101, h=115, l=100, c=110),  # target touched only on the exit boundary bar
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=90.0, applicable_target=110.0)
        assert target_touch.touched is True
        assert target_touch.is_boundary_bar is True

    def test_interior_complete_session_bars_remain_valid_non_boundary(self):
        """Scenario 12."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [
            _bar(entry_date, o=100, h=101, l=99, c=100),
            _bar(dt.date(2026, 6, 3), o=100, h=115, l=99, c=110),  # interior — a clean, unambiguous touch
            _bar(exit_date, o=110, h=111, l=109, c=110),
        ]
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=90.0, applicable_target=110.0)
        assert target_touch.touched is True
        assert target_touch.is_boundary_bar is False


@pytest.mark.unit
class TestLevelHistoryCompleteness:
    """Stage G — 10 enumerated scenarios. level_history_complete is a
    caller-supplied fact (this codebase's own exit_snapshot only stores
    final stop/target values today — see module docstring in
    price_path_calculator.py — so callers integrating this in Stage H
    will almost always pass level_history_complete=False for real
    trades, per the checkpoint's own honest expectation)."""

    def test_current_finding_is_endpoints_only_not_full_history(self):
        assert CURRENT_LEVEL_HISTORY_FINDING == LEVEL_HISTORY_ENDPOINTS_ONLY

    def _touches(self, entry_date, exit_date, bars, stop, target):
        bundle = _bundle(bars, entry_date=entry_date, exit_date=exit_date)
        return detect_touches(bundle, applicable_stop=stop, applicable_target=target)

    def test_unchanged_stop_and_target_full_history_known(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 3), o=100, h=110, l=99, c=105), _bar(exit_date, o=105, h=106, l=104, c=105)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=110.0, level_history_complete=True)
        assert order == TARGET_ONLY

    def test_only_entry_stop_known_history_incomplete(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 3), o=100, h=110, l=99, c=105), _bar(exit_date, o=105, h=106, l=104, c=105)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_only_final_stop_known_history_incomplete(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 3), o=100, h=110, l=99, c=105), _bar(exit_date, o=105, h=106, l=104, c=105)]
        tt, st = self._touches(entry_date, exit_date, bars, 95.0, 110.0)  # a DIFFERENT (final) stop than "entry" case
        order = classify_touch_order(tt, st, applicable_stop=95.0, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_stop_edited_without_timestamp_treated_as_incomplete(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(exit_date, o=100, h=101, l=95, c=100)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, None)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=None, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_target_edited_without_timestamp_treated_as_incomplete(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(exit_date, o=100, h=111, l=99, c=100)]
        tt, st = self._touches(entry_date, exit_date, bars, None, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=None, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_both_edited_treated_as_incomplete(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(exit_date, o=100, h=111, l=95, c=100)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_missing_stop_with_complete_history_is_target_only_or_insufficient(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 3), o=100, h=110, l=99, c=105), _bar(exit_date, o=105, h=106, l=104, c=105)]
        tt, st = self._touches(entry_date, exit_date, bars, None, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=None, applicable_target=110.0, level_history_complete=True)
        assert order == TARGET_ONLY

    def test_missing_target_with_complete_history_is_stop_only_or_insufficient(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 3), o=100, h=101, l=85, c=95), _bar(exit_date, o=95, h=96, l=94, c=95)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, None)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=None, level_history_complete=True)
        assert order == STOP_ONLY

    def test_final_target_would_appear_touched_before_established_blocked_by_incomplete_flag(self):
        """A bar that WOULD show a target touch is present, but since the
        target's applicability history isn't known, the honest ordering
        result is LEVEL_HISTORY_INCOMPLETE — never a false TARGET_ONLY
        claim that the final target applied for the whole window."""
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 2), o=100, h=112, l=99, c=110), _bar(exit_date, o=110, h=111, l=109, c=110)]
        tt, st = self._touches(entry_date, exit_date, bars, None, 110.0)
        order = classify_touch_order(tt, st, applicable_stop=None, applicable_target=110.0, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_final_stop_would_appear_touched_before_established_blocked_by_incomplete_flag(self):
        entry_date, exit_date = dt.date(2026, 6, 1), dt.date(2026, 6, 5)
        bars = [_bar(entry_date, o=100, h=101, l=99, c=100), _bar(dt.date(2026, 6, 2), o=100, h=101, l=88, c=90), _bar(exit_date, o=90, h=91, l=89, c=90)]
        tt, st = self._touches(entry_date, exit_date, bars, 90.0, None)
        order = classify_touch_order(tt, st, applicable_stop=90.0, applicable_target=None, level_history_complete=False)
        assert order == LEVEL_HISTORY_INCOMPLETE
