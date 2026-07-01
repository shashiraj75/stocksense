"""
Product Integrity Workstream #002G — Daily Picks Early-Phase Telemetry.

Verifies the full lifecycle of progress writes:
  queued → initializing → universe_selection → phase_0b → phase_0b_done
  → shortlist_ready → phase_1 → ranking → persisting → completed

All tests are fully mocked — no DB, no network, no external providers,
no real Daily Picks generation.
"""

from unittest.mock import MagicMock, patch, call
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared patch context helpers
#
# _generate_picks_inner uses lazy local imports — patch the SOURCE modules, not
# services.daily_picks.<name>, because the names are bound inside the function.
# Module-level functions (_bulk_screen, _predict_stock, _zscore_and_rank,
# _write_score_snapshots, _try_job_progress) ARE accessible via services.daily_picks.
# ─────────────────────────────────────────────────────────────────────────────

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": ""}


def _inner_patches(*, use_postgres=False, predict_return=None):
    """Return a list of patch targets suitable for _generate_picks_inner tests."""
    getenv_val = "1" if use_postgres else None
    return [
        patch("services.daily_picks._bulk_screen",
              return_value=(["AAPL"], 5, "screener", False, 10)),
        patch("services.daily_picks._write_score_snapshots"),
        patch("services.daily_picks._zscore_and_rank", return_value=[]),
        patch("services.daily_picks._predict_stock", return_value=predict_return),
        patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"),
        patch("services.alpha_engine.regime_cluster.detect_regime",
              return_value=_FAKE_REGIME),
        patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}),
        patch("services.alpha_engine.store.log_prediction"),
        patch("services.global_context.get_global_context", return_value={}),
        patch("os.getenv", return_value=getenv_val),
        patch("builtins.open", MagicMock()),
        patch("json.dump"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. initializing is recorded before universe_selection; outcome resolution is
#    NOT run inline from Daily Picks generation (Product Integrity #002K —
#    decoupled from the critical path; owned exclusively by the periodic
#    _outcome_resolver_loop in api/main.py).
# ─────────────────────────────────────────────────────────────────────────────

def test_initializing_written_before_universe_selection():
    """_try_job_progress('initializing') is called before 'universe_selection'."""
    import services.daily_picks as dp

    call_order = []

    def fake_progress(job_id, phase, processed, total, **kw):
        call_order.append(("progress", phase))

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    initializing_pos = next(
        (i for i, e in enumerate(call_order) if e == ("progress", "initializing")), None
    )
    universe_pos = next(
        (i for i, e in enumerate(call_order) if e == ("progress", "universe_selection")), None
    )
    assert initializing_pos is not None, "initializing progress write was never called"
    assert universe_pos is not None, "universe_selection progress write was never called"
    assert initializing_pos < universe_pos, (
        f"initializing (pos {initializing_pos}) must precede universe_selection (pos {universe_pos})"
    )


def test_generate_picks_inner_does_not_call_resolve_pending_outcomes():
    """Daily Picks generation must NOT call resolve_pending_outcomes inline.

    Product Integrity #002J found this call unbounded (no row cap, no
    provider-call cap, no elapsed-time cap) and redundant with the dedicated
    periodic _outcome_resolver_loop. #002K removes it from the critical path.
    """
    import services.daily_picks as dp

    with patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes") as mock_resolve, \
         patch("services.daily_picks._try_job_progress"), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    mock_resolve.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 2. universe_selection written before _bulk_screen is called
# ─────────────────────────────────────────────────────────────────────────────

def test_universe_selection_written_before_bulk_screen():
    """universe_selection progress must be written before _bulk_screen is called."""
    import services.daily_picks as dp

    call_order = []

    def fake_progress(job_id, phase, processed, total, **kw):
        call_order.append(("progress", phase))

    def fake_bulk_screen(*args, **kwargs):
        call_order.append(("bulk_screen",))
        return (["AAPL"], 5, "screener", False, 10)

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen", side_effect=fake_bulk_screen), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    universe_pos = next(
        (i for i, e in enumerate(call_order) if e == ("progress", "universe_selection")), None
    )
    bulk_pos = next(
        (i for i, e in enumerate(call_order) if e == ("bulk_screen",)), None
    )
    assert universe_pos is not None, "universe_selection was never written"
    assert bulk_pos is not None, "_bulk_screen was never called"
    assert universe_pos < bulk_pos, (
        f"universe_selection (pos {universe_pos}) must precede _bulk_screen (pos {bulk_pos})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Existing phase_0b progress behavior remains unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_phase_0b_progress_fires_per_batch():
    """phase_0b progress is written once per yf.download batch (existing behavior).

    Uses side_effect=Exception so the except-path fires, which is the code path that
    guarantees the phase_0b write runs regardless of download success/failure.
    (An empty DataFrame triggers `continue` which skips the phase_0b write;
    a download error falls through the except block to the write at the loop bottom.)
    """
    import services.daily_picks as dp

    progress_calls = []

    def fake_progress(job_id, phase, processed, total, **kw):
        progress_calls.append((phase, processed, total))

    with patch("services.daily_picks._get_universe_by_mcap") as mock_u, \
         patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("yfinance.download", side_effect=Exception("network error")):
        mock_u.return_value = (["AAPL", "MSFT"], "screener", False, 5)

        dp._bulk_screen("US", n_candidates=50, job_id="job-1")

    phase_0b_calls = [(ph, pr, tot) for (ph, pr, tot) in progress_calls if ph == "phase_0b"]
    assert len(phase_0b_calls) >= 1, "phase_0b progress must fire at least once per batch"
    for _, processed, total in phase_0b_calls:
        assert isinstance(processed, int), "phase_0b processed must be an integer"
        assert isinstance(total, int), "phase_0b total must be an integer"
        assert processed >= 1
        assert total >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. shortlist_ready written after bulk screening, before Phase 1
# ─────────────────────────────────────────────────────────────────────────────

def test_shortlist_ready_written_after_bulk_screen_before_phase1():
    """shortlist_ready must appear after phase_0b_done and before phase_1."""
    import services.daily_picks as dp

    call_order = []

    def fake_progress(job_id, phase, processed, total, **kw):
        call_order.append(phase)

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    assert "shortlist_ready" in call_order, "shortlist_ready was never written"
    assert "phase_0b_done" in call_order, "phase_0b_done was never written"
    assert "phase_1" in call_order, "phase_1 was never written"

    pos_0b_done    = call_order.index("phase_0b_done")
    pos_shortlist  = call_order.index("shortlist_ready")
    pos_phase1     = call_order.index("phase_1")

    assert pos_0b_done < pos_shortlist < pos_phase1, (
        f"Expected phase_0b_done({pos_0b_done}) < shortlist_ready({pos_shortlist}) "
        f"< phase_1({pos_phase1})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Existing phase_1 progress reports real task counts
# ─────────────────────────────────────────────────────────────────────────────

def test_phase_1_progress_uses_real_task_counts():
    """phase_1 progress reports real (processed, total) task counts — not None."""
    import services.daily_picks as dp

    phase1_calls = []

    def fake_progress(job_id, phase, processed, total, **kw):
        if phase == "phase_1":
            phase1_calls.append((processed, total))

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL", "MSFT"], 2, "screener", False, 5)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    assert len(phase1_calls) >= 1, "phase_1 progress was never written"
    for processed, total in phase1_calls:
        assert isinstance(processed, int), "phase_1 processed must be int (not None)"
        assert isinstance(total, int), "phase_1 total must be int (not None)"
        # 2 candidates × 3 horizons = 6 tasks total
        assert total == 6, f"Expected 6 tasks (2 candidates × 3 horizons), got {total}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. ranking written immediately after Phase 1, before score snapshots
# ─────────────────────────────────────────────────────────────────────────────

def test_ranking_written_before_score_snapshots_and_before_persisting():
    """ranking must appear after the last phase_1 update, before _write_score_snapshots,
    and before persisting.

    This test will FAIL if _write_score_snapshots is called before the ranking
    progress write — the ordering that was flagged as incorrect in the pre-push audit.
    """
    import services.daily_picks as dp

    call_order = []

    def fake_progress(job_id, phase, processed, total, **kw):
        call_order.append(("progress", phase))

    def fake_score_snapshots(raw, market):
        call_order.append(("write_score_snapshots",))

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots",
               side_effect=fake_score_snapshots), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    # Verify all expected events appeared
    phases = [e[1] for e in call_order if e[0] == "progress"]
    events = call_order  # mixed ("progress", phase) and ("write_score_snapshots",)

    assert ("progress", "ranking") in events, "ranking was never written"
    assert ("write_score_snapshots",) in events, "_write_score_snapshots was never called"
    assert ("progress", "persisting") in events, "persisting was never written"
    assert "phase_1" in phases, "phase_1 was never written"

    last_phase1_pos     = max(i for i, e in enumerate(events) if e == ("progress", "phase_1"))
    pos_ranking         = events.index(("progress", "ranking"))
    pos_score_snapshots = events.index(("write_score_snapshots",))
    pos_persisting      = events.index(("progress", "persisting"))

    assert last_phase1_pos < pos_ranking, (
        f"ranking (pos {pos_ranking}) must come after last phase_1 (pos {last_phase1_pos})"
    )
    # Critical: ranking must precede score snapshots
    assert pos_ranking < pos_score_snapshots, (
        f"ranking (pos {pos_ranking}) must come BEFORE _write_score_snapshots "
        f"(pos {pos_score_snapshots}) — test fails if ordering is reversed"
    )
    assert pos_score_snapshots < pos_persisting, (
        f"_write_score_snapshots (pos {pos_score_snapshots}) must come before "
        f"persisting (pos {pos_persisting})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. persisting written before durable save
# ─────────────────────────────────────────────────────────────────────────────

def test_persisting_written_before_save_picks_to_db():
    """persisting must be written before save_picks_to_db is called."""
    import services.daily_picks as dp

    call_order = []

    def fake_progress(job_id, phase, processed, total, **kw):
        call_order.append(("progress", phase))

    def fake_save(payload, market="US"):
        call_order.append(("save_picks_to_db",))
        return True

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value="1"), \
         patch("services.postgres_store.save_picks_to_db", side_effect=fake_save), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    persisting_pos = next(
        (i for i, e in enumerate(call_order) if e == ("progress", "persisting")), None
    )
    save_pos = next(
        (i for i, e in enumerate(call_order) if e == ("save_picks_to_db",)), None
    )
    assert persisting_pos is not None, "persisting was never written"
    assert save_pos is not None, "save_picks_to_db was never called"
    assert persisting_pos < save_pos, (
        f"persisting (pos {persisting_pos}) must precede save_picks_to_db (pos {save_pos})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. State-only phases pass None, None for processed and total
# ─────────────────────────────────────────────────────────────────────────────

def test_state_only_phases_do_not_fabricate_numeric_workload():
    """initializing, universe_selection, shortlist_ready, ranking, persisting
    must all pass processed=None and total=None."""
    import services.daily_picks as dp

    state_only_phases = {
        "initializing", "universe_selection", "shortlist_ready", "ranking", "persisting"
    }
    violations = []

    def fake_progress(job_id, phase, processed, total, **kw):
        if phase in state_only_phases:
            if processed is not None or total is not None:
                violations.append((phase, processed, total))

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    assert not violations, (
        f"State-only phases must not fabricate counts; violations: {violations}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. last_progress_at is updated for each state transition
# ─────────────────────────────────────────────────────────────────────────────

def test_last_progress_at_updated_per_state_transition():
    """record_daily_picks_job_progress sets last_progress_at on every call."""
    from services.postgres_store import record_daily_picks_job_progress
    from datetime import datetime, timezone

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        record_daily_picks_job_progress("job-1", "initializing", None, None)

    execute_args = mock_conn.execute.call_args.args
    sql    = execute_args[0]
    params = execute_args[1]
    assert "last_progress_at" in sql
    # params for state-only: (phase, processed, total, last_progress_at, job_id)
    last_progress_at_value = params[3]
    assert last_progress_at_value is not None
    assert isinstance(last_progress_at_value, datetime)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Heartbeat is separate from progress updates
# ─────────────────────────────────────────────────────────────────────────────

def test_heartbeat_does_not_alter_phase_or_progress():
    """record_daily_picks_job_heartbeat only writes last_runner_heartbeat_at."""
    from services.postgres_store import record_daily_picks_job_heartbeat
    from datetime import datetime, timezone

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    ts = datetime.now(timezone.utc)
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        record_daily_picks_job_heartbeat("job-1", ts)

    sql = mock_conn.execute.call_args.args[0]
    assert "last_runner_heartbeat_at" in sql
    assert "phase" not in sql
    assert "processed" not in sql
    assert "last_progress_at" not in sql


# ─────────────────────────────────────────────────────────────────────────────
# 11. Failure before persistence never produces completed status
# ─────────────────────────────────────────────────────────────────────────────

def test_failure_in_inner_never_marks_job_completed():
    """If _generate_picks_inner raises, job is marked failed, not completed."""
    import services.daily_picks as dp

    mark_completed_calls = []
    mark_failed_calls = []

    with patch("services.daily_picks._generate_picks_inner",
               side_effect=RuntimeError("ranking exploded")), \
         patch("os.getenv", return_value="1"), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed",
               side_effect=lambda *a, **kw: mark_completed_calls.append(a)), \
         patch("services.postgres_store.mark_daily_picks_job_failed",
               side_effect=lambda *a, **kw: mark_failed_calls.append(a)), \
         patch("services.daily_picks._heartbeat_loop"), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        dp.generate_picks("US", job_id="job-fail-1")

    assert len(mark_completed_calls) == 0, "completed must not be called on failure"
    assert len(mark_failed_calls) == 1, "failed must be called exactly once"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Existing output payload fields unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_does_not_alter_output_payload_fields():
    """Adding telemetry writes must not remove or rename any existing payload fields."""
    import services.daily_picks as dp

    required_fields = {
        "generated_at", "market", "currency", "picks",
        "screened_from", "screener_raw_count", "universe_eligible_size",
        "deep_prediction_candidates", "phase_1_task_total", "final_candidate_count",
        "universe_used", "universe_degraded", "candidates",
        "issuer_dedup_applied", "issuer_duplicates_suppressed",
    }

    with patch("services.daily_picks._try_job_progress"), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        payload, _ = dp._generate_picks_inner("US", job_id=None)

    missing = required_fields - set(payload.keys())
    assert not missing, f"Telemetry changes removed payload fields: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# 13. Full lifecycle order — all new + existing phases in correct sequence
# ─────────────────────────────────────────────────────────────────────────────

def test_full_lifecycle_phase_order():
    """Verify the complete canonical phase sequence end-to-end."""
    import services.daily_picks as dp

    observed_phases = []

    def fake_progress(job_id, phase, processed, total, **kw):
        observed_phases.append(phase)

    with patch("services.daily_picks._try_job_progress", side_effect=fake_progress), \
         patch("services.daily_picks._bulk_screen",
               return_value=(["AAPL"], 5, "screener", False, 10)), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", return_value=[]), \
         patch("services.daily_picks._predict_stock", return_value=None), \
         patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes"), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        try:
            dp._generate_picks_inner("US", job_id="job-1")
        except Exception:
            pass

    def pos(phase):
        indices = [i for i, p in enumerate(observed_phases) if p == phase]
        assert indices, f"Phase '{phase}' never written. Observed: {observed_phases}"
        return indices[0]

    def last_pos(phase):
        indices = [i for i, p in enumerate(observed_phases) if p == phase]
        assert indices, f"Phase '{phase}' never written. Observed: {observed_phases}"
        return indices[-1]

    # phase_0b is written INSIDE _bulk_screen which is mocked here — cannot be observed.
    # It is tested separately in test_phase_0b_progress_fires_per_batch.
    # The assertions below verify all phases observable at the _generate_picks_inner level.
    assert pos("initializing")       < pos("universe_selection"),  "initializing must precede universe_selection"
    assert pos("universe_selection") < pos("phase_0b_done"),       "universe_selection must precede phase_0b_done"
    assert pos("phase_0b_done")      < pos("shortlist_ready"),     "phase_0b_done must precede shortlist_ready"
    assert pos("shortlist_ready")    < pos("phase_1"),             "shortlist_ready must precede phase_1"
    assert last_pos("phase_1")       < pos("ranking"),             "last phase_1 must precede ranking"
    assert pos("ranking")            < pos("persisting"),          "ranking must precede persisting"


# ─────────────────────────────────────────────────────────────────────────────
# 14. No external calls occur — _try_job_progress short-circuits without job_id
# ─────────────────────────────────────────────────────────────────────────────

def test_try_job_progress_short_circuits_without_job_id():
    """_try_job_progress(job_id=None) returns without calling postgres_store."""
    import services.daily_picks as dp

    with patch("services.postgres_store.record_daily_picks_job_progress") as mock_record, \
         patch("os.getenv", return_value=None):
        dp._try_job_progress(None, "initializing", None, None)

    mock_record.assert_not_called()


def test_try_job_progress_short_circuits_without_postgres_env():
    """_try_job_progress with job_id but no USE_POSTGRES=1 must not call postgres_store."""
    import services.daily_picks as dp

    with patch("services.postgres_store.record_daily_picks_job_progress") as mock_record, \
         patch("os.getenv", return_value=None):
        dp._try_job_progress("job-1", "initializing", None, None)

    mock_record.assert_not_called()
