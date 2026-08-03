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
