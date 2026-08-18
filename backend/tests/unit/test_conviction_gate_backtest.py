"""
Unit tests for backend/scripts/conviction_gate_backtest.py.

Scope: only the pure statistics layer (bucket_for_confidence,
compute_bucket_stats, format_report) — deliberately synthetic-data-only, so
correctness never depends on real historical volume existing yet. The I/O
layer (fetch_observations / resolve_realized_return / run_backtest) touches
Postgres and yfinance and is exercised manually against real data, not here.
"""

import math

from scripts.conviction_gate_backtest import (
    BUCKET_EDGES,
    MIN_SAMPLE_SIZE,
    bucket_for_confidence,
    compute_bucket_stats,
    format_report,
)


# --------------------------------------------------------------------------
# bucket_for_confidence
# --------------------------------------------------------------------------


class TestBucketForConfidence:
    def test_below_quality_gate(self):
        assert bucket_for_confidence(0) == "0-24 (below quality gate)"
        assert bucket_for_confidence(24.9) == "0-24 (below quality gate)"

    def test_25_49(self):
        assert bucket_for_confidence(25) == "25-49"
        assert bucket_for_confidence(49.9) == "25-49"

    def test_50_69(self):
        assert bucket_for_confidence(50) == "50-69"
        assert bucket_for_confidence(69.9) == "50-69"

    def test_70_79(self):
        assert bucket_for_confidence(70) == "70-79"
        assert bucket_for_confidence(79.9) == "70-79"

    def test_below_publication_gate(self):
        assert bucket_for_confidence(80) == "80-84 (below publication gate)"
        assert bucket_for_confidence(84.9) == "80-84 (below publication gate)"

    def test_at_publication_gate_boundary_is_inclusive(self):
        # The real gate is `confidence >= 85.0` (see
        # services/daily_picks.py _apply_conviction_publication_gate /
        # DAILY_PICKS_PUBLICATION threshold) — 85.0 itself must land in the
        # gate bucket, not the one below it.
        assert bucket_for_confidence(85.0) == "85-94 (publication gate)"
        assert bucket_for_confidence(94.9) == "85-94 (publication gate)"

    def test_saturation_bucket_includes_100(self):
        assert bucket_for_confidence(95.0) == "95-100 (publication gate)"
        assert bucket_for_confidence(100.0) == "95-100 (publication gate)"

    def test_out_of_range_fails_closed(self):
        assert bucket_for_confidence(-0.01) is None
        assert bucket_for_confidence(100.01) is None
        assert bucket_for_confidence(-50) is None
        assert bucket_for_confidence(1000) is None

    def test_non_numeric_fails_closed(self):
        assert bucket_for_confidence(None) is None
        assert bucket_for_confidence("not-a-number") is None
        assert bucket_for_confidence(object()) is None

    def test_nan_and_infinity_fail_closed(self):
        assert bucket_for_confidence(float("nan")) is None
        assert bucket_for_confidence(float("inf")) is None
        assert bucket_for_confidence(float("-inf")) is None

    def test_every_bucket_label_reachable(self):
        # Sanity check on the fixture data below: every declared bucket
        # edge must actually be reachable by some confidence value.
        labels = {label for _, _, label in BUCKET_EDGES}
        reached = {bucket_for_confidence(lo + 0.01) for lo, _, _ in BUCKET_EDGES}
        assert labels == reached


# --------------------------------------------------------------------------
# compute_bucket_stats
# --------------------------------------------------------------------------


def _obs(confidence, realized_return_pct):
    return {"confidence": confidence, "realized_return_pct": realized_return_pct}


