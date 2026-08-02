"""
2026-08 Supabase egress incident — alpha_engine.store.get_training_data()
TTL cache.

Stage 2 correction (PR #34 independent review, finding K): weight_adapter's
count_training_rows() called get_training_data() uncached, once per horizon,
immediately after ic_engine.invalidate_cache() wiped ic_engine's own cache —
so every adaptation cycle triggered a fresh round of full unbounded
predictions/outcomes joins regardless of ic_engine's cache. Caching at
store.get_training_data() itself (the shared entry point for ic_engine,
count_training_rows, and meta_model.train) fixes this for every relevant
caller at once. meta_model.train() bypasses the cache via force_fresh=True
so actual retraining always sees true current data — proven here to still
call the uncached path every time.
"""
from unittest.mock import patch

import pytest

from services.alpha_engine import ic_engine, store


@pytest.fixture(autouse=True)
def _clean_caches():
    store.invalidate_training_data_cache()
    ic_engine.invalidate_cache()
    yield
    store.invalidate_training_data_cache()
    ic_engine.invalidate_cache()


_ROWS = [{"tech_z": 1.0, "fund_z": 1.0, "sentiment_z": 1.0, "quality_z": 1.0, "fwd_return": 1.0}] * 5


@pytest.mark.unit
class TestTrainingDataCache:
    def test_second_call_same_key_does_not_rehit_uncached_join(self):
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("medium", market="IN")
            store.get_training_data("medium", market="IN")
            assert mock_uncached.call_count == 1

    def test_count_training_rows_benefits_from_cache(self):
        """count_training_rows() is called 3x per market per weight_adapter
        cycle (once per horizon) — proving repeated calls for the SAME
        horizon/market hit the cache is the direct fix for finding K."""
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            n1 = store.count_training_rows("short", market="IN")
            n2 = store.count_training_rows("short", market="IN")
            assert n1 == n2 == 5
            assert mock_uncached.call_count == 1

    def test_force_fresh_always_bypasses_cache(self):
        """meta_model.train() must always see true current data."""
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("long", market="US", force_fresh=True)
            store.get_training_data("long", market="US", force_fresh=True)
            assert mock_uncached.call_count == 2

    def test_force_fresh_does_not_populate_or_read_the_cache(self):
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("long", market="US", force_fresh=True)
            # A subsequent normal (cached) call must still be a fresh miss —
            # force_fresh calls never populate the shared cache.
            store.get_training_data("long", market="US")
            assert mock_uncached.call_count == 2

    def test_cache_is_isolated_per_horizon_market_window(self):
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("short", market="IN")
            store.get_training_data("medium", market="IN")
            store.get_training_data("short", market="US")
            store.get_training_data("short", market="IN", window_days=30)
            assert mock_uncached.call_count == 4

    def test_invalidate_training_data_cache_forces_refetch(self):
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("short", market="IN")
            store.invalidate_training_data_cache()
            store.get_training_data("short", market="IN")
            assert mock_uncached.call_count == 2

    def test_ic_engine_invalidate_cache_also_clears_store_cache(self):
        """weight_adapter calls ic_engine.invalidate_cache() once per
        adaptation cycle — it must clear store's training-data cache too,
        otherwise the two caches could disagree about whether new outcomes
        have been picked up."""
        with patch.object(store, "_get_training_data_uncached", return_value=_ROWS) as mock_uncached:
            store.get_training_data("short", market="IN")
            ic_engine.invalidate_cache()
            store.get_training_data("short", market="IN")
            assert mock_uncached.call_count == 2

    def test_meta_model_train_call_site_uses_force_fresh(self):
        """Static proof meta_model.train() opts out of the cache — see the
        module docstring for why: numerical training behavior must never
        change as a result of a caching change."""
        import inspect
        from services.alpha_engine import meta_model

        source = inspect.getsource(meta_model.train)
        assert "force_fresh=True" in source
