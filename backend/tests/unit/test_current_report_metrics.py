"""
Unit tests for services/postmortem/current_report_metrics.py — Wave C,
WC-O observability.
"""
import pytest

from services.postmortem import current_report_metrics as metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


@pytest.mark.unit
class TestCounters:
    def test_increment_accumulates(self):
        metrics.increment(metrics.COUNTER_AVAILABILITY_READY)
        metrics.increment(metrics.COUNTER_AVAILABILITY_READY)
        assert metrics.get_snapshot()["counters"][metrics.COUNTER_AVAILABILITY_READY] == 2

    def test_independent_counters_do_not_interfere(self):
        metrics.increment(metrics.COUNTER_AVAILABILITY_READY)
        metrics.increment(metrics.COUNTER_AVAILABILITY_PROCESSING)
        counters = metrics.get_snapshot()["counters"]
        assert counters[metrics.COUNTER_AVAILABILITY_READY] == 1
        assert counters[metrics.COUNTER_AVAILABILITY_PROCESSING] == 1

    def test_reset_for_tests_clears_state(self):
        metrics.increment(metrics.COUNTER_AVAILABILITY_READY)
        metrics.reset_for_tests()
        assert metrics.get_snapshot()["counters"] == {}


@pytest.mark.unit
class TestRecordAvailability:
    @pytest.mark.parametrize(
        "availability,counter",
        [
            ("READY", metrics.COUNTER_AVAILABILITY_READY),
            ("PROCESSING", metrics.COUNTER_AVAILABILITY_PROCESSING),
            ("NOT_ELIGIBLE", metrics.COUNTER_AVAILABILITY_NOT_ELIGIBLE),
            ("NOT_AVAILABLE", metrics.COUNTER_AVAILABILITY_NOT_AVAILABLE),
            ("TERMINAL_FAILURE", metrics.COUNTER_AVAILABILITY_TERMINAL_FAILURE),
            ("INTEGRITY_CONTRADICTION", metrics.COUNTER_AVAILABILITY_INTEGRITY_CONTRADICTION),
            ("FEATURE_DISABLED", metrics.COUNTER_AVAILABILITY_FEATURE_DISABLED),
        ],
    )
    def test_record_availability_increments_the_exact_counter(self, availability, counter):
        metrics.record_availability(availability)
        assert metrics.get_snapshot()["counters"][counter] == 1

    def test_unrecognized_availability_value_raises_rather_than_silently_uncounted(self):
        with pytest.raises(KeyError):
            metrics.record_availability("SOMETHING_MADE_UP")


@pytest.mark.unit
class TestDurations:
    def test_record_duration_and_summary(self):
        metrics.record_duration(metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT, 0.5)
        metrics.record_duration(metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT, 1.5)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT]
        assert summary["count"] == 2
        assert summary["avg"] == pytest.approx(1.0)
        assert summary["max"] == pytest.approx(1.5)

    def test_timed_context_manager_records_a_sample(self):
        with metrics.timed(metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT):
            pass
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT]
        assert summary["count"] == 1
        assert summary["max"] >= 0

    def test_duration_samples_are_bounded(self):
        for _ in range(metrics._MAX_DURATION_SAMPLES + 50):
            metrics.record_duration(metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT, 0.01)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_OUTBOX_ROW_AGE_AT_SETTLEMENT]
        assert summary["count"] == metrics._MAX_DURATION_SAMPLES
