"""
Regression tests for periodic orphan/restart reconciliation (2026-07-21
memory-exhaustion postmortem consolidation).

Two US Daily Picks incidents occurred back-to-back on 2026-07-21: a
deployment orphaned one run mid phase_1, and its replacement was itself
killed (OOM-consistent signature) shortly after entering `ranking`. Both
times, `reconcile_stale_daily_picks_jobs()`'s existing 6h-only startup
pass never caught it — on the next process boot, the row was only minutes
stale, nowhere near 6h.

A ranking-stage in-process watchdog was considered and explicitly
REJECTED as unsafe (see this PR's description for the full analysis): the
ranking/selection/persistence closure runs synchronously in the same
thread that calls it, while a `threading.Timer` fires independently in a
separate thread. If the closure is genuinely stuck (blocked I/O, a native
call), the timer can mark the job 'failed' and release its lease WHILE the
original closure keeps running — and that closure's own tail
(`save_picks_to_db`) can still complete and publish a payload afterward,
with nothing checking the job's status first. That is precisely the
"two lifecycle owners" / "late write can publish picks" failure mode this
consolidation must not introduce. No timeout is added here; a real
forced-termination guarantee requires child-process or dedicated-worker
isolation (deferred, tracked separately).

Instead, this file covers the SAFE recovery mechanism that was ported:
periodic (not just startup-only) orphan reconciliation, purely a durable-
state, compare-and-set operation against a job whose owning process is
already gone — never trying to interrupt a live worker, only reclassifying
a row once its heartbeat has gone silent far longer than any live process
could explain. Combined with services.memory_guard's cooperative,
in-thread memory checks (see test_memory_guard.py /
test_daily_picks_bounded_phase1.py), this is the actual defense: reduce
retention so the process is far less likely to be killed at all, and
recover the durable row quickly on whatever process boots next if it still
happens.

All tests are deterministic and fully mocked — no real DB, no external
providers, no live Daily Picks generation, no real sleeps.
"""
import inspect
from unittest.mock import MagicMock, patch


# ─── the watchdog was rejected — nothing resembling it may exist ──────────

def test_no_ranking_watchdog_or_timer_based_timeout_exists():
    """A time-based, timer-driven job-abort mechanism was evaluated and
    rejected as unsafe (see module docstring). Nothing resembling it may
    be present: no Timer-driven callback that marks a job failed while
    the protected work can still be running in another thread."""
    import services.daily_picks as _dp
    src = inspect.getsource(_dp)
    for forbidden in ("_RankingTimeoutError", "_run_with_ranking_watchdog", "_RANKING_WATCHDOG_SECONDS"):
        assert forbidden not in src, (
            f"found rejected watchdog artifact {forbidden!r} — a timer-based "
            f"timeout that fires in a separate thread while the ranking "
            f"closure keeps running was explicitly rejected as unsafe"
        )


# ─── default (startup) reconciliation is unchanged ────────────────────────

def _mock_pool(rowcount=None, rows=None, raise_on_connect=None):
    if raise_on_connect:
        mock_pool = MagicMock()
        mock_pool.connection.side_effect = raise_on_connect
        return mock_pool
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    if rowcount is not None:
        mock_cursor.rowcount = rowcount
    if rows is not None:
        mock_cursor.fetchall.return_value = rows
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool


def test_reconcile_default_call_unchanged_uses_six_hour_threshold():
    """Calling reconcile_stale_daily_picks_jobs() with no argument (the
    existing startup call site) must keep embedding the original 6h
    threshold, unchanged from before this consolidation."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    mock_conn = MagicMock()
    stale_cursor = MagicMock()
    stale_cursor.fetchall.return_value = []
    mock_conn.execute.return_value = stale_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        reconcile_stale_daily_picks_jobs()
    sql_text = mock_conn.execute.call_args_list[0][0][0]
    assert "6 hours" in sql_text


def test_reconcile_stale_daily_picks_jobs_reclassifies_and_releases_lease():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    mock_conn = MagicMock()
    stale_cursor = MagicMock()
    stale_cursor.fetchall.return_value = [("job-orphan-us-1",)]
    mock_conn.execute.return_value = stale_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 1
    assert mock_conn.execute.call_count == 2  # the UPDATE...RETURNING, then the lease release


def test_reconcile_stale_daily_picks_jobs_no_op_when_nothing_stale():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rows=[])):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 0


# ─── periodic override: shorter threshold, safety margin over heartbeat ──

def test_reconcile_accepts_shorter_periodic_interval_override():
    """The periodic sweep call site passes a short INTERVAL literal; the
    function must use exactly that literal instead of the 6h default."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    mock_conn = MagicMock()
    stale_cursor = MagicMock()
    stale_cursor.fetchall.return_value = []
    mock_conn.execute.return_value = stale_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        reconcile_stale_daily_picks_jobs(stale_interval="10 minutes")
    sql_text = mock_conn.execute.call_args_list[0][0][0]
    assert "10 minutes" in sql_text
    assert "6 hours" not in sql_text


