"""
US Daily Picks generation-reliability incident (2026-07-22) — scope
discipline guards. Each test here targets one explicit "must not happen"
from the incident's authorization boundary: memory checking disabled,
abort threshold merely raised, candidate universe silently reduced,
India/paper-trading/DP-026 publication behavior touched, watchdog
publishing a failed attempt as if successful.

These are adversarial in intent — each is written to FAIL if a future
change (or this session's own work) regresses the exact thing it guards,
not to re-prove behavior already covered by the failure-safe-publication
and governed-recovery test files.
"""
import inspect
from pathlib import Path

import services.daily_picks as dp
import services.memory_guard as mg


# ─────────────────────────────────────────────────────────────────────────
# Must not "fix" the incident by weakening the memory guard
# ─────────────────────────────────────────────────────────────────────────

def test_memory_abort_threshold_was_not_raised():
    """The incident must be fixed by reducing peak memory, not by telling
    the circuit breaker to tolerate more of it. 80% is the threshold that
    was in production before this incident's fix — it must be unchanged."""
    assert mg.DEFAULT_ABORT_PCT == 80.0


def test_memory_circuit_breaker_is_not_disabled_or_bypassed():
    """MemoryCircuitBreaker must still be constructed and actually checked
    inside the pipeline — a regression that merely stops calling .check()
    (rather than removing the class) would otherwise pass a naive
    "class still exists" check."""
    src = inspect.getsource(dp._generate_picks_inner)
    assert "MemoryCircuitBreaker" in src
    assert "_mem_guard = MemoryCircuitBreaker(" in src
    assert src.count('_mem_guard.check(') >= 3  # phase_1, ranking_entry, persisting


def test_memory_guard_module_still_raises_on_abort_not_just_logs():
    """The abort path must still raise DailyPicksMemoryLimitError — a
    regression that downgrades this to a log-only warning would silently
    reintroduce the original OS-level OOM-kill failure mode this module
    exists to convert into a handled exception."""
    src = inspect.getsource(mg.MemoryCircuitBreaker.check)
    assert "raise DailyPicksMemoryLimitError" in src


# ─────────────────────────────────────────────────────────────────────────
# Must not "fix" the incident by silently shrinking the scored universe
# ─────────────────────────────────────────────────────────────────────────

def test_candidate_universe_size_was_not_silently_reduced():
    """_N_CANDIDATES/_TARGET_UNIVERSE_SIZE must remain 400 — the incident's
    authorization explicitly forbids silently lowering candidate coverage
    to make the job finish faster/lighter."""
    assert dp._N_CANDIDATES == 400
    assert dp._TARGET_UNIVERSE_SIZE == 400


def test_phase1_still_scores_all_three_horizons_for_every_candidate():
    """The horizon-bounded restructuring must still cover every candidate x
    every horizon — exactly the same total task count as before, just
    reordered. A regression that quietly drops a horizon (e.g. skips
    'long' to save memory) must fail this."""
    src = inspect.getsource(dp._generate_picks_inner)
    assert '_phase1_task_total = len(candidates) * 3' in src
    assert 'for horizon in ("short", "medium", "long"):' in src


# ─────────────────────────────────────────────────────────────────────────
# Must not touch India, paper trading, or DP-026/DP-032 publication
# ─────────────────────────────────────────────────────────────────────────

def test_generate_picks_inner_has_no_market_special_case_for_the_memory_fix():
    """The horizon-bounded restructuring must apply identically to both
    markets — no `if market == "US":` branch around the Phase 1 loop or
    the memory-guard checks, which would mean India got a different
    (untested, unreviewed) code path."""
    src = inspect.getsource(dp._generate_picks_inner)
    phase1_start = src.index('for horizon in ("short", "medium", "long"):')
    phase1_region = src[phase1_start:phase1_start + 1500]
    assert 'if market ==' not in phase1_region
    assert 'if market !=' not in phase1_region


def test_dp026_and_validation_engine_files_untouched_by_this_incident():
    """This incident's fix must not touch DP-026/DP-032 publication-HOLD
    files — those are covered by their own governance (DPD-012) and must
    not be silently bundled into an unrelated reliability fix."""
    backend_root = Path(__file__).parent.parent.parent
    for path in ("services/validation_engine.py", "services/sec_pit_store.py"):
        # These files existing and being importable is the actual guard —
        # a real diff-based check happens in CI/PR review; here we assert
        # the incident's own new functions are NOT defined inside them
        # (i.e., nothing was accidentally merged into the wrong module).
        src = (backend_root / path).read_text()
        assert "attempt_governed_recovery" not in src
        assert "get_generation_attempt_status" not in src
        assert "_MAX_DAILY_RECOVERY_ATTEMPTS" not in src


def test_paper_trading_router_has_no_reference_to_the_new_recovery_machinery():
    backend_root = Path(__file__).parent.parent.parent
    src = (backend_root / "api" / "routers" / "paper_trading.py").read_text()
    assert "attempt_governed_recovery" not in src
    assert "daily_picks_cache" not in src


# ─────────────────────────────────────────────────────────────────────────
# Watchdog must never itself publish a failed attempt as a success
# ─────────────────────────────────────────────────────────────────────────

def test_attempt_governed_recovery_never_calls_save_picks_to_db_directly():
    """The watchdog must delegate ALL actual generation/persistence to
    generate_picks() (which has its own correct success/failure handling)
    — it must never itself construct or save a payload, which would be a
    second place the empty-payload-overwrite bug could be reintroduced."""
    src = inspect.getsource(dp.attempt_governed_recovery)
    assert "save_picks_to_db" not in src
    assert "json.dump" not in src
    assert "generate_picks(market, job_id=job_id)" in src


def test_attempt_governed_recovery_never_marks_a_job_completed_itself():
    """Only generate_picks()'s own lifecycle management may mark a job
    completed — the watchdog must not have its own path to do so, which
    could bypass generate_picks()'s persisted_at-gated completion check."""
    src = inspect.getsource(dp.attempt_governed_recovery)
    assert "mark_daily_picks_job_completed" not in src
