"""
Unit tests for services/postmortem/idempotency_metrics.py.
"""
import pytest

from services.postmortem import idempotency_metrics as metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


@pytest.mark.unit
class TestCounters:
    def test_increment_accumulates(self):
        metrics.increment(metrics.COUNTER_COMPLETED)
        metrics.increment(metrics.COUNTER_COMPLETED)
        assert metrics.get_snapshot()["counters"][metrics.COUNTER_COMPLETED] == 2

    def test_increment_with_amount(self):
        metrics.increment(metrics.COUNTER_CLEANUP_ROWS_DELETED, amount=7)
        assert metrics.get_snapshot()["counters"][metrics.COUNTER_CLEANUP_ROWS_DELETED] == 7

    def test_independent_counters_do_not_interfere(self):
        metrics.increment(metrics.COUNTER_REPLAYED)
        metrics.increment(metrics.COUNTER_FINGERPRINT_CONFLICT)
        counters = metrics.get_snapshot()["counters"]
        assert counters[metrics.COUNTER_REPLAYED] == 1
        assert counters[metrics.COUNTER_FINGERPRINT_CONFLICT] == 1

    def test_reset_for_tests_clears_state(self):
        metrics.increment(metrics.COUNTER_COMPLETED)
        metrics.reset_for_tests()
        assert metrics.get_snapshot()["counters"] == {}


@pytest.mark.unit
class TestDurations:
    def test_record_duration_and_summary(self):
        metrics.record_duration(metrics.DURATION_CLEANUP, 0.5)
        metrics.record_duration(metrics.DURATION_CLEANUP, 1.5)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_CLEANUP]
        assert summary["count"] == 2
        assert summary["avg"] == pytest.approx(1.0)
        assert summary["max"] == pytest.approx(1.5)

    def test_timed_context_manager_records_a_sample(self):
        with metrics.timed(metrics.DURATION_BUY_TRANSACTION):
            pass
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_BUY_TRANSACTION]
        assert summary["count"] == 1
        assert summary["max"] >= 0

    def test_timed_records_even_when_the_block_raises(self):
        with pytest.raises(ValueError):
            with metrics.timed(metrics.DURATION_BUY_TRANSACTION):
                raise ValueError("boom")
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_BUY_TRANSACTION]
        assert summary["count"] == 1

    def test_duration_samples_are_bounded(self):
        for _ in range(metrics._MAX_DURATION_SAMPLES + 50):
            metrics.record_duration(metrics.DURATION_CLEANUP, 0.01)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_CLEANUP]
        assert summary["count"] == metrics._MAX_DURATION_SAMPLES


@pytest.mark.unit
class TestHashKeyPrefix:
    def test_is_short_and_irreversible_looking(self):
        prefix = metrics.hash_key_prefix("550e8400-e29b-41d4-a716-446655440000")
        assert len(prefix) == 8
        assert all(c in "0123456789abcdef" for c in prefix)

    def test_different_keys_produce_different_prefixes(self):
        assert metrics.hash_key_prefix("key-one") != metrics.hash_key_prefix("key-two")

    def test_does_not_contain_the_original_key_substring(self):
        key = "super-secret-idempotency-key-value"
        prefix = metrics.hash_key_prefix(key)
        assert key not in prefix
        assert key[:8] != prefix  # not merely a truncation of the raw key
