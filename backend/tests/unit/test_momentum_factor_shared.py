"""
2026-09-06 corrective review of PR #83 — shared momentum module tests.

services.momentum_factor is the single source of truth for 12-1 month
momentum indexing, used identically by the live path, the backtest
mirror, and every research script. Covers: exact observation-date
indexing (the confirmed off-by-one fix), sufficient/insufficient
history, bucket boundaries, and missing/NaN/infinite/zero/negative
prices.
"""
import math

import pytest

from services.momentum_factor import (
    momentum_indices, momentum_pct, bucket_momentum_score, compute_momentum_score,
    LOOKBACK_DAYS, SKIP_DAYS, NEUTRAL_SCORE,
)


class TestMomentumIndices:
    def test_exact_offsets_from_today(self):
        """today_idx = n_rows - 1. old_idx and recent_idx must be
        EXACTLY lookback/skip steps before that — not lookback-1 (the
        `iloc[-N]` off-by-one this module replaces)."""
        n = LOOKBACK_DAYS + SKIP_DAYS + 50
        old_idx, recent_idx = momentum_indices(n)
        today_idx = n - 1
        assert today_idx - old_idx == LOOKBACK_DAYS
        assert today_idx - recent_idx == SKIP_DAYS

    def test_exactly_minimum_sufficient_history(self):
        n = LOOKBACK_DAYS + 1
        result = momentum_indices(n)
        assert result is not None
        old_idx, _recent_idx = result
        assert old_idx == 0

    def test_insufficient_history_returns_none(self):
        assert momentum_indices(LOOKBACK_DAYS) is None
        assert momentum_indices(1) is None
        assert momentum_indices(0) is None

    def test_custom_lookback_and_skip(self):
        old_idx, recent_idx = momentum_indices(100, lookback=50, skip=10)
        assert old_idx == 49
        assert recent_idx == 89


class TestMomentumPct:
    def _series(self, old_price, recent_price, n=None):
        n = n or (LOOKBACK_DAYS + SKIP_DAYS + 10)
        old_idx, recent_idx = momentum_indices(n)
        prices = [100.0] * n
        prices[old_idx] = old_price
        prices[recent_idx] = recent_price
        return prices

    def test_positive_momentum(self):
        pct = momentum_pct(self._series(100.0, 130.0))
        assert pct == pytest.approx(30.0)

    def test_negative_momentum(self):
        pct = momentum_pct(self._series(100.0, 70.0))
        assert pct == pytest.approx(-30.0)

    def test_insufficient_history_returns_none(self):
        assert momentum_pct([100.0] * LOOKBACK_DAYS) is None

    def test_none_old_price_returns_none(self):
        n = LOOKBACK_DAYS + SKIP_DAYS + 10
        old_idx, _recent_idx = momentum_indices(n)
        prices = [100.0] * n
        prices[old_idx] = None
        assert momentum_pct(prices) is None

    def test_nan_price_returns_none(self):
        assert momentum_pct(self._series(float("nan"), 100.0)) is None
        assert momentum_pct(self._series(100.0, float("nan"))) is None

    def test_infinite_price_returns_none(self):
        assert momentum_pct(self._series(float("inf"), 100.0)) is None
        assert momentum_pct(self._series(100.0, float("-inf"))) is None

    def test_zero_old_price_returns_none(self):
        assert momentum_pct(self._series(0.0, 100.0)) is None

    def test_negative_old_price_returns_none(self):
        assert momentum_pct(self._series(-50.0, 100.0)) is None

    def test_zero_recent_price_is_valid_total_loss(self):
        """A genuine 0 CURRENT price is a valid (if extreme) -100%
        reading, not an error — only the denominator (old price) must be
        strictly positive."""
        pct = momentum_pct(self._series(100.0, 0.0))
        assert pct == pytest.approx(-100.0)

    def test_works_with_plain_list_and_would_work_with_pandas_series(self):
        # Explicit list input (research use) — pandas.Series support is
        # exercised indirectly via compute_momentum_score's live/backtest
        # callers, which pass df["Close"] (a Series) directly.
        prices = self._series(100.0, 120.0)
        assert momentum_pct(prices) == pytest.approx(20.0)


class TestBucketMomentumScore:
    def test_none_yields_neutral(self):
        assert bucket_momentum_score(None) == NEUTRAL_SCORE

    @pytest.mark.parametrize("pct,expected", [
        (30.0, 58.0),    # exactly at the strong-positive boundary: falls through to the +8 bucket (`> 15`), not included in `> 30`
        (30.01, 65.0),
        (15.0, 50.0),    # exactly at the positive boundary: NOT included (`> 15`)
        (15.01, 58.0),
        (0.0, 50.0),
        (-15.0, 50.0),   # exactly at the negative boundary: NOT included (`< -15`)
        (-15.01, 42.0),
        (-30.0, 42.0),   # exactly at the strong-negative boundary: NOT included (`< -30`)
        (-30.01, 35.0),
    ])
    def test_bucket_boundaries(self, pct, expected):
        assert bucket_momentum_score(pct) == expected

    def test_never_outside_0_100(self):
        assert bucket_momentum_score(10_000.0) <= 100.0
        assert bucket_momentum_score(-10_000.0) >= 0.0


class TestComputeMomentumScoreEndToEnd:
    def test_matches_bucket_of_direct_pct(self):
        n = LOOKBACK_DAYS + SKIP_DAYS + 10
        old_idx, recent_idx = momentum_indices(n)
        prices = [100.0] * n
        prices[old_idx] = 100.0
        prices[recent_idx] = 120.0
        assert compute_momentum_score(prices) == bucket_momentum_score(momentum_pct(prices))

    def test_insufficient_history_yields_neutral(self):
        assert compute_momentum_score([100.0] * 10) == NEUTRAL_SCORE