class TestComputeBucketStats:
    def test_empty_input_all_buckets_zero(self):
        stats = compute_bucket_stats([])
        assert len(stats) == len(BUCKET_EDGES)
        for s in stats.values():
            assert s.n == 0
            assert s.win_rate is None
            assert s.avg_return_pct is None
            assert s.adequate_sample is False

    def test_win_rate_counts_strictly_positive_returns(self):
        observations = [
            _obs(90, 5.0),   # win
            _obs(90, -2.0),  # loss
            _obs(90, 0.0),   # flat — NOT a win
        ]
        stats = compute_bucket_stats(observations)
        gate_bucket = stats["85-94 (publication gate)"]
        assert gate_bucket.n == 3
        assert gate_bucket.n_wins == 1
        assert math.isclose(gate_bucket.win_rate, 1 / 3)

    def test_avg_return_is_simple_mean(self):
        observations = [_obs(90, 10.0), _obs(91, -4.0), _obs(92, 2.0)]
        stats = compute_bucket_stats(observations)
        gate_bucket = stats["85-94 (publication gate)"]
        assert math.isclose(gate_bucket.avg_return_pct, (10.0 - 4.0 + 2.0) / 3)

    def test_rows_bucket_independently(self):
        observations = [_obs(10, 1.0), _obs(90, 1.0)]
        stats = compute_bucket_stats(observations)
        assert stats["0-24 (below quality gate)"].n == 1
        assert stats["85-94 (publication gate)"].n == 1
        assert stats["25-49"].n == 0

    def test_unbucketable_confidence_excluded_from_every_bucket(self):
        observations = [_obs(None, 5.0), _obs(float("nan"), 5.0), _obs(150, 5.0)]
        stats = compute_bucket_stats(observations)
        assert sum(s.n for s in stats.values()) == 0

    def test_non_finite_return_excluded(self):
        observations = [
            _obs(90, float("nan")),
            _obs(90, float("inf")),
            _obs(90, None),
            _obs(90, 3.0),  # only this one counts
        ]
        stats = compute_bucket_stats(observations)
        gate_bucket = stats["85-94 (publication gate)"]
        assert gate_bucket.n == 1
        assert gate_bucket.avg_return_pct == 3.0

    def test_adequate_sample_flagging_default_threshold(self):
        below = [_obs(90, 1.0) for _ in range(MIN_SAMPLE_SIZE - 1)]
        at = [_obs(90, 1.0) for _ in range(MIN_SAMPLE_SIZE)]
        stats_below = compute_bucket_stats(below)
        stats_at = compute_bucket_stats(at)
        assert stats_below["85-94 (publication gate)"].adequate_sample is False
        assert stats_at["85-94 (publication gate)"].adequate_sample is True

    def test_adequate_sample_flagging_custom_threshold(self):
        observations = [_obs(90, 1.0) for _ in range(5)]
        stats = compute_bucket_stats(observations, min_sample_size=5)
        assert stats["85-94 (publication gate)"].adequate_sample is True
        stats_stricter = compute_bucket_stats(observations, min_sample_size=6)
        assert stats_stricter["85-94 (publication gate)"].adequate_sample is False

    def test_higher_conviction_higher_win_rate_synthetic_signal(self):
        # A synthetic sanity check: if the gate field were perfectly
        # predictive, a naive backtest over synthetic data should recover
        # that monotonic relationship. This does NOT assert anything about
        # real-world data — it only proves the statistics layer doesn't
        # distort a clean synthetic signal.
        low_conf = [_obs(10, -1.0) for _ in range(40)] + [_obs(10, 1.0) for _ in range(10)]
        high_conf = [_obs(96, 1.0) for _ in range(40)] + [_obs(96, -1.0) for _ in range(10)]
        stats = compute_bucket_stats(low_conf + high_conf)
        low_stats = stats["0-24 (below quality gate)"]
        high_stats = stats["95-100 (publication gate)"]
        assert low_stats.adequate_sample and high_stats.adequate_sample
        assert high_stats.win_rate > low_stats.win_rate


# --------------------------------------------------------------------------
# format_report
# --------------------------------------------------------------------------


class TestFormatReport:
    def test_reports_zero_bucket_as_na_not_a_fabricated_number(self):
        stats = compute_bucket_stats([])
        report = format_report({"short": stats})
        assert "n/a" in report
        assert "NO (n=0)" in report

    def test_reports_inadequate_sample_flag(self):
        observations = [_obs(90, 1.0) for _ in range(5)]
        stats = compute_bucket_stats(observations)
        report = format_report({"short": stats})
        assert f"NO (n<{MIN_SAMPLE_SIZE})" in report

    def test_reports_adequate_sample_flag(self):
        observations = [_obs(90, 1.0) for _ in range(MIN_SAMPLE_SIZE)]
        stats = compute_bucket_stats(observations)
        report = format_report({"short": stats})
        lines = [l for l in report.splitlines() if "85-94" in l]
        assert len(lines) == 1
        assert lines[0].rstrip().endswith("yes")

    def test_horizon_header_present(self):
        report = format_report({"medium": compute_bucket_stats([])})
        assert "MEDIUM" in report
