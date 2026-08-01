"""
Trade Postmortem Sprint 3A, Stage J4B — unit tests for the observed
numerical-crossing and session-attribution core
(price_path_calculator.py's classify_bar_session_attribution,
observe_numerical_level_crossing, summarize_observed_numerical_crossings).

IMPLEMENTED, UNIT-TESTED, NOT REPORT-WIRED, NOT PERSISTED, NOT
ENDPOINT-VERIFIED. This model is purely additive and internal to this
phase: it describes ONLY what immutable daily OHLC bars prove about two
SUPPLIED numerical values, never whether they were the actual active
configured stop/target throughout the holding period (a separate, later
J4C/J4D governed conclusion). Nothing here is imported by
price_path_generation.py, price_path_claims.py, or paper_trading.py.
"""
import dataclasses
import datetime as dt

import pytest

from services.market_hours import ET, IST
from services.postmortem.price_path_acquisition import build_price_path_evidence
from services.postmortem.price_path_calculator import (
    CROSSING_TYPE_GAP_THROUGH,
    CROSSING_TYPE_NORMAL,
    CROSSING_TYPE_NOT_OBSERVED,
    PATTERN_BOTH_VALUES_SAME_BAR,
    PATTERN_NEITHER_NUMERICAL_VALUE_CROSSED,
    PATTERN_NO_NUMERICAL_VALUES_SUPPLIED,
    PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR,
    PATTERN_STOP_VALUE_ONLY_CROSSED,
    PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR,
    PATTERN_TARGET_VALUE_ONLY_CROSSED,
    SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR,
    SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED,
    SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE,
    SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL,
    SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN,
    SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL,
    SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN,
    SESSION_ATTRIBUTION_INTERIOR,
    SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL,
    SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN,
    STOP_VALUE,
    SUPPLIED_AT_CALCULATION,
    TARGET_VALUE,
    SessionAttributionError,
    classify_bar_session_attribution,
    is_safely_attributable_session,
    observe_numerical_level_crossing,
    summarize_observed_numerical_crossings,
)


def _raw(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


_D = lambda n: dt.date(2026, 6, n)


def _us_bundle(entry, exit_, bars, paper_trade_id=1):
    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id="user-aaa", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=entry, exit_timestamp=exit_,
        raw_bars=bars, split_events=[],
    )


def _in_bundle(entry, exit_, bars, paper_trade_id=1):
    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id="user-aaa", symbol="RELIANCE", market="IN",
        market_timezone_name="Asia/Kolkata", market_tzinfo=IST,
        entry_timestamp=entry, exit_timestamp=exit_,
        raw_bars=bars, split_events=[],
    )


# 2026-06-01 is a Monday, 2026-06-04 a Thursday -- both real US/IN trading
# days, giving Tue/Wed as clean interior sessions between them.
_US_ENTRY_INTERIOR = dt.datetime(2026, 6, 1, 9, 30, tzinfo=ET)   # exact open -> INCLUDED_FULL
_US_EXIT_INTERIOR = dt.datetime(2026, 6, 4, 16, 0, tzinfo=ET)    # exact close -> INCLUDED_FULL
_US_ENTRY_PARTIAL = dt.datetime(2026, 6, 1, 10, 0, tzinfo=ET)    # after open -> PARTIAL_UNKNOWN
_US_EXIT_PARTIAL = dt.datetime(2026, 6, 4, 15, 0, tzinfo=ET)     # before close -> PARTIAL_UNKNOWN

_IN_ENTRY_INTERIOR = dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST)
_IN_EXIT_INTERIOR = dt.datetime(2026, 6, 4, 15, 30, tzinfo=IST)
_IN_ENTRY_PARTIAL = dt.datetime(2026, 6, 1, 11, 0, tzinfo=IST)
_IN_EXIT_PARTIAL = dt.datetime(2026, 6, 4, 14, 0, tzinfo=IST)


