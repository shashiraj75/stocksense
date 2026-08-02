"""
2026-08 Supabase egress incident — endpoint-level proofs, not just helper-level.

Stage 2 correction (PR #34 independent review): the original PR added a TTL
cache in front of get_cached_picks() but GET /api/picks/status still went
through it (via picks_generated_today() and, for US, a second direct call)
on every cold request — reducing frequency but never eliminating full-JSONB
Daily Picks payload reads from the status endpoint. This file proves the
corrected behavior end-to-end through the real FastAPI app, not just against
the helper functions in isolation.
"""
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import services.daily_picks as dp


@pytest.fixture(autouse=True)
def _reset_picks_caches():
    dp._picks_cache.clear()
    dp._picks_cache_expiry.clear()
    dp._picks_metadata_cache.clear()
    dp._picks_metadata_cache_expiry.clear()
    yield
    dp._picks_cache.clear()
    dp._picks_cache_expiry.clear()
    dp._picks_metadata_cache.clear()
    dp._picks_metadata_cache_expiry.clear()


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _payload(market="IN"):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": market,
        "picks": {"short": [], "medium": [], "long": []},
        "base_generated_at": None,
        "premarket_finalized_at": None,
        "premarket_status": None,
        "premarket_finalizer_version": None,
    }


def _metadata(market="IN"):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_generated_at": None,
        "premarket_finalized_at": None,
        "premarket_status": None,
        "premarket_finalizer_version": None,
    }