def test_periodic_stale_interval_has_safety_margin_over_heartbeat_cadence():
    """The periodic sweep threshold must sit far above the heartbeat write
    cadence (30s, in services.daily_picks._heartbeat_loop) so a genuinely
    healthy job — which keeps writing a heartbeat every 30s regardless of
    total run length — can never be interrupted by this sweep. A slow but
    still-heartbeating job is likewise never touched: this reconciliation
    only ever looks at COALESCE(last_runner_heartbeat_at, started_at), not
    total elapsed run time."""
    from services.postgres_store import _DAILY_PICKS_PERIODIC_STALE_INTERVAL
    import re

    match = re.match(r"(\d+)\s+minutes?", _DAILY_PICKS_PERIODIC_STALE_INTERVAL)
    assert match, f"unexpected interval literal format: {_DAILY_PICKS_PERIODIC_STALE_INTERVAL}"
    minutes = int(match.group(1))
    heartbeat_cadence_seconds = 30
    assert minutes * 60 >= 10 * heartbeat_cadence_seconds, (
        "periodic sweep threshold must be at least 10x the heartbeat cadence "
        "to avoid ever touching a healthy (or slow-but-advancing) job"
    )
    assert minutes < 6 * 60, "must stay well below the 6h startup-only threshold"


def test_reconcile_where_clause_keys_only_on_heartbeat_not_elapsed_runtime():
    """A slow job whose heartbeat keeps advancing must never be caught by
    this sweep, no matter how long its total run takes — the WHERE clause
    must only compare against the heartbeat/started_at column, never a
    wall-clock 'time since job started' value."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    assert "COALESCE(last_runner_heartbeat_at, started_at)" in src


# ─── scope: only queued/running rows, only the stale job's own lease ─────

def test_reconcile_periodic_sweep_still_only_targets_queued_and_running():
    """Completed, failed, and already-interrupted jobs must never be
    touched again — the WHERE clause scope is unchanged by adding the
    threshold parameter."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    assert "WHERE status IN ('queued', 'running')" in src


def test_reconcile_releases_only_leases_owned_by_reclassified_jobs():
    """The lease-release UPDATE must be scoped to owner_job_id = ANY(the
    exact set of job_ids this call just reclassified) — never a blanket
    release of unrelated active leases."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    assert "WHERE released_at IS NULL AND owner_job_id = ANY(%s)" in src


def test_reconcile_is_idempotent_second_pass_finds_nothing_left():
    """Once a stale job has been reclassified 'interrupted', a second
    reconciliation pass must find it already gone from the queued/running
    scope and do nothing — proven here by a pass that returns zero rows
    (the mock simulates 'already reconciled')."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rows=[])):
        first_pass_again = reconcile_stale_daily_picks_jobs(stale_interval="10 minutes")
    assert first_pass_again == 0


# ─── periodic loop wiring (api/main.py) ───────────────────────────────────

def test_daily_picks_periodic_reconciliation_loop_wired_into_lifespan():
    """The periodic sweep must actually be started as a background task at
    app startup, same wiring pattern as the other periodic loops."""
    import api.main as main_mod
    src = inspect.getsource(main_mod)
    assert "_daily_picks_orphan_reconciliation_loop" in src
    assert "asyncio.create_task(_daily_picks_orphan_reconciliation_loop())" in src


def test_periodic_reconciliation_loop_uses_the_short_interval_constant():
    """The periodic loop must call reconcile_stale_daily_picks_jobs with
    _DAILY_PICKS_PERIODIC_STALE_INTERVAL — not the bare 6h default — or the
    whole point of adding it is silently lost."""
    import api.main as main_mod
    src = inspect.getsource(main_mod._daily_picks_orphan_reconciliation_loop)
    assert "_DAILY_PICKS_PERIODIC_STALE_INTERVAL" in src
    assert "reconcile_stale_daily_picks_jobs" in src


def test_daily_picks_reconciliation_still_wired_into_startup():
    """The original startup-only 6h pass must still exist and still be
    called with no argument (its original default-preserving contract)."""
    import inspect
    import api.main as main_mod
    assert "reconcile_stale_daily_picks_jobs()" in inspect.getsource(main_mod)


def test_daily_picks_status_endpoint_does_not_call_reconciliation():
    """GET /api/picks/status must never mutate lifecycle state — both the
    startup pass and the periodic sweep are explicit reconciliation paths
    only, never a request-path side effect."""
    import inspect
    from api.routers import picks as picks_router
    status_src = inspect.getsource(picks_router.get_status) if hasattr(picks_router, "get_status") \
        else inspect.getsource(picks_router)
    assert "reconcile_stale_daily_picks_jobs" not in status_src


# ─── never starts a replacement, never touches premarket ──────────────────

def test_reconciliation_never_starts_a_replacement_job():
    """Neither the reconciliation function nor the periodic loop that calls
    it may ever start a new generation run or touch premarket review —
    orphan cleanup only frees the active-job slot; a human (or a separate,
    explicit trigger) must start any replacement."""
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    import api.main as main_mod

    reconcile_src = inspect.getsource(reconcile_stale_daily_picks_jobs)
    loop_src = inspect.getsource(main_mod._daily_picks_orphan_reconciliation_loop)

    # Checks actual call-syntax, not bare words — reconcile_src's own
    # docstring legitimately *mentions* "premarket_finalizer.py" in prose
    # explaining an unrelated status-value compatibility note, which must
    # not count as this function calling premarket logic.
    for forbidden in (
        "generate_picks(", "try_reserve_daily_picks_job(", "/api/picks/generate",
        "premarket_finalize(", "finalize_premarket(",
    ):
        assert forbidden not in reconcile_src
        assert forbidden not in loop_src


def test_reconcile_stale_daily_picks_jobs_swallows_db_errors():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert reconcile_stale_daily_picks_jobs() == 0
