"""
2026-08 Supabase egress incident — TTL cache in front of get_cached_picks().

Before this fix, get_cached_picks() re-read the full picks JSONB payload from
Postgres on every call, with no server-side cache. /api/picks/daily and
/api/picks/status hit this on every request, and frontend polling as often as
every 15s during generation meant the full blob was fetched from Postgres
dozens of times a minute for the whole product's ~2 monthly active users.
Live Supabase Query Performance data showed this pair of queries accounting
for ~83% of total database time in the affected billing cycle.

These tests prove: a cold read hits Postgres at most once, a warm read within
the TTL hits Postgres zero times, the cache is market-isolated, invalidation
after a successful persist forces the next read to be durable, and concurrent
readers during a cache miss share one in-flight fetch (no stampede).
"""
import threading
import time
from unittest.mock import patch

import pytest

import services.daily_picks as daily_picks


@pytest.fixture(autouse=True)
def _reset_picks_cache():
    """Each test starts with a clean, unexpired cache state."""
    daily_picks._picks_cache.clear()
    daily_picks._picks_cache_expiry.clear()
    yield
    daily_picks._picks_cache.clear()
    daily_picks._picks_cache_expiry.clear()


def _payload(market="IN"):
    return {"generated_at": "2026-08-03T00:00:00Z", "market": market, "picks": {}}


@pytest.mark.unit
class TestPicksTTLCache:
    def test_cold_request_reads_postgres_exactly_once(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load:
            mock_load.return_value = _payload("IN")
            result = daily_picks.get_cached_picks("IN")
            assert result == _payload("IN")
            assert mock_load.call_count == 1

    def test_warm_request_within_ttl_performs_zero_durable_reads(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load:
            mock_load.return_value = _payload("IN")
            daily_picks.get_cached_picks("IN")  # cold — populates cache
            mock_load.reset_mock()

            for _ in range(5):
                result = daily_picks.get_cached_picks("IN")
                assert result == _payload("IN")

            assert mock_load.call_count == 0

    def test_cache_is_market_isolated(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load:
            mock_load.side_effect = lambda market: _payload(market)

            daily_picks.get_cached_picks("IN")
            daily_picks.get_cached_picks("US")
            assert mock_load.call_count == 2

            mock_load.reset_mock()
            assert daily_picks.get_cached_picks("IN")["market"] == "IN"
            assert daily_picks.get_cached_picks("US")["market"] == "US"
            # Both already cached from the first pass — no new durable reads.
            assert mock_load.call_count == 0

    def test_invalidation_forces_a_fresh_durable_read(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load:
            mock_load.return_value = _payload("IN")
            daily_picks.get_cached_picks("IN")
            assert mock_load.call_count == 1

            daily_picks._invalidate_cached_picks("IN")

            mock_load.return_value = {**_payload("IN"), "picks": {"NEW": ["x"]}}
            result = daily_picks.get_cached_picks("IN")
            assert mock_load.call_count == 2
            assert result["picks"] == {"NEW": ["x"]}

    def test_expired_ttl_forces_a_fresh_durable_read(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load:
            mock_load.return_value = _payload("IN")
            daily_picks.get_cached_picks("IN")
            assert mock_load.call_count == 1

            # Simulate TTL expiry without sleeping in the test.
            daily_picks._picks_cache_expiry["IN"] = time.monotonic() - 1

            daily_picks.get_cached_picks("IN")
            assert mock_load.call_count == 2

    def test_concurrent_readers_during_a_miss_do_not_stampede(self):
        call_count = {"n": 0}
        call_lock = threading.Lock()

        def _slow_load(market):
            with call_lock:
                call_count["n"] += 1
            time.sleep(0.05)  # widen the race window
            return _payload(market)

        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db", side_effect=_slow_load):
            threads = [
                threading.Thread(target=daily_picks.get_cached_picks, args=("IN",))
                for _ in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert call_count["n"] == 1

    def test_no_failed_payload_is_cached(self):
        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.postgres_store.load_picks_from_db") as mock_load, \
             patch("builtins.open", side_effect=FileNotFoundError):
            mock_load.return_value = None
            result = daily_picks.get_cached_picks("IN")
            assert result is None
            assert "IN" not in daily_picks._picks_cache
