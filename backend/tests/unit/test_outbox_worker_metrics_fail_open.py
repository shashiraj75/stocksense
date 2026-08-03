"""
Wave C, WC-O (corrected) §O1, proof item 3 — a worker-row processing
failure must remain isolated (never abort the rest of the batch) even
when metric recording ALSO fails. Runs the real _process_claimed_row
against a fake conn_factory/process_current_report, forcing the
underlying metrics store to raise, and confirms behavior is unchanged.
"""
import asyncio

import pytest

from services.postmortem import current_report_metrics, outbox_worker


@pytest.mark.unit
class TestWorkerRowIsolationUnderMetricsFailure:
    @pytest.mark.asyncio
    async def test_row_processing_failure_stays_isolated_when_metrics_increment_also_raises(self, monkeypatch):
        def _broken_increment(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(current_report_metrics, "increment", _broken_increment)

        def _raising_process_current_report(*a, **k):
            raise RuntimeError("simulated generation failure")
        monkeypatch.setattr(outbox_worker, "process_current_report", _raising_process_current_report)

        row = {"outbox_id": 1, "trade_id": 42, "user_id": "user-a", "status": "GENERATING", "market": "US"}
        # Must not raise — the metrics call site uses safe_increment,
        # which is guaranteed fail-open even when the underlying store
        # itself is broken.
        await outbox_worker._process_claimed_row(
            row, "claimant-a", market_tzinfo_by_market={"US": (None, "America/New_York")}, conn_factory=lambda: None,
        )
        # The internal-failure counter itself is best-effort; the real
        # proof is that nothing raised above.

    @pytest.mark.asyncio
    async def test_row_processing_failure_still_returns_normally_when_metrics_is_healthy(self):
        current_report_metrics.reset_for_tests()

        def _raising_process_current_report(*a, **k):
            raise RuntimeError("simulated generation failure")

        import services.postmortem.outbox_worker as ow
        orig = ow.process_current_report
        ow.process_current_report = _raising_process_current_report
        try:
            row = {"outbox_id": 2, "trade_id": 43, "user_id": "user-b", "status": "GENERATING", "market": "US"}
            await ow._process_claimed_row(
                row, "claimant-b", market_tzinfo_by_market={"US": (None, "America/New_York")}, conn_factory=lambda: None,
            )
        finally:
            ow.process_current_report = orig
        assert current_report_metrics.get_snapshot()["counters"][
            current_report_metrics.COUNTER_WORKER_ROW_PROCESSING_FAILURE
        ] == 1
        current_report_metrics.reset_for_tests()

    @pytest.mark.asyncio
    async def test_multiple_rows_in_a_batch_are_each_isolated_even_when_one_row_and_metrics_both_fail(self, monkeypatch):
        """The batch-processing loop (_poll_once's own `for row in batch`)
        must continue to the next row even if one row's processing AND
        its metrics recording both fail."""
        current_report_metrics.reset_for_tests()
        call_log = []

        def _process(conn_factory, *, trade_id, **kwargs):
            call_log.append(trade_id)
            if trade_id == 100:
                raise RuntimeError("row 100 fails")

        import services.postmortem.outbox_worker as ow
        orig_process = ow.process_current_report
        orig_increment = current_report_metrics.increment
        ow.process_current_report = _process

        def _broken_increment(counter_name, amount=1):
            if counter_name == current_report_metrics.COUNTER_WORKER_ROW_PROCESSING_FAILURE:
                raise RuntimeError("metrics store broken for this counter")
            return orig_increment(counter_name, amount)
        current_report_metrics.increment = _broken_increment

        try:
            batch = [
                {"outbox_id": 1, "trade_id": 100, "user_id": "u1", "status": "GENERATING", "market": "US"},
                {"outbox_id": 2, "trade_id": 101, "user_id": "u2", "status": "GENERATING", "market": "US"},
            ]
            for row in batch:
                await ow._process_claimed_row(
                    row, "claimant", market_tzinfo_by_market={"US": (None, "America/New_York")},
                    conn_factory=lambda: None,
                )
        finally:
            ow.process_current_report = orig_process
            current_report_metrics.increment = orig_increment
            current_report_metrics.reset_for_tests()

        # Both rows were attempted — row 100's failure (and its metrics
        # recording failure) never aborted processing of row 101.
        assert call_log == [100, 101]

    @pytest.mark.asyncio
    async def test_row_failure_logging_and_metrics_failure_together_do_not_abort_the_next_row(self, monkeypatch):
        """Package O §1, proof item 5 — LOGGING itself (not just the
        metrics store) is forced to raise on the failing row, together
        with the metrics store, and the next row in the batch must
        still be attempted."""
        current_report_metrics.reset_for_tests()
        call_log = []

        def _process(conn_factory, *, trade_id, **kwargs):
            call_log.append(trade_id)
            if trade_id == 200:
                raise RuntimeError("row 200 fails")

        monkeypatch.setattr(outbox_worker, "process_current_report", _process)
        monkeypatch.setattr(
            current_report_metrics, "increment",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("metrics store broken")),
        )
        monkeypatch.setattr(outbox_worker.log, "warning", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging broken")))

        batch = [
            {"outbox_id": 1, "trade_id": 200, "user_id": "u1", "status": "GENERATING", "market": "US"},
            {"outbox_id": 2, "trade_id": 201, "user_id": "u2", "status": "GENERATING", "market": "US"},
        ]
        for row in batch:
            await outbox_worker._process_claimed_row(
                row, "claimant", market_tzinfo_by_market={"US": (None, "America/New_York")}, conn_factory=lambda: None,
            )
        current_report_metrics.reset_for_tests()

        assert call_log == [200, 201]


@pytest.mark.unit
class TestQueueSnapshotFailureDoesNotBlockClaiming:
    @pytest.mark.asyncio
    async def test_queue_snapshot_and_its_logging_both_failing_does_not_prevent_the_batch_claim(self, monkeypatch):
        """Package O §1, proof item 4 — the queue-health snapshot read
        AND its transition-logging are both forced to raise; the
        subsequent claim_next_current_outbox_batch call in the same
        cycle must still run and its result must still be processed."""
        claimed_marker = object()

        def _broken_snapshot(*a, **k):
            raise RuntimeError("simulated queue-health query failure")
        monkeypatch.setattr(outbox_worker, "read_queue_health_snapshot", _broken_snapshot)
        monkeypatch.setattr(outbox_worker.log, "warning", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging broken")))

        claimed = []
        def _fake_claim(conn, *, claimant, limit, schema_version, calculation_version, rules_version):
            claimed.append(claimed_marker)
            return []
        monkeypatch.setattr(outbox_worker, "claim_next_current_outbox_batch", _fake_claim)

        class _FakeConn:
            pass

        from contextlib import contextmanager as _cm

        @_cm
        def _conn_factory():
            yield _FakeConn()

        count = await outbox_worker._poll_once(_conn_factory, market_tzinfo_by_market={})
        assert count == 0
        assert claimed == [claimed_marker], "the claim must still run even when the queue-health snapshot/logging both fail"


@pytest.mark.unit
class TestTaskDoneCallbackFailOpen:
    @pytest.mark.asyncio
    async def test_task_done_callback_logging_and_metrics_failure_do_not_corrupt_module_state(self, monkeypatch):
        """Package O §1, proof item 6 — the done-callback's own metrics
        increment AND its logging call are both forced to raise when the
        worker loop task crashes; _TASK/_STOP_EVENT must still clear
        normally (the exact behavior test_outbox_worker_shutdown.py
        already proves in the healthy-observability case)."""
        outbox_worker._TASK = None
        outbox_worker._STOP_EVENT = None
        try:
            monkeypatch.setattr(
                current_report_metrics, "increment",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("metrics store broken")),
            )
            monkeypatch.setattr(outbox_worker.log, "warning", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging broken")))

            async def _raising_loop(conn_factory, *, market_tzinfo_by_market, poll_interval_seconds):
                raise RuntimeError("simulated worker loop crash")
            monkeypatch.setattr(outbox_worker, "_worker_loop", _raising_loop)

            async def _fake_conn_factory():
                return None

            task = outbox_worker.start_outbox_worker(
                _fake_conn_factory, market_tzinfo_by_market={}, poll_interval_seconds=0.05,
            )
            for _ in range(50):
                if task.done():
                    break
                await asyncio.sleep(0.02)
            assert task.done()
            assert outbox_worker._TASK is None
            assert outbox_worker._STOP_EVENT is None
        finally:
            outbox_worker._TASK = None
            outbox_worker._STOP_EVENT = None