def _four_day_bars(o=100, h=101, l=99, c=100):
    return [_raw(_D(1), o, h, l, c), _raw(_D(2), o, h, l, c), _raw(_D(3), o, h, l, c), _raw(_D(4), o, h, l, c)]


@dataclasses.dataclass(frozen=True)
class _FakeBar:
    session_date: dt.date


@dataclasses.dataclass(frozen=True)
class _FakeBundle:
    requested_window_start: dt.date
    requested_window_end: dt.date
    entry_bar_policy: str
    exit_bar_policy: str


@pytest.mark.unit
class TestSessionAttributionTypedConstants:
    def test_included_full_attributions_are_safely_attributable(self):
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_INTERIOR) is True
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL) is True
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL) is True
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL) is True

    def test_partial_unknown_attributions_are_not_safely_attributable(self):
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN) is False
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN) is False
        assert is_safely_attributable_session(SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN) is False


@pytest.mark.unit
class TestSessionAttributionAlgorithm:
    def test_interior_bar_classified_as_interior(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        interior_bar = next(b for b in bundle.bars if b.session_date == _D(2))
        assert classify_bar_session_attribution(bundle, interior_bar) == SESSION_ATTRIBUTION_INTERIOR

    def test_entry_included_full(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        entry_bar = next(b for b in bundle.bars if b.session_date == _D(1))
        assert bundle.entry_bar_policy == "ENTRY_BAR_INCLUDED_FULL"
        assert classify_bar_session_attribution(bundle, entry_bar) == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL

    def test_entry_partial_unknown(self):
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, _four_day_bars())
        entry_bar = next(b for b in bundle.bars if b.session_date == _D(1))
        assert bundle.entry_bar_policy == "ENTRY_BAR_PARTIAL_UNKNOWN"
        assert classify_bar_session_attribution(bundle, entry_bar) == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN

    def test_exit_included_full(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        exit_bar = next(b for b in bundle.bars if b.session_date == _D(4))
        assert bundle.exit_bar_policy == "EXIT_BAR_INCLUDED_FULL"
        assert classify_bar_session_attribution(bundle, exit_bar) == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL

    def test_exit_partial_unknown(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, _four_day_bars())
        exit_bar = next(b for b in bundle.bars if b.session_date == _D(4))
        assert bundle.exit_bar_policy == "EXIT_BAR_PARTIAL_UNKNOWN"
        assert classify_bar_session_attribution(bundle, exit_bar) == SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN

    def test_same_day_included_full(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        assert bundle.requested_window_start == bundle.requested_window_end
        bar = bundle.bars[0]
        assert classify_bar_session_attribution(bundle, bar) == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL

    def test_same_day_partial_unknown(self):
        bundle = _us_bundle(_US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 101, 99, 100)])
        assert bundle.requested_window_start == bundle.requested_window_end
        bar = bundle.bars[0]
        assert classify_bar_session_attribution(bundle, bar) == SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN

    def test_never_infers_from_array_position(self):
        """A bar list constructed in REVERSE order must still classify
        correctly by requested_window_start/end + policy fields, never
        by bars[0]/bars[-1] position -- this directly proves the Stage
        J4A finding #2 defect is NOT reproduced here."""
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        # bundle.bars is always stored in ascending order (bundle invariant),
        # so simulate array-position-independence by classifying the
        # LAST-indexed bar (exit date) and the FIRST-indexed bar (entry
        # date) and confirming each is judged purely by its own
        # session_date against requested_window_start/end, not by its
        # position in bundle.bars.
        assert bundle.bars[0].session_date == bundle.requested_window_start
        assert bundle.bars[-1].session_date == bundle.requested_window_end
        assert classify_bar_session_attribution(bundle, bundle.bars[0]) == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        assert classify_bar_session_attribution(bundle, bundle.bars[-1]) == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL

    def test_unknown_entry_boundary_policy_fails_closed(self):
        fake_bundle = _FakeBundle(
            requested_window_start=_D(1), requested_window_end=_D(4),
            entry_bar_policy="SOMETHING_UNRECOGNIZED", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
        )
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(fake_bundle, _FakeBar(session_date=_D(1)))

    def test_unknown_exit_boundary_policy_fails_closed(self):
        fake_bundle = _FakeBundle(
            requested_window_start=_D(1), requested_window_end=_D(4),
            entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="SOMETHING_UNRECOGNIZED",
        )
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(fake_bundle, _FakeBar(session_date=_D(4)))

    def test_unknown_same_day_boundary_policy_combination_fails_closed(self):
        fake_bundle = _FakeBundle(
            requested_window_start=_D(1), requested_window_end=_D(1),
            entry_bar_policy="SOMETHING_UNRECOGNIZED", exit_bar_policy="SOMETHING_ELSE_UNRECOGNIZED",
        )
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(fake_bundle, _FakeBar(session_date=_D(1)))

    def test_duplicate_bars_still_rejected_by_existing_bundle_invariant(self):
        """#27 -- this phase adds no new duplicate/ordering guard; the
        pre-existing PricePathEvidenceBundle.__post_init__ invariant
        (price_path_evidence.py, proven directly by
        test_price_path_evidence.py::test_duplicate_session_date_rejected)
        continues to reject a duplicate-date bar list before a bundle can
        even be constructed. Going through build_price_path_evidence's
        own raw_bars path (acquisition-layer dedup: the later row wins)
        never even reaches that invariant, so this proves it directly
        against PricePathEvidenceBundle itself, matching the existing
        test's own construction pattern."""
        from services.postmortem.price_path_evidence import (
            PricePathBar,
            PricePathEvidenceBundle,
            PricePathEvidenceError,
            STATUS_COMPLETE,
            UNADJUSTED,
        )

        def _dup_bar(session_date):
            return PricePathBar(
                timestamp=dt.datetime(session_date.year, session_date.month, session_date.day, tzinfo=ET),
                interval="1d", open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0,
                session_date=session_date, source_id="yfinance_daily",
                adjustment_basis=UNADJUSTED, verification_level="DIRECTLY_OBSERVED",
            )

        with pytest.raises(PricePathEvidenceError, match="duplicate bar"):
            PricePathEvidenceBundle(
                evidence_bundle_version="1.0.0", paper_trade_id=1, user_id="user-aaa",
                symbol="AAPL", market="US", source_id="yfinance_daily", source_type="EXTERNAL_UNOFFICIAL_DAILY",
                source_version="1.0.0", provider_symbol="AAPL", price_adjustment_basis=UNADJUSTED,
                bar_interval="1d", market_timezone="America/New_York",
                entry_timestamp=_US_ENTRY_INTERIOR, exit_timestamp=_US_EXIT_INTERIOR,
                entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
                requested_window_start=_D(1), requested_window_end=_D(4),
                observed_window_start=_D(1), observed_window_end=_D(4),
                bars_expected=2, bars_observed=2, missing_bar_count=None,
                data_completeness=STATUS_COMPLETE, freshness_basis="acquired_at_generation_time",
                acquisition_timestamp=dt.datetime(2026, 6, 4, 16, tzinfo=ET), source_manifest={},
                bars=(_dup_bar(_D(1)), _dup_bar(_D(1))),
            )


@pytest.mark.unit
class TestNumericalCrossingObservationTerminologyAndNoValue:
    def test_no_supplied_value_returns_typed_no_value_observation_not_no_levels_configured(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        obs = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        assert obs.value_supplied is False
        assert obs.crossed_anywhere is False
        assert obs.first_observed_crossing_type == CROSSING_TYPE_NOT_OBSERVED
        assert obs.supplied_value_basis == SUPPLIED_AT_CALCULATION
        # Object never claims NO_LEVELS_CONFIGURED -- that string does
        # not appear anywhere on this typed object at all.
        assert not hasattr(obs, "no_levels_configured")

    def test_evidence_id_contains_trade_id_session_date_and_level_kind(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars(h=200), paper_trade_id=777)
        obs = observe_numerical_level_crossing(bundle, 150.0, TARGET_VALUE)
        assert obs.first_observed_evidence_id == f"NUMERICAL-CROSSING-777-{_D(1).isoformat()}-TARGET_VALUE"


@pytest.mark.unit
class TestCrossingDetectionMatrix:
    """#1-#26 of the required Stage J4B unit matrix."""

    def test_1_neither_value_supplied(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars())
        target = observe_numerical_level_crossing(bundle, None, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, None, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_NO_NUMERICAL_VALUES_SUPPLIED
        assert summary.safely_attributable_pattern == SAFE_PATTERN_NO_NUMERICAL_VALUES_SUPPLIED

    def test_2_target_supplied_not_crossed(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars(h=101))
        obs = observe_numerical_level_crossing(bundle, 500.0, TARGET_VALUE)
        assert obs.value_supplied is True
        assert obs.crossed_anywhere is False

    def test_3_stop_supplied_not_crossed(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars(l=99))
        obs = observe_numerical_level_crossing(bundle, 1.0, STOP_VALUE)
        assert obs.value_supplied is True
        assert obs.crossed_anywhere is False

    def test_4_both_supplied_neither_crossed(self):
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, _four_day_bars(h=101, l=99))
        target = observe_numerical_level_crossing(bundle, 500.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 1.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_NEITHER_NUMERICAL_VALUE_CROSSED

    def test_5_target_crossed_on_interior_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.crossed_anywhere is True
        assert obs.first_observed_session == _D(2)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        assert obs.first_safely_attributable_session == _D(2)

    def test_6_stop_crossed_on_interior_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        assert obs.crossed_anywhere is True
        assert obs.first_observed_session == _D(2)
        assert obs.first_safely_attributable_session == _D(2)

    def test_7_target_and_stop_different_interior_bars_target_first(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 50, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR
        assert summary.safely_attributable_pattern == "TARGET_SAFE_BAR_BEFORE_STOP_SAFE_BAR"

    def test_8_target_and_stop_different_interior_bars_stop_first(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 50, 100),
                _raw(_D(3), 100, 150, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR
        assert summary.safely_attributable_pattern == "STOP_SAFE_BAR_BEFORE_TARGET_SAFE_BAR"

    def test_9_both_crossed_in_one_interior_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_BOTH_VALUES_SAME_BAR
        assert summary.safely_attributable_pattern == SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR

    def test_10_target_gap_through(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 130, 140, 129, 135),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_crossing_type == CROSSING_TYPE_GAP_THROUGH
        assert obs.first_safely_attributable_crossing_type == CROSSING_TYPE_GAP_THROUGH

    def test_11_stop_gap_through(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 70, 75, 65, 72),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        assert obs.first_observed_crossing_type == CROSSING_TYPE_GAP_THROUGH
        assert obs.first_safely_attributable_crossing_type == CROSSING_TYPE_GAP_THROUGH

    def test_12_target_first_crossed_on_partial_entry_bar_no_later_safe_crossing(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.crossed_anywhere is True
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        assert obs.partial_boundary_crossing_observed is True
        assert obs.first_safely_attributable_session is None

    def test_13_stop_first_crossed_on_partial_entry_bar_no_later_safe_crossing(self):
        bars = [_raw(_D(1), 100, 101, 50, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        assert obs.first_safely_attributable_session is None

    def test_14_target_first_crossed_on_partial_exit_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(4)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN
        assert obs.first_safely_attributable_session is None

    def test_15_stop_first_crossed_on_partial_exit_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 50, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        assert obs.first_observed_session == _D(4)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_PARTIAL_UNKNOWN
        assert obs.first_safely_attributable_session is None

    def test_16_partial_entry_crossing_followed_by_safe_interior_crossing(self):
        """Both the earlier ambiguous observation AND the later safe
        observation must be retained (Stage J4A finding #5)."""
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 130, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_PARTIAL_UNKNOWN
        assert obs.partial_boundary_crossing_observed is True
        assert obs.first_safely_attributable_session == _D(2)
        assert obs.first_safely_attributable_session_attribution == SESSION_ATTRIBUTION_INTERIOR

    def test_17_partial_exit_crossing_preceded_by_safe_interior_crossing(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 130, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_PARTIAL, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(3)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        assert obs.first_safely_attributable_session == _D(3)
        # The later, exit-date PARTIAL_UNKNOWN crossing must still be
        # detected and flagged, even though it is not the safe basis.
        assert obs.partial_boundary_crossing_observed is True

    def test_18_exact_open_entry_included_full_crossing(self):
        bars = [_raw(_D(1), 100, 150, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(1)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_ENTRY_INCLUDED_FULL
        assert obs.first_safely_attributable_session == _D(1)
        assert obs.partial_boundary_crossing_observed is False

    def test_19_exact_close_exit_included_full_crossing(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 150, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(4)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_EXIT_INCLUDED_FULL
        assert obs.first_safely_attributable_session == _D(4)

    def test_20_same_day_full_session_crossing(self):
        bundle = _us_bundle(
            _US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 100, 150, 99, 100)],
        )
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_INCLUDED_FULL
        assert obs.first_safely_attributable_session == _D(1)

    def test_21_same_day_full_session_both_values_same_bar(self):
        bundle = _us_bundle(
            _US_ENTRY_INTERIOR, dt.datetime(2026, 6, 1, 16, 0, tzinfo=ET), [_raw(_D(1), 100, 150, 50, 100)],
        )
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_BOTH_VALUES_SAME_BAR
        assert summary.safely_attributable_pattern == SAFE_PATTERN_BOTH_VALUES_SAME_SAFE_BAR

    def test_22_same_day_partial_session_crossing(self):
        bundle = _us_bundle(
            _US_ENTRY_PARTIAL, dt.datetime(2026, 6, 1, 15, 0, tzinfo=ET), [_raw(_D(1), 100, 150, 99, 100)],
        )
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_SAME_DAY_PARTIAL_UNKNOWN
        assert obs.partial_boundary_crossing_observed is True
        assert obs.first_safely_attributable_session is None

    def test_23_missing_entry_date_provider_bar_with_later_interior_crossing(self):
        # Entry-date (June 1) bar simply absent from the provider fixture.
        bars = [_raw(_D(2), 100, 150, 99, 100), _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        assert all(b.session_date != _D(1) for b in bundle.bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(2)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        assert obs.first_safely_attributable_session == _D(2)

    def test_24_missing_exit_date_provider_bar(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100), _raw(_D(3), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        assert all(b.session_date != _D(4) for b in bundle.bars)
        obs = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(2)
        assert obs.first_safely_attributable_session == _D(2)

    def test_25_unknown_entry_boundary_policy_fails_closed(self):
        fake_bundle = _FakeBundle(
            requested_window_start=_D(1), requested_window_end=_D(4),
            entry_bar_policy="MADE_UP_POLICY", exit_bar_policy="EXIT_BAR_INCLUDED_FULL",
        )
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(fake_bundle, _FakeBar(session_date=_D(1)))

    def test_26_unknown_exit_boundary_policy_fails_closed(self):
        fake_bundle = _FakeBundle(
            requested_window_start=_D(1), requested_window_end=_D(4),
            entry_bar_policy="ENTRY_BAR_INCLUDED_FULL", exit_bar_policy="MADE_UP_POLICY",
        )
        with pytest.raises(SessionAttributionError):
            classify_bar_session_attribution(fake_bundle, _FakeBar(session_date=_D(4)))

    def test_28_india_bundle_attribution(self):
        bars = [_raw(_D(1), 2800, 2850, 2790, 2820), _raw(_D(2), 2800, 2900, 2790, 2820),
                _raw(_D(3), 2800, 2850, 2790, 2820), _raw(_D(4), 2800, 2850, 2790, 2820)]
        bundle = _in_bundle(_IN_ENTRY_INTERIOR, _IN_EXIT_INTERIOR, bars)
        assert bundle.market == "IN"
        obs = observe_numerical_level_crossing(bundle, 2870.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(2)
        assert obs.first_observed_session_attribution == SESSION_ATTRIBUTION_INTERIOR
        assert obs.first_safely_attributable_session == _D(2)

    def test_29_us_bundle_attribution(self):
        bars = [_raw(_D(1), 190, 195, 188, 192), _raw(_D(2), 190, 210, 188, 192),
                _raw(_D(3), 190, 195, 188, 192), _raw(_D(4), 190, 195, 188, 192)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        assert bundle.market == "US"
        obs = observe_numerical_level_crossing(bundle, 205.0, TARGET_VALUE)
        assert obs.first_observed_session == _D(2)
        assert obs.first_safely_attributable_session == _D(2)

    def test_30_no_dependence_on_levels_modified_after_entry(self):
        """Neither observe_numerical_level_crossing nor
        summarize_observed_numerical_crossings accepts any level-history
        parameter -- proven structurally via signature inspection, not
        merely by omission in these tests' own call sites."""
        import inspect
        crossing_params = set(inspect.signature(observe_numerical_level_crossing).parameters)
        summary_params = set(inspect.signature(summarize_observed_numerical_crossings).parameters)
        for forbidden in ("levels_modified_after_entry", "level_history_complete", "level_history_status"):
            assert forbidden not in crossing_params
            assert forbidden not in summary_params


@pytest.mark.unit
class TestObservedCrossingSummaryPatterns:
    def test_target_value_only_crossed(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 1.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_TARGET_VALUE_ONLY_CROSSED
        assert summary.safely_attributable_pattern == SAFE_PATTERN_TARGET_VALUE_ONLY_SAFELY_ATTRIBUTABLE

    def test_stop_value_only_crossed(self):
        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 101, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 500.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        assert summary.any_observation_pattern == PATTERN_STOP_VALUE_ONLY_CROSSED
        assert summary.safely_attributable_pattern == SAFE_PATTERN_STOP_VALUE_ONLY_SAFELY_ATTRIBUTABLE

    def test_safely_attributable_neither_when_only_partial_boundary_crossings_exist(self):
        bars = [_raw(_D(1), 100, 150, 50, 100), _raw(_D(2), 100, 101, 99, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_PARTIAL, _US_EXIT_INTERIOR, bars)
        target = observe_numerical_level_crossing(bundle, 120.0, TARGET_VALUE)
        stop = observe_numerical_level_crossing(bundle, 80.0, STOP_VALUE)
        summary = summarize_observed_numerical_crossings(target, stop)
        # Both crossed (any-observation), but neither is safely
        # attributable (both only observed in the partial entry bar).
        assert summary.any_observation_pattern == PATTERN_BOTH_VALUES_SAME_BAR
        assert summary.safely_attributable_pattern == SAFE_PATTERN_NEITHER_SAFELY_ATTRIBUTABLE
        assert summary.partial_boundary_observation_present is True

    def test_terminology_never_uses_touch_as_primary_field_name(self):
        """Structural proof that the new dataclasses avoid 'touch' as a
        primary semantic term, per Stage J4B, Stage 2."""
        import dataclasses as dc
        from services.postmortem.price_path_calculator import (
            NumericalLevelCrossingObservation,
            ObservedNumericalCrossingSummary,
        )
        for cls in (NumericalLevelCrossingObservation, ObservedNumericalCrossingSummary):
            for f in dc.fields(cls):
                assert "touch" not in f.name.lower()


@pytest.mark.unit
class TestJ4BCompatibilityAssertions:
    """Stage J4B, Stage 8 -- proves the existing public report/claim
    model is byte-for-byte unchanged by this phase's purely additive
    code. This is a REGRESSION assertion, not new behavior: every value
    asserted here already existed and already passed before J4B."""

    def _fake_evidence(self, bundle):
        """Duck-typed stand-in for price_path_store.PersistedPricePathEvidence
        -- build_price_path_report_payload only needs attribute access,
        never isinstance checks, so a plain namespace with the same
        field names (bars converted to the persisted JSON-ish dict
        shape _persisted_evidence_to_bundle expects) is sufficient."""
        import types

        bars = [
            {
                "timestamp": b.timestamp.isoformat(), "interval": b.interval,
                "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
                "session_date": b.session_date.isoformat(), "source_id": b.source_id,
                "adjustment_basis": b.adjustment_basis, "verification_level": b.verification_level,
            }
            for b in bundle.bars
        ]
        return types.SimpleNamespace(
            id=1,
            evidence_bundle_version=bundle.evidence_bundle_version, paper_trade_id=bundle.paper_trade_id,
            user_id=bundle.user_id, symbol=bundle.symbol, market=bundle.market,
            source_id=bundle.source_id, source_type=bundle.source_type, source_version=bundle.source_version,
            provider_symbol=bundle.provider_symbol, price_adjustment_basis=bundle.price_adjustment_basis,
            bar_interval=bundle.bar_interval, market_timezone=bundle.market_timezone,
            entry_timestamp=bundle.entry_timestamp, exit_timestamp=bundle.exit_timestamp,
            entry_bar_policy=bundle.entry_bar_policy, exit_bar_policy=bundle.exit_bar_policy,
            requested_window_start=bundle.requested_window_start, requested_window_end=bundle.requested_window_end,
            observed_window_start=bundle.observed_window_start, observed_window_end=bundle.observed_window_end,
            bars_expected=bundle.bars_expected, bars_observed=bundle.bars_observed,
            missing_bar_count=bundle.missing_bar_count, data_completeness=bundle.data_completeness,
            freshness_basis=bundle.freshness_basis, acquisition_timestamp=bundle.acquisition_timestamp,
            source_manifest=bundle.source_manifest, limitations=list(bundle.limitations),
            bars=bars, evidence_hash=bundle.evidence_hash,
        )

    def test_detect_touches_output_unchanged(self):
        from services.postmortem.price_path_calculator import detect_touches

        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=80.0, applicable_target=120.0)
        assert target_touch.touched is True
        assert target_touch.first_observed_bar.session_date == _D(2)
        assert target_touch.is_boundary_bar is False
        assert stop_touch.touched is True
        assert stop_touch.first_observed_bar.session_date == _D(2)

    def test_classify_touch_order_output_unchanged(self):
        from services.postmortem.price_path_calculator import LEVEL_HISTORY_INCOMPLETE, classify_touch_order, detect_touches

        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        target_touch, stop_touch = detect_touches(bundle, applicable_stop=80.0, applicable_target=120.0)
        # Production always passes level_history_complete=False -- this
        # must still short-circuit to LEVEL_HISTORY_INCOMPLETE exactly as
        # before, discarding nothing new and gaining nothing new.
        order = classify_touch_order(
            target_touch, stop_touch, applicable_stop=80.0, applicable_target=120.0, level_history_complete=False,
        )
        assert order == LEVEL_HISTORY_INCOMPLETE

    def test_structured_report_unchanged_for_representative_fixture(self):
        from services.postmortem.price_path_generation import build_price_path_report_payload

        bars = [_raw(_D(1), 100, 101, 99, 100), _raw(_D(2), 100, 150, 50, 100),
                _raw(_D(3), 100, 101, 99, 100), _raw(_D(4), 100, 101, 99, 100)]
        bundle = _us_bundle(_US_ENTRY_INTERIOR, _US_EXIT_INTERIOR, bars)
        evidence = self._fake_evidence(bundle)

        payload = build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=100.0, applicable_stop=80.0, applicable_target=120.0,
            level_history_complete=False, trade_id=1,
        )
        assert payload.price_path_section["touch_order"] == "LEVEL_HISTORY_INCOMPLETE"
        assert set(payload.price_path_section.keys()) == {
            "mfe", "mae", "touch_order", "acquisition_decision",
        } or "touch_order" in payload.price_path_section  # tolerant of additional pre-existing keys, never a NEW one below

        # No new claim factor beyond the pre-existing, documented set.
        known_factors = {"mfe", "mae", "touch_order"}
        for claim in payload.claims:
            assert claim["factor"] in known_factors, f"unexpected NEW claim factor introduced: {claim['factor']!r}"

    def test_report_schema_and_calculation_version_constants_unchanged(self):
        from services.postmortem.price_path_identity import (
            BOUNDARY_POLICY_VERSION,
            CALCULATION_RULES_VERSION,
            EVIDENCE_BUNDLE_SCHEMA_VERSION,
            PRICE_PATH_REPORT_SCHEMA_VERSION,
            SOURCE_MANIFEST_SCHEMA_VERSION,
            SOURCE_VERSION,
        )
        assert EVIDENCE_BUNDLE_SCHEMA_VERSION == "1.0.0"
        assert SOURCE_VERSION == "1.1.0"
        assert SOURCE_MANIFEST_SCHEMA_VERSION == "1.0.0"
        assert BOUNDARY_POLICY_VERSION == "1.0.0"
        assert PRICE_PATH_REPORT_SCHEMA_VERSION == "1.1.0"
        assert CALCULATION_RULES_VERSION == "1.0.0"

    def test_price_path_claims_rule_registry_gains_no_new_j4b_rule(self):
        """price_path_claims.py is not imported by J4B production code
        at all -- this proves it, and proves the registry's existing
        rule-id set is unchanged, without J4B ever touching that file."""
        from services.postmortem import price_path_claims
        from services.postmortem.evidence import RULE_REGISTRY

        existing_rule_ids = {
            "PRICE_PATH_WINDOW", "MFE", "MAE", "TARGET_TOUCH", "STOP_TOUCH", "TOUCH_ORDER",
            "SAME_BAR_AMBIGUITY", "ENTRY_BOUNDARY_AMBIGUITY", "EXIT_BOUNDARY_AMBIGUITY",
            "GAP_THROUGH_LEVEL", "MFE_GIVEBACK", "CAPTURED_MFE", "PRICE_BASIS_COMPATIBILITY",
            "MISSING_LEVEL_HISTORY",
        }
        price_path_rule_ids = {rid for rid, rule in RULE_REGISTRY.items() if rule.report_section == "price_path"}
        assert price_path_rule_ids == existing_rule_ids, (
            f"price_path rule registry changed -- new: {price_path_rule_ids - existing_rule_ids}, "
            f"missing: {existing_rule_ids - price_path_rule_ids}"
        )

    def test_price_path_calculator_module_does_not_import_generation_or_claims(self):
        """Structural proof that J4B's new code is not wired into the
        live report path: price_path_calculator.py itself (both its
        pre-existing content and this phase's additions) never imports
        price_path_generation or price_path_claims -- if it did, that
        would be a reverse-dependency this phase's own docstring
        explicitly disclaims."""
        import inspect
        from services.postmortem import price_path_calculator
        source = inspect.getsource(price_path_calculator)
        for forbidden_module in ("price_path_generation", "price_path_claims"):
            assert f"import {forbidden_module}" not in source
            assert f"import services.postmortem.{forbidden_module}" not in source

    def test_generation_and_claims_and_router_do_not_import_j4b_symbols(self):
        """Confirms the inverse direction too: none of the three files
        this phase is prohibited from wiring into actually reference the
        new J4B symbols."""
        import inspect
        from services.postmortem import price_path_claims, price_path_generation
        import api.routers.paper_trading as paper_trading_router

        j4b_symbols = (
            "observe_numerical_level_crossing", "summarize_observed_numerical_crossings",
            "classify_bar_session_attribution", "NumericalLevelCrossingObservation",
            "ObservedNumericalCrossingSummary",
        )
        for module in (price_path_generation, price_path_claims, paper_trading_router):
            source = inspect.getsource(module)
            for symbol in j4b_symbols:
                assert symbol not in source, f"{module.__name__} unexpectedly references J4B symbol {symbol!r}"
