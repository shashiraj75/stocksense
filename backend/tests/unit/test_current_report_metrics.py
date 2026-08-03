"""
Unit tests for services/postmortem/current_report_metrics.py — Wave C,
WC-O observability, including the §O1 fail-open correction (safe_*
wrappers must never raise, regardless of what the underlying in-process
store does).
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
        metrics.record_duration(metrics.DURATION_PROVIDER_ACQUISITION, 0.5)
        metrics.record_duration(metrics.DURATION_PROVIDER_ACQUISITION, 1.5)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_PROVIDER_ACQUISITION]
        assert summary["count"] == 2
        assert summary["avg"] == pytest.approx(1.0)
        assert summary["max"] == pytest.approx(1.5)

    def test_duration_samples_are_bounded(self):
        for _ in range(metrics._MAX_DURATION_SAMPLES + 50):
            metrics.record_duration(metrics.DURATION_PROVIDER_ACQUISITION, 0.01)
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_PROVIDER_ACQUISITION]
        assert summary["count"] == metrics._MAX_DURATION_SAMPLES


@pytest.mark.unit
class TestSafeWrappersAreFailOpen:
    """§O1 — the core correction. Every safe_* function must never raise,
    regardless of an internal store failure, and must never silently
    swallow an unrecognized value without at least a bounded warning."""

    def test_safe_increment_never_raises_when_the_store_itself_raises(self, monkeypatch):
        def _broken_increment(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(metrics, "increment", _broken_increment)
        metrics.safe_increment(metrics.COUNTER_AVAILABILITY_READY)  # must not raise
        assert metrics.get_snapshot()["counters"].get(metrics.COUNTER_METRICS_INTERNAL_FAILURE) == 1

    def test_safe_record_duration_never_raises_when_the_store_itself_raises(self, monkeypatch):
        def _broken_record_duration(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(metrics, "record_duration", _broken_record_duration)
        metrics.safe_record_duration(metrics.DURATION_PROVIDER_ACQUISITION, 1.0)  # must not raise

    def test_safe_record_availability_records_recognized_values_normally(self):
        metrics.safe_record_availability("READY")
        assert metrics.get_snapshot()["counters"][metrics.COUNTER_AVAILABILITY_READY] == 1

    def test_safe_record_availability_never_raises_on_unrecognized_value(self, caplog):
        metrics.safe_record_availability("SOMETHING_MADE_UP")  # must not raise
        assert "unrecognized availability" in caplog.text.lower()
        # No counter is silently invented for the unrecognized value —
        # only the bounded warning is the record of this happening.
        assert "SOMETHING_MADE_UP" not in metrics.get_snapshot()["counters"]

    def test_safe_record_availability_never_raises_when_the_store_itself_raises(self, monkeypatch):
        def _broken_record_availability(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(metrics, "record_availability", _broken_record_availability)
        metrics.safe_record_availability("READY")  # must not raise

    def test_safe_timed_still_propagates_the_wrapped_block_s_own_exception(self):
        """The fail-open contract covers the metrics RECORDING, not the
        caller's own work — a real application exception inside the
        `with safe_timed(...):` block must still propagate normally."""
        with pytest.raises(ValueError):
            with metrics.safe_timed(metrics.DURATION_PROVIDER_ACQUISITION):
                raise ValueError("a real application error")

    def test_safe_timed_records_a_sample_when_the_block_succeeds(self):
        with metrics.safe_timed(metrics.DURATION_PROVIDER_ACQUISITION):
            pass
        summary = metrics.get_snapshot()["durations"][metrics.DURATION_PROVIDER_ACQUISITION]
        assert summary["count"] == 1

    def test_internal_failure_log_never_includes_the_raw_exception_text(self, monkeypatch, caplog):
        """Privacy discipline: even when logging that the metrics store
        itself failed, the raw exception object/message must never
        appear — only a fixed, bounded message."""
        def _broken_increment(*a, **k):
            raise RuntimeError("some-sensitive-detail-that-must-never-be-logged")
        monkeypatch.setattr(metrics, "increment", _broken_increment)
        metrics.safe_increment(metrics.COUNTER_AVAILABILITY_READY)
        assert "some-sensitive-detail-that-must-never-be-logged" not in caplog.text
