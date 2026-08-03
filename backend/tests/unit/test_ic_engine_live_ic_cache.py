"""
2026-08 Supabase egress incident — caching for _compute_live_ic().

_compute_live_ic() runs get_training_data(), an unbounded join of the entire
predictions/outcomes history. Live Supabase Query Performance data showed
this query pattern at ~22,000 calls and ~12M rows returned in the affected
billing cycle. shadow_ic_available() — a purely diagnostic/observability
flag, never used to influence production ranking — called this function
directly on every Daily Picks generation, bypassing the TTL cache that
get_ic_weights() already had.

These tests prove: a second call for the same (market, horizon) within
CACHE_TTL does not re-hit get_training_data, shadow_ic_available() benefits
from the same cache as get_ic_weights(), the cache is isolated per
(market, horizon), and invalidate_cache() clears it. The returned IC values
themselves are untouched — this only changes how often they're recomputed.
"""
from unittest.mock import patch

import pytest

from services.alpha_engine import ic_engine


@pytest.fixture(autouse=True)
def _clean_cache():
    ic_engine.invalidate_cache()
    yield
    ic_engine.invalidate_cache()


_TRAINING_ROWS = [
    {"tech_z": float(i % 7), "fund_z": float(i % 5), "sentiment_z": float(i % 3),
     "quality_z": float(i % 4), "fwd_return": float(i % 11)}
    for i in range(100)
]


@pytest.mark.unit
class TestLiveICCache:
    def test_second_call_same_market_horizon_does_not_rehit_training_data(self):
        with patch("services.alpha_engine.store.get_training_data",
                   return_value=_TRAINING_ROWS) as mock_data:
            first = ic_engine._compute_live_ic("medium", "IN")
            second = ic_engine._compute_live_ic("medium", "IN")

            assert mock_data.call_count == 1
            assert first == second

    def test_shadow_ic_available_shares_cache_with_get_ic_weights(self):
        with patch("services.alpha_engine.store.get_training_data",
                   return_value=_TRAINING_ROWS) as mock_data:
            ic_engine.get_ic_weights("short", market="US")
            ic_engine.shadow_ic_available("short", market="US")

            assert mock_data.call_count == 1

    def test_cache_is_isolated_per_market_and_horizon(self):
        with patch("services.alpha_engine.store.get_training_data",
                   return_value=_TRAINING_ROWS) as mock_data:
            ic_engine._compute_live_ic("short", "IN")
            ic_engine._compute_live_ic("medium", "IN")
            ic_engine._compute_live_ic("short", "US")

            assert mock_data.call_count == 3

            mock_data.reset_mock()
            ic_engine._compute_live_ic("short", "IN")
            ic_engine._compute_live_ic("medium", "IN")
            ic_engine._compute_live_ic("short", "US")
            assert mock_data.call_count == 0

    def test_invalidate_cache_forces_recompute(self):
        with patch("services.alpha_engine.store.get_training_data",
                   return_value=_TRAINING_ROWS) as mock_data:
            ic_engine._compute_live_ic("long", "IN")
            assert mock_data.call_count == 1

            ic_engine.invalidate_cache()

            ic_engine._compute_live_ic("long", "IN")
            assert mock_data.call_count == 2

    def test_cached_value_is_numerically_identical_to_uncached(self):
        with patch("services.alpha_engine.store.get_training_data",
                   return_value=_TRAINING_ROWS):
            uncached = ic_engine._compute_live_ic_uncached("short", "IN")
            ic_engine.invalidate_cache()
            cached_first = ic_engine._compute_live_ic("short", "IN")
            cached_second = ic_engine._compute_live_ic("short", "IN")

            assert uncached == cached_first == cached_second
