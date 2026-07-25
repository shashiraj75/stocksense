"""Unit + property tests for relative_strength.py (pure functions, no I/O)."""
import numpy as np
import pandas as pd
import pytest

from services.market_leadership.contracts import DataQualityStatus, RsTrend
from services.market_leadership.relative_strength import (
    compute_relative_return_windows, composite_relative_return, rank_universe,
    classify_rs_trend, compute_rs_acceleration, build_stock_relative_strength,
)
from tests.unit.market_leadership.conftest import make_price_df, make_uptrend_df, make_flat_df


@pytest.mark.unit
class TestComputeRelativeReturnWindows:
    def test_outperformer_has_positive_relative_return(self):
        stock = make_price_df(300, daily_drift_pct=0.20, seed=1)
        bench = make_price_df(300, daily_drift_pct=0.02, seed=2)
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, bench, as_of)
        assert calc["windows"]["rs_3m"] is not None
        assert calc["windows"]["rs_3m"] > 0
        assert calc["quality"] == DataQualityStatus.OK

    def test_underperformer_has_negative_relative_return(self):
        stock = make_price_df(300, daily_drift_pct=-0.10, seed=1)
        bench = make_price_df(300, daily_drift_pct=0.10, seed=2)
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, bench, as_of)
        assert calc["windows"]["rs_3m"] < 0

    def test_empty_price_df_is_failed_quality_never_neutral(self):
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        calc = compute_relative_return_windows(empty, make_flat_df(), pd.Timestamp.now().date())
        assert calc["quality"] == DataQualityStatus.FAILED
        assert all(v is None for v in calc["windows"].values())

    def test_short_history_ipo_case_yields_none_for_long_windows(self):
        stock = make_price_df(40, seed=3)  # only ~40 sessions — an IPO-like case
        bench = make_flat_df()
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, bench, as_of)
        assert calc["windows"]["rs_12m"] is None
        assert calc["windows"]["rs_6m"] is None

    def test_none_benchmark_df_yields_none_windows_not_a_crash(self):
        stock = make_uptrend_df()
        as_of = stock.index[-1].date()
        calc = compute_relative_return_windows(stock, None, as_of)
        assert all(v is None for v in calc["windows"].values())

    def test_as_of_in_the_past_excludes_later_bars(self):
        """Leakage guard at the window-computation layer: as_of slicing
        must be applied before any return is computed."""
        stock = make_uptrend_df(300)
        bench = make_flat_df(300)
        cutoff = stock.index[150].date()
        calc = compute_relative_return_windows(stock, bench, cutoff)
        # Only ~151 sessions visible — rs_6m (126) barely fits, rs_12m (252) must not.
        assert calc["windows"]["rs_12m"] is None


@pytest.mark.unit
class TestCompositeRelativeReturn:
    def test_none_when_required_window_missing(self):
        assert composite_relative_return({"rs_1m": 5.0, "rs_3m": None, "rs_6m": 3.0, "rs_12m": None}) is None

    def test_none_when_fewer_than_min_windows_usable(self):
        assert composite_relative_return({"rs_1m": None, "rs_3m": 5.0, "rs_6m": None, "rs_12m": None}) is None

    def test_equal_weighted_average_over_usable_windows(self):
        composite = composite_relative_return({"rs_1m": 10.0, "rs_3m": 10.0, "rs_6m": None, "rs_12m": None})
        assert composite == pytest.approx(10.0)

    def test_all_four_windows_simple_average(self):
        composite = composite_relative_return({"rs_1m": 4.0, "rs_3m": 8.0, "rs_6m": 12.0, "rs_12m": 16.0})
        assert composite == pytest.approx(10.0)


