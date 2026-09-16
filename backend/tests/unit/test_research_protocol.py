"""
2026-09-06 corrective review of PR #83 — frozen research protocol tests.

Proves, deterministically and without any network dependency:
  - split_by_unique_date splits on unique dates, never row count;
  - no date appears in both dev and test;
  - outcome-window-crossing dates are purged from both sides;
  - stationary_block_bootstrap_ci behaves sanely on synthetic data with
    a KNOWN true mean and a KNOWN dependence structure.
"""
import sys

sys.path.insert(0, "scripts")

from research_protocol import (  # noqa: E402
    SplitConfig, split_by_unique_date, assert_no_date_overlap,
    stationary_block_bootstrap_ci, RESEARCH_PROTOCOL_STATUS,
)


def _dates(n):
    return [f"2026-01-{d:02d}" if d <= 31 else f"2026-02-{d-31:02d}" for d in range(1, n + 1)]


class TestSplitByUniqueDate:
    def test_splits_by_unique_dates_not_row_count(self):
        """10 unique dates, but with wildly uneven row counts per date
        (simulating a universe where some dates have many more stocks
        than others) — the split boundary must still land on a date
        boundary, never mid-date."""
        dates_with_dupes = (["d1"] * 50 + ["d2"] * 1 + ["d3"] * 1 + ["d4"] * 1 +
                             ["d5"] * 1 + ["d6"] * 1 + ["d7"] * 1 + ["d8"] * 1 +
                             ["d9"] * 1 + ["d10"] * 1)
        result = split_by_unique_date(dates_with_dupes, SplitConfig(dev_fraction=0.7, outcome_window_days=0))
        # 10 unique dates total, 70% -> 7 dev, 3 test, no purge (window=0)
        assert len(result.dev_dates) == 7
        assert len(result.test_dates) == 3
        assert_no_date_overlap(result)  # must not raise

    def test_no_date_overlap_ever(self):
        result = split_by_unique_date(_dates(100), SplitConfig(dev_fraction=0.6, outcome_window_days=5))
        assert_no_date_overlap(result)  # explicit non-raising assertion
        all_clean = set(result.dev_dates) | set(result.test_dates)
        all_purged = set(result.purged_dev_dates) | set(result.purged_test_dates)
        assert all_clean.isdisjoint(all_purged)

    def test_purges_dates_within_outcome_window_of_boundary(self):
        dates = _dates(100)
        config = SplitConfig(dev_fraction=0.5, outcome_window_days=10)
        result = split_by_unique_date(dates, config)
        # boundary at unique-date position 50; purge zone is [40, 60)
        assert len(result.purged_dev_dates) == 10   # positions 40-49
        assert len(result.purged_test_dates) == 10  # positions 50-59
        # the LAST dev date and FIRST test date must both be purged
        assert dates[49] in result.purged_dev_dates
        assert dates[50] in result.purged_test_dates
        # a date well before the boundary must NOT be purged
        assert dates[0] in result.dev_dates
        # a date well after the boundary must NOT be purged
        assert dates[-1] in result.test_dates

    def test_zero_outcome_window_purges_nothing(self):
        result = split_by_unique_date(_dates(20), SplitConfig(dev_fraction=0.5, outcome_window_days=0))
        assert result.purged_dev_dates == ()
        assert result.purged_test_dates == ()
        assert len(result.dev_dates) + len(result.test_dates) == 20

    def test_empty_input(self):
        result = split_by_unique_date([], SplitConfig())
        assert result.dev_dates == ()
        assert result.test_dates == ()

    def test_dev_and_test_together_with_purged_account_for_all_unique_dates(self):
        dates = _dates(50)
        result = split_by_unique_date(dates, SplitConfig(dev_fraction=0.6, outcome_window_days=3))
        total = (len(result.dev_dates) + len(result.test_dates)
                 + len(result.purged_dev_dates) + len(result.purged_test_dates))
        assert total == len(set(dates))


class TestStationaryBlockBootstrapCI:
    def test_clearly_positive_series_yields_significant_positive_ci(self):
        values = [0.1] * 200  # zero variance, unambiguously positive
        result = stationary_block_bootstrap_ci(values, block_size=5, n_bootstrap=500)
        assert result["mean"] == 0.1
        assert result["ci_low"] > 0
        assert result["significant_at_95pct"] is True

    def test_zero_mean_series_is_not_significant(self):
        values = ([0.05, -0.05] * 100)
        result = stationary_block_bootstrap_ci(values, block_size=4, n_bootstrap=500)
        assert abs(result["mean"]) < 1e-9
        assert result["ci_low"] <= 0.0 <= result["ci_high"]
        assert result["significant_at_95pct"] is False

    def test_insufficient_data_returns_none_fields(self):
        result = stationary_block_bootstrap_ci([0.1, 0.2], block_size=10)
        assert result["mean"] is None
        assert result["n"] == 2

    def test_deterministic_with_fixed_seed(self):
        values = [0.03, -0.01, 0.02, 0.04, -0.02, 0.01, 0.03, -0.01] * 10
        r1 = stationary_block_bootstrap_ci(values, block_size=3, seed=123)
        r2 = stationary_block_bootstrap_ci(values, block_size=3, seed=123)
        assert r1 == r2

    def test_reports_effect_size_and_interval_not_only_a_binary_verdict(self):
        values = [0.02] * 50
        result = stationary_block_bootstrap_ci(values, block_size=5)
        assert "mean" in result and "ci_low" in result and "ci_high" in result and "se" in result


def test_research_protocol_status_is_the_explicit_escape_valve():
    """This module ships with NO claim of a completed acceptance run —
    the honest, explicit status per the corrective-review instructions."""
    assert RESEARCH_PROTOCOL_STATUS == "QUANTITATIVE ACCEPTANCE PENDING — NO UNTOUCHED HOLDOUT AVAILABLE"
