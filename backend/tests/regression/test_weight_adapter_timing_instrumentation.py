"""
Product Integrity Workstream #023 — weight_adapter timing instrumentation.

A 2026-07-16 production memory investigation found the backend process's
RSS stayed pinned near the container's OOM limit for ~90 minutes after a
Daily Picks job's own completed_at timestamp — with run_adaptation()
(fired as an un-joined daemon thread from generate_picks()'s `finally`
block) as one of the only things still running in that window. There was
previously no way to know how long this thread actually ran past job
completion. This adds start/end timing logs so a future incident can be
diagnosed with evidence instead of guesswork.

All tests mock every external call (ic_engine, meta_model, regime_cluster,
count_training_rows) — no real training, no real IC computation.
"""

from unittest.mock import patch

from services.alpha_engine import weight_adapter


def _run_with_everything_mocked(caplog, market="US"):
    with patch("services.alpha_engine.weight_adapter.count_training_rows", return_value=0), \
         patch("services.alpha_engine.meta_model.train"), \
         patch("services.alpha_engine.regime_cluster.retrain_on_history"), \
         patch("services.alpha_engine.ic_engine.get_ic_values", return_value={}), \
         patch("services.alpha_engine.meta_model.is_trained", return_value=False):
        with caplog.at_level("INFO"):
            weight_adapter.run_adaptation(market=market)


def test_start_and_completion_are_both_logged(caplog):
    _run_with_everything_mocked(caplog)
    messages = [rec.message for rec in caplog.records]
    assert any("Starting adaptation cycle (US)" in m for m in messages)
    assert any("Adaptation cycle complete (US)" in m for m in messages)


def test_completion_log_reports_elapsed_duration(caplog):
    _run_with_everything_mocked(caplog)
    completion_records = [rec.message for rec in caplog.records if "Adaptation cycle complete" in rec.message]
    assert len(completion_records) == 1
    # Format is "... complete (US) in {elapsed:.1f}s." — assert the numeric
    # duration is present and parses as a non-negative float, not just that
    # some text follows "complete".
    msg = completion_records[0]
    assert " in " in msg and msg.strip().endswith("s.")
    duration_str = msg.split(" in ")[-1].rstrip("s.")
    elapsed = float(duration_str)
    assert elapsed >= 0.0


def test_timing_logs_present_regardless_of_containment_state(caplog, monkeypatch):
    from services.alpha_engine import containment

    monkeypatch.setenv(containment.ENV_VAR, "1")
    _run_with_everything_mocked(caplog)
    messages = [rec.message for rec in caplog.records]
    assert any("Adaptation cycle complete" in m for m in messages), (
        "Timing instrumentation must not be skipped just because production "
        "learning is enabled — the memory-footprint question this exists to "
        "answer is independent of containment state."
    )