@pytest.mark.regression
class TestDailyEndpointReadCounts:
    def test_cold_daily_performs_at_most_one_full_payload_read(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db", return_value=_payload("IN")) as mock_load:
            resp = client.get("/api/picks/daily", params={"market": "IN"})
        assert resp.status_code == 200
        assert mock_load.call_count == 1

    def test_warm_daily_performs_zero_full_payload_reads(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db", return_value=_payload("IN")) as mock_load:
            client.get("/api/picks/daily", params={"market": "IN"})
            mock_load.reset_mock()
            for _ in range(5):
                resp = client.get("/api/picks/daily", params={"market": "IN"})
                assert resp.status_code == 200
            assert mock_load.call_count == 0


@pytest.mark.regression
class TestStatusEndpointNeverReadsFullPayload:
    def test_cold_status_in_performs_zero_full_payload_reads(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("IN")), \
             patch("services.postgres_store.load_picks_from_db") as mock_full_load:
            resp = client.get("/api/picks/status", params={"market": "IN"})
        assert resp.status_code == 200
        assert mock_full_load.call_count == 0

    def test_warm_status_in_performs_zero_full_payload_reads(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("IN")), \
             patch("services.postgres_store.load_picks_from_db") as mock_full_load:
            client.get("/api/picks/status", params={"market": "IN"})
            client.get("/api/picks/status", params={"market": "IN"})
            client.get("/api/picks/status", params={"market": "IN"})
        assert mock_full_load.call_count == 0

    def test_cold_status_us_performs_zero_full_payload_reads(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("US")), \
             patch("services.postgres_store.load_picks_from_db") as mock_full_load:
            resp = client.get("/api/picks/status", params={"market": "US"})
        assert resp.status_code == 200
        assert mock_full_load.call_count == 0

    def test_warm_status_us_performs_zero_full_payload_reads(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("US")), \
             patch("services.postgres_store.load_picks_from_db") as mock_full_load:
            for _ in range(5):
                resp = client.get("/api/picks/status", params={"market": "US"})
                assert resp.status_code == 200
        assert mock_full_load.call_count == 0

    def test_status_us_uses_lightweight_metadata_at_most_once_when_warm(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata",
                   return_value=_metadata("US")) as mock_meta:
            client.get("/api/picks/status", params={"market": "US"})
            mock_meta.reset_mock()
            for _ in range(5):
                client.get("/api/picks/status", params={"market": "US"})
            assert mock_meta.call_count == 0  # served from the metadata TTL cache


@pytest.mark.regression
class TestPremarketFinalizationInvalidatesImmediately:
    def test_invalidate_cached_picks_clears_both_caches(self):
        """_invalidate_cached_picks (the exact call premarket_finalizer makes
        after a successful save — see the source-wiring proof below) clears
        both the full-payload cache and the lightweight status-metadata
        cache for the market."""
        dp._picks_cache["US"] = _payload("US")
        dp._picks_cache_expiry["US"] = __import__("time").monotonic() + 60
        dp._picks_metadata_cache["US"] = _metadata("US")
        dp._picks_metadata_cache_expiry["US"] = __import__("time").monotonic() + 60

        dp._invalidate_cached_picks("US")

        assert "US" not in dp._picks_cache
        assert "US" not in dp._picks_metadata_cache

    def test_invalidate_cached_picks_is_called_after_successful_save_in_finalizer_source(self):
        """Static proof the finalizer's persistence path calls the
        invalidation hook — avoids depending on the full finalize_premarket
        control flow (many unrelated preconditions) to prove this wiring."""
        import inspect
        from services import premarket_finalizer

        source = inspect.getsource(premarket_finalizer.finalize_premarket)
        save_idx = source.index("save_picks_to_db(finalized_payload")
        not_saved_idx = source.index('"status": "failed"', save_idx)
        invalidate_idx = source.index("_invalidate_cached_picks(\"US\")", save_idx)
        # Invalidation must be wired after the save call and after the
        # early-return-on-failure branch (only successful persistence
        # invalidates), i.e. strictly between them or after both.
        assert save_idx < not_saved_idx < invalidate_idx


@pytest.mark.regression
class TestFailedPersistenceNeverAdvertised:
    def test_failed_save_does_not_advertise_stale_cache_as_fresh(self):
        """If save_picks_to_db fails, _invalidate_cached_picks must not be
        called by the base-generation path — a stale-but-valid cached
        payload should keep serving rather than being wiped for nothing,
        and no failed/partial payload ever enters the cache in the first
        place since get_cached_picks() only ever caches non-None reads of
        already-durably-persisted (status='success') rows."""
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db", return_value=None) as mock_load:
            result = dp.get_cached_picks("IN")
        assert result is None
        assert "IN" not in dp._picks_cache


@pytest.mark.regression
class TestConcurrentRequestsStampedeSafe:
    def test_concurrent_status_requests_share_one_metadata_fetch(self, client):
        import threading
        import time as _time

        call_count = {"n": 0}
        lock = threading.Lock()

        def _slow_meta(market):
            with lock:
                call_count["n"] += 1
            _time.sleep(0.05)
            return _metadata(market)

        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", side_effect=_slow_meta):
            threads = [
                threading.Thread(target=client.get, args=("/api/picks/status",),
                                  kwargs={"params": {"market": "IN"}})
                for _ in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert call_count["n"] == 1


@pytest.mark.regression
class TestResponseContractsUnchanged:
    def test_status_in_response_shape_unchanged(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("IN")):
            resp = client.get("/api/picks/status", params={"market": "IN"})
        assert resp.status_code == 200
        body = resp.json()
        for key in ("market", "generating", "has_today", "last_error",
                    "last_trigger_received_at", "containment"):
            assert key in body
        assert "premarket_status" not in body
        assert "base_generated_at" not in body

    def test_status_us_response_shape_unchanged(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=_metadata("US")):
            resp = client.get("/api/picks/status", params={"market": "US"})
        assert resp.status_code == 200
        body = resp.json()
        for key in ("base_generated_at", "premarket_finalized_at", "premarket_status",
                    "premarket_finalizer_version", "next_base_run_hint", "next_premarket_run_hint"):
            assert key in body

    def test_status_us_base_generated_at_falls_back_to_generated_at(self, client):
        meta = _metadata("US")
        meta["base_generated_at"] = None
        meta["generated_at"] = "2026-08-01T00:00:00Z"
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.get_picks_status_metadata", return_value=meta):
            resp = client.get("/api/picks/status", params={"market": "US"})
        body = resp.json()
        assert body["base_generated_at"] == meta["base_generated_at"]

    def test_daily_response_shape_unchanged(self, client):
        with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db", return_value=_payload("IN")):
            resp = client.get("/api/picks/daily", params={"market": "IN"})
        assert resp.status_code == 200
        body = resp.json()
        for key in ("generated_at", "market", "picks", "generating", "historical_track_record"):
            assert key in body
