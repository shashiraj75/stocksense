"""
Wave C, WC-O final correction — §2/§3 real behavioral proof that
current_report_generation.process_current_report wires the provider
acquisition attempt/success/failure/replay signals correctly through
the ACTUAL shared orchestration branches (not source-text inspection),
and that a metrics/logging failure never changes the generation
outcome.

Phase 1's DB-reading helpers (_fetch_trade_row, fetch_entry_snapshot,
fetch_exit_snapshot, report_store.get_current_report,
price_path_generation.load_generation_context) are monkeypatched to
return controlled fakes — process_current_report is called for real,
top to bottom, exercising its actual Phase 2 acquisition branch and
Phase 3-5 pipeline via monkeypatched pipeline functions, never a
reimplemented copy of its logic.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from services.postmortem import current_report_generation as crg
from services.postmortem import current_report_metrics
from services.postmortem import price_path_generation


class _FakeCursor:
    def fetchone(self):
        return None  # no pre-existing outbox row in the COMPLETE/LIMITED_EVIDENCE integrity-contradiction check


class _FakeConn:
    def execute(self, *a, **k):
        return _FakeCursor()


@contextmanager
def _fake_conn_factory():
    # Only the outbox_id integrity-contradiction SELECT ever touches
    # this connection directly — every other DB-reading call
    # (_fetch_trade_row, fetch_entry_snapshot, fetch_exit_snapshot,
    # report_store.get_current_report, load_generation_context) is
    # monkeypatched to a fake, so this object never needs to satisfy
    # any query beyond that one bounded SELECT.
    yield _FakeConn()


class _FakeGenCtx:
    def __init__(self, compatible_evidence):
        self.compatible_evidence = compatible_evidence


def _install_common_fakes(monkeypatch, *, compatible_evidence=None):
    fake_trade = dict(
        trade_id=42, user_id="user-a", status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=108.0, stop_loss=95.0, target_price=110.0,
        opened_at=datetime(2026, 7, 1, tzinfo=timezone.utc), closed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        trade_management_mode="manual", exit_reason="TARGET_HIT",
    )
    monkeypatch.setattr(crg, "_fetch_trade_row", lambda conn, trade_id: fake_trade)
    monkeypatch.setattr(crg, "fetch_entry_snapshot", lambda conn, trade_id: None)
    monkeypatch.setattr(crg, "fetch_exit_snapshot", lambda conn, trade_id: None)
    monkeypatch.setattr(crg.report_store, "get_current_report", lambda *a, **k: None)
    monkeypatch.setattr(
        price_path_generation, "load_generation_context",
        lambda *a, **k: _FakeGenCtx(compatible_evidence),
    )
    # Phase 4/5 — pure/short-write pipeline, faked so the test stays
    # scoped to Phase 2's provider-metrics wiring rather than re-testing
    # the whole payload-construction pipeline (covered elsewhere).
    monkeypatch.setattr(crg, "build_governed_price_path_payload", lambda *a, **k: object())
    monkeypatch.setattr(crg, "compute_postmortem", lambda *a, **k: object())
    monkeypatch.setattr(crg, "build_current_report_payload", lambda *a, **k: object())
    monkeypatch.setattr(crg, "persist_current_report", lambda *a, **k: (object(), True))


@pytest.fixture(autouse=True)
def _reset_metrics():
    current_report_metrics.reset_for_tests()
    yield
    current_report_metrics.reset_for_tests()


@pytest.mark.unit
class TestProviderAcquisitionMetricsWiring:
    def test_real_acquisition_success_records_one_attempt_one_success_one_duration_sample(self, monkeypatch):
        _install_common_fakes(monkeypatch, compatible_evidence=None)
        monkeypatch.setattr(
            price_path_generation, "acquire_evidence_outside_transaction", lambda *a, **k: object(),
        )
        monkeypatch.setattr(price_path_generation, "persist_price_path_evidence", lambda *a, **k: None)

        report, outcome = crg.process_current_report(
            _fake_conn_factory, trade_id=42, user_id="user-a",
            market_tzinfo=timezone.utc, market_timezone_name="America/New_York",
        )

        counters = current_report_metrics.get_snapshot()["counters"]
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_ATTEMPT) == 1
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_SUCCESS) == 1
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_FAILURE, 0) == 0
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_REPLAY, 0) == 0
        durations = current_report_metrics.get_snapshot()["durations"]
        assert durations[current_report_metrics.DURATION_PROVIDER_ACQUISITION]["count"] == 1

    def test_provider_acquisition_exception_records_one_attempt_one_failure_and_preserves_failed_retryable(self, monkeypatch):
        _install_common_fakes(monkeypatch, compatible_evidence=None)

        def _raise(*a, **k):
            raise RuntimeError("simulated provider network failure")
        monkeypatch.setattr(price_path_generation, "acquire_evidence_outside_transaction", _raise)
        monkeypatch.setattr(crg.outbox_ops, "mark_retryable_failure", lambda *a, **k: None)

        report, outcome = crg.process_current_report(
            _fake_conn_factory, trade_id=42, user_id="user-a",
            market_tzinfo=timezone.utc, market_timezone_name="America/New_York",
            outbox_id=7, claimed_by="claimant-a",
        )

        assert report is None
        assert outcome == crg.CURRENT_REPORT_FAILED_RETRYABLE  # existing outcome unchanged
        counters = current_report_metrics.get_snapshot()["counters"]
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_ATTEMPT) == 1
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_SUCCESS, 0) == 0
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_FAILURE) == 1
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_REPLAY, 0) == 0

    def test_compatible_persisted_evidence_replay_records_zero_attempts(self, monkeypatch):
        sentinel_evidence = object()
        _install_common_fakes(monkeypatch, compatible_evidence=sentinel_evidence)
        monkeypatch.setattr(price_path_generation, "_persisted_evidence_to_bundle", lambda evidence: object())

        def _fail_if_called(*a, **k):
            raise AssertionError("acquire_evidence_outside_transaction must not be called on a replay")
        monkeypatch.setattr(price_path_generation, "acquire_evidence_outside_transaction", _fail_if_called)

        report, outcome = crg.process_current_report(
            _fake_conn_factory, trade_id=42, user_id="user-a",
            market_tzinfo=timezone.utc, market_timezone_name="America/New_York",
        )

        counters = current_report_metrics.get_snapshot()["counters"]
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_REPLAY) == 1
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_ATTEMPT, 0) == 0
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_SUCCESS, 0) == 0
        assert counters.get(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_FAILURE, 0) == 0
        durations = current_report_metrics.get_snapshot()["durations"]
        assert current_report_metrics.DURATION_PROVIDER_ACQUISITION not in durations

    def test_metrics_and_logging_failure_never_replace_the_original_provider_exception_or_change_the_outcome(self, monkeypatch):
        """Both the metrics store AND the logging subsystem are forced
        to raise simultaneously — the ORIGINAL provider acquisition
        exception must still be the one that determines the outcome
        (FAILED_RETRYABLE), never masked by a metrics/logging
        exception."""
        _install_common_fakes(monkeypatch, compatible_evidence=None)

        def _raise_provider(*a, **k):
            raise RuntimeError("simulated provider network failure")
        monkeypatch.setattr(price_path_generation, "acquire_evidence_outside_transaction", _raise_provider)
        monkeypatch.setattr(crg.outbox_ops, "mark_retryable_failure", lambda *a, **k: None)

        def _broken_increment(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(current_report_metrics, "increment", _broken_increment)

        def _broken_log(*a, **k):
            raise RuntimeError("simulated logging subsystem failure")
        monkeypatch.setattr(crg.logger, "warning", _broken_log)

        report, outcome = crg.process_current_report(
            _fake_conn_factory, trade_id=42, user_id="user-a",
            market_tzinfo=timezone.utc, market_timezone_name="America/New_York",
            outbox_id=7, claimed_by="claimant-a",
        )

        assert report is None
        assert outcome == crg.CURRENT_REPORT_FAILED_RETRYABLE