@pytest.mark.unit
class TestRankUniverse:
    def test_empty_input_returns_empty(self):
        assert rank_universe({}) == {}

    def test_percentiles_are_between_0_and_100(self):
        composites = {f"S{i}": float(i) for i in range(20)}
        ranked = rank_universe(composites)
        for entry in ranked.values():
            assert 0.0 <= entry["percentile"] <= 100.0

    def test_highest_composite_gets_rank_1(self):
        composites = {"A": 1.0, "B": 5.0, "C": 3.0}
        ranked = rank_universe(composites)
        assert ranked["B"]["rank"] == 1
        assert ranked["A"]["rank"] == 3

    def test_tied_values_get_equal_percentile(self):
        composites = {"A": 5.0, "B": 5.0, "C": 1.0}
        ranked = rank_universe(composites)
        assert ranked["A"]["percentile"] == ranked["B"]["percentile"]

    def test_universe_size_recorded_on_every_entry(self):
        composites = {"A": 1.0, "B": 2.0, "C": 3.0}
        ranked = rank_universe(composites)
        assert all(e["universe_size"] == 3 for e in ranked.values())

    def test_rank_ordering_stable_under_input_ordering(self):
        composites = {"A": 1.0, "B": 2.0, "C": 3.0}
        reordered = {"C": 3.0, "A": 1.0, "B": 2.0}
        r1 = rank_universe(composites)
        r2 = rank_universe(reordered)
        for sym in composites:
            assert r1[sym] == r2[sym]

    def test_identical_inputs_produce_identical_outputs(self):
        composites = {f"S{i}": float(np.sin(i)) for i in range(15)}
        assert rank_universe(composites) == rank_universe(dict(composites))

    def test_nan_composite_never_masquerades_as_a_genuine_extreme(self):
        """Regression: fresh pre-merge adversarial review found a NaN
        composite was silently sorted by numpy as if it were a real,
        comparable value — numpy's argsort places NaN at the extreme end
        of the ordering, so a completely missing/invalid signal could be
        handed a top-percentile rank (confirmed live: NaN landed at
        rank=3/percentile=83.33 among 3 members before the fix). NaN must
        be excluded entirely, exactly like a missing (None) composite."""
        composites = {"A": 5.0, "B": float("nan"), "C": 3.0}
        ranked = rank_universe(composites)
        assert "B" not in ranked
        assert set(ranked) == {"A", "C"}
        assert ranked["A"]["universe_size"] == 2  # denominator excludes the NaN entry

    def test_positive_and_negative_infinity_are_excluded(self):
        composites = {"A": 5.0, "INF": float("inf"), "NEGINF": float("-inf"), "C": 3.0}
        ranked = rank_universe(composites)
        assert "INF" not in ranked
        assert "NEGINF" not in ranked
        assert set(ranked) == {"A", "C"}

    def test_all_nan_input_returns_empty_not_a_crash(self):
        assert rank_universe({"X": float("nan"), "Y": float("nan")}) == {}


@pytest.mark.unit
class TestClassifyRsTrend:
    def test_improving_when_shift_above_deadband(self):
        assert classify_rs_trend([3.0, 3.0], [0.0, 0.0]) == RsTrend.IMPROVING

    def test_weakening_when_shift_below_negative_deadband(self):
        assert classify_rs_trend([0.0, 0.0], [3.0, 3.0]) == RsTrend.WEAKENING

    def test_flat_within_deadband(self):
        assert classify_rs_trend([0.1, 0.1], [0.0, 0.0]) == RsTrend.FLAT

    def test_insufficient_data_on_empty_input(self):
        assert classify_rs_trend([], []) == RsTrend.INSUFFICIENT_DATA


@pytest.mark.unit
class TestRsAcceleration:
    def test_none_when_either_input_missing(self):
        assert compute_rs_acceleration(None, 5.0) is None
        assert compute_rs_acceleration(5.0, None) is None

    def test_positive_when_short_term_outpaces_annualized_medium_term(self):
        # rs_1m=5% over 21d annualizes far above rs_6m=5% over 126d.
        accel = compute_rs_acceleration(5.0, 5.0)
        assert accel > 0


@pytest.mark.unit
class TestBuildStockRelativeStrength:
    def test_insufficient_data_never_gets_a_score(self):
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        contract = build_stock_relative_strength(
            "XYZ", "IN", empty, make_flat_df(), pd.Timestamp.now().date(), "u1", None,
        )
        assert contract.stock_rs_score is None
        assert contract.stock_rs_percentile is None
        assert contract.rs_trend == RsTrend.INSUFFICIENT_DATA
        assert "INSUFFICIENT_HISTORY" in contract.reason_codes

    def test_scored_symbol_carries_full_versioning(self):
        stock = make_uptrend_df()
        bench = make_flat_df()
        as_of = stock.index[-1].date()
        rank_entry = {"percentile": 82.5, "rank": 3, "universe_size": 40}
        contract = build_stock_relative_strength(
            "ABC", "US", stock, bench, as_of, "u1", rank_entry, RsTrend.IMPROVING,
        )
        assert contract.stock_rs_score == 82 or contract.stock_rs_score == 83
        assert contract.market == "US"
        assert contract.methodology_version
        assert contract.universe_version
        assert contract.calculation_as_of == as_of.isoformat()

    def test_market_is_never_inferred_it_is_whatever_was_passed(self):
        stock = make_uptrend_df()
        contract = build_stock_relative_strength(
            "ABC", "US", stock, make_flat_df(), stock.index[-1].date(), "u1", None,
        )
        assert contract.market == "US"
