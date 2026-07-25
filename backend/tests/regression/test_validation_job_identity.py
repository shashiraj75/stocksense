"""
Regression: validation jobs must be immutably bound to market, universe
and horizon.

Original production defect (2026-07, observed in the Validation UI): with
"Nifty 100" + "Short (5-day)" selected, the page displayed an active run
processing US symbols (V, MA, JNJ, PYPL, UNH, PFE). Root cause: the active
run state (`validation_engine._run_status`) was a module-global dict with
NO job identity — no job_id, no market, no universe, no horizon — and
/api/validation/status returned it verbatim, so the frontend rendered
whatever run was globally active under whichever tab the user had selected.

These tests reproduce that defect at the state layer: before the fix,
`get_run_status()` during a US run carried nothing identifying the run as
US, so every assertion on the job identity below failed. After the fix,
a running job always carries exactly one market/universe/horizon identity,
created once under the status lock and never relabeled afterwards.

All tests are deterministic and fully mocked — no live network, no real DB
writes (SES-003).
"""
import sqlite3
import threading
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import services.validation_engine as ve


# The immutable job-identity contract every validation job must carry.
REQUIRED_JOB_FIELDS = {
    "job_id", "market", "universe_id", "universe_version", "benchmark",
    "horizon", "started_at", "completed_at", "status", "processed", "total",
    "current_symbol", "source_commit", "model_version", "methodology_version",
    "data_cutoff", "data_cutoff_basis", "requested_by", "trigger_type",
    "created_at", "updated_at", "failure_code", "failure_message",
}


class _NoWriteCursor:
    lastrowid = 0


class _NoWriteConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _NoWriteCursor()

    def executemany(self, *args, **kwargs):
        pass


def _mock_io(monkeypatch, backtest_fake):
    """Mock every I/O boundary run_validation() touches (same pattern as
    tests/unit/test_validation_engine_market_routing.py)."""
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = pd.DataFrame()
    monkeypatch.setattr(ve, "_backtest_stock", backtest_fake)
    monkeypatch.setattr(ve, "yf", mock_yf)
    monkeypatch.setattr(ve, "_init_db", lambda: None)
    monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _NoWriteConn())
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    with ve._status_lock:
        ve._run_status.clear()
        ve._run_status.update(
            {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
        )


@pytest.mark.regression
class TestActiveJobCarriesImmutableIdentity:
    """The exact original defect: a US run's live status must identify
    itself as US — never be attributable to Nifty 100 by omission."""

    def _run_and_capture_mid_run_status(self, monkeypatch, universe, horizon):
        captured = {}

        def fake_backtest(sym, hor, benchmark_df, market, universe=None):
            # First symbol captures the live status snapshot mid-run —
            # exactly what /api/validation/status serves the frontend.
            if not captured:
                captured.update(ve.get_run_status())
            return []

        _mock_io(monkeypatch, fake_backtest)
        ve.run_validation(horizon=horizon, universe=universe)
        return captured

    def test_us_run_status_identifies_us_market_not_nifty100(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        # Pre-fix: status carried no "job" key at all — the UI could not
        # distinguish a US run from a Nifty 100 run. This is the defect.
        assert job is not None, "active-run status carries no job identity"
        assert job["market"] == "US"
        assert job["universe_id"] == "us"
        assert job["horizon"] == "short"
        assert job["benchmark"] == "^GSPC"
        assert job["status"] == "running"

    def test_india_run_status_identifies_in_market(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "midcap", "medium")
        job = status.get("job")
        assert job is not None
        assert job["market"] == "IN"
        assert job["universe_id"] == "midcap"
        assert job["horizon"] == "medium"
        assert job["benchmark"] == "^NSEI"

    def test_job_contract_is_complete(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "long")
        job = status.get("job") or {}
        missing = REQUIRED_JOB_FIELDS - set(job)
        assert not missing, f"job identity missing required fields: {sorted(missing)}"

    def test_identity_not_relabeled_during_run(self, monkeypatch):
        """Reading status twice mid-run (as tab switches cause) must return
        the same job_id/market/universe — a running job is never relabeled."""
        snapshots = []

        def fake_backtest(sym, hor, benchmark_df, market, universe=None):
            snapshots.append(ve.get_run_status().get("job"))
            return []

        _mock_io(monkeypatch, fake_backtest)
        ve.run_validation(horizon="short", universe="us")
        assert len(snapshots) >= 2
        first = snapshots[0]
        assert first is not None
        for later in snapshots[1:]:
            assert later["job_id"] == first["job_id"]
            assert later["market"] == first["market"]
            assert later["universe_id"] == first["universe_id"]
            assert later["horizon"] == first["horizon"]

    def test_completed_job_reaches_terminal_state(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        assert job is not None
        assert job["status"] == "completed"
        assert job["completed_at"] is not None
        assert job["failure_code"] is None

    def test_failed_job_records_failure_not_silent_reset(self, monkeypatch, caplog):
        """Public-diagnostic sanitization regression: the raw exception
        message must never reach the public job.failure_message (or the
        public log) — only a stable failure_code/fixed message. The real
        exception must still be logged server-side (caplog), so an
        operator can actually diagnose the failure."""
        import logging
        _mock_io(monkeypatch, lambda *a, **k: [])

        def _boom(*a, **k):
            raise RuntimeError("synthetic metrics failure — password=SECRET host=db.internal")

        monkeypatch.setattr(ve, "_compute_metrics", _boom)
        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            with pytest.raises(RuntimeError):
                ve.run_validation(horizon="short", universe="us")
        status = ve.get_run_status()
        assert status["running"] is False
        job = status.get("job")
        assert job is not None
        assert job["status"] == "failed"
        assert job["failure_code"] == "RUN_EXCEPTION"
        assert job["failure_message"] == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_EXCEPTION"]
        assert "synthetic metrics failure" not in job["failure_message"]
        assert "password" not in job["failure_message"]
        # Public progress log must not carry the raw exception either.
        assert not any("synthetic metrics failure" in line for line in status["log"])
        assert not any("password" in line for line in status["log"])
        # The real exception must still be diagnosable server-side — the
        # traceback (exc_info=True via log.exception) is captured in the
        # formatted log output.
        assert "synthetic metrics failure" in caplog.text

    def test_backward_compatible_top_level_keys_preserved(self, monkeypatch):
        """Existing consumers read running/progress/total/started_at/log —
        the identity fix is additive, not a breaking rename."""
        status = self._run_and_capture_mid_run_status(monkeypatch, "nifty100", "medium")
        for key in ("running", "progress", "total", "started_at", "log"):
            assert key in status, f"backward-compatible key {key!r} missing"

    def test_completed_run_summary_carries_job_identity(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        metrics = ve.run_validation(horizon="short", universe="us")
        job = metrics.get("job")
        assert job is not None, "persisted run summary carries no job identity"
        assert job["market"] == "US"
        assert job["universe_id"] == "us"
        assert job["horizon"] == "short"
        assert job["status"] == "completed"


@pytest.mark.regression
class TestDataCutoffProvenanceIsTruthful:
    """`data_cutoff` previously held the claim/run-start timestamp, which is
    not an observed market-data cutoff (this job pulls each symbol's own
    history independently over the run's lifetime — no single instant is
    "the" cutoff for every symbol). Fixed to be explicitly None with a
    `data_cutoff_basis: "not_captured"` companion field, rather than
    silently presenting an unrelated timestamp as if it were verified
    market-data provenance."""

    def _run_and_capture_mid_run_status(self, monkeypatch, universe, horizon):
        captured = {}

        def fake_backtest(sym, hor, benchmark_df, market, universe=None):
            if not captured:
                captured.update(ve.get_run_status())
            return []

        _mock_io(monkeypatch, fake_backtest)
        ve.run_validation(horizon=horizon, universe=universe)
        return captured

    def test_data_cutoff_is_none_at_claim_time(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        assert job is not None
        assert job["data_cutoff"] is None

    def test_data_cutoff_basis_states_not_captured(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        assert job["data_cutoff_basis"] == "not_captured"

    def test_started_at_and_created_at_remain_populated(self, monkeypatch):
        """The claim/run-start timestamps themselves are still honest and
        present — only the mislabeled 'data cutoff' framing was removed."""
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        assert job["started_at"] is not None
        assert job["created_at"] is not None

    def test_job_identity_still_fixed_market_universe_horizon(self, monkeypatch):
        """This narrow correction must not disturb the original
        immutable-identity fix."""
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        assert job["market"] == "US"
        assert job["universe_id"] == "us"
        assert job["horizon"] == "short"

    def test_completion_does_not_fabricate_a_data_cutoff(self, monkeypatch):
        """Completion must not silently replace the honest None with the
        run-completion timestamp — that would just move the same
        fabrication to a different instant."""
        _mock_io(monkeypatch, lambda *a, **k: [])
        ve.run_validation(horizon="short", universe="us")
        job = ve.get_run_status().get("job")
        assert job["status"] == "completed"
        assert job["data_cutoff"] is None
        assert job["data_cutoff_basis"] == "not_captured"

    def test_persisted_summary_retains_truthful_null_cutoff(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        metrics = ve.run_validation(horizon="short", universe="us")
        job = metrics.get("job")
        assert job is not None
        assert job["data_cutoff"] is None
        assert job["data_cutoff_basis"] == "not_captured"

    def test_existing_backward_compatible_keys_still_present(self, monkeypatch):
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        missing = REQUIRED_JOB_FIELDS - set(job)
        assert not missing, f"job identity missing required fields: {sorted(missing)}"
        for key in ("running", "progress", "total", "started_at", "log"):
            assert key in status

    def test_original_cross_universe_defect_remains_fixed(self, monkeypatch):
        """This narrow correction must not regress the original bug this
        file exists to prevent: a US run must still identify as US, never
        be attributable to Nifty 100 by omission."""
        status = self._run_and_capture_mid_run_status(monkeypatch, "us", "short")
        job = status.get("job")
        assert job["market"] == "US"
        assert job["universe_id"] == "us"
        assert job["benchmark"] == "^GSPC"


@pytest.mark.regression
class TestLastRunTimeScopedToUniverse:
    """get_last_run_time(horizon) previously ignored universe entirely —
    a US run could satisfy a 'when did nifty100 last run?' query."""

    def _seed_two_universes(self, tmp_path, monkeypatch):
        db = tmp_path / "val.db"
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_DB_PATH", str(db))
        # _init_db is once-guarded per process — reset the guard so THIS
        # test's fresh tmp DB gets the schema regardless of test order.
        monkeypatch.setattr(ve, "_db_initialised", False)
        ve._init_db()
        monkeypatch.setattr(ve, "_db_initialised", True)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
                "VALUES (?,?,?,?,?,?)",
                ("2026-07-20T00:00:00+00:00", "medium", 10, 5, "{}", "nifty100"),
            )
            conn.execute(
                "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
                "VALUES (?,?,?,?,?,?)",
                ("2026-07-22T00:00:00+00:00", "medium", 10, 5, "{}", "us"),
            )

    def test_universe_scoped_lookup_returns_that_universe_only(self, tmp_path, monkeypatch):
        self._seed_two_universes(tmp_path, monkeypatch)
        t_in = ve.get_last_run_time("medium", universe="nifty100")
        t_us = ve.get_last_run_time("medium", universe="us")
        assert t_in is not None and t_us is not None
        assert t_in.day == 20, "nifty100 lookup contaminated by the later US run"
        assert t_us.day == 22

    def test_unscoped_lookup_preserves_legacy_any_universe_behavior(self, tmp_path, monkeypatch):
        self._seed_two_universes(tmp_path, monkeypatch)
        t_any = ve.get_last_run_time("medium")
        assert t_any is not None
        assert t_any.day == 22  # most recent across universes, as before


@pytest.mark.regression
class TestStatusApiSurfacesIdentity:
    """/api/validation/status must give the client enough to distinguish a
    foreign-universe active job; /run must not hardcode 'Nifty 100'."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def test_status_endpoint_exposes_job_identity_during_us_run(self, client, monkeypatch):
        with ve._status_lock:
            saved = dict(ve._run_status)
            ve._run_status.clear()
            ve._run_status.update({
                "running": True, "progress": 3, "total": 200,
                "started_at": "2026-07-24T00:00:00+00:00",
                "log": ["[3/200] PYPL: 4 signals"],
                "job": ve._new_job_identity(
                    market="US", universe_id="us", horizon="short",
                    benchmark="^GSPC", total=200, trigger_type="api",
                ),
            })
        try:
            body = client.get("/api/validation/status").json()
            assert body.get("job", {}).get("market") == "US"
            assert body.get("job", {}).get("universe_id") == "us"
        finally:
            with ve._status_lock:
                ve._run_status.clear()
                ve._run_status.update(saved)

    def test_run_endpoint_message_reflects_requested_universe(self, client, monkeypatch):
        # Prevent a real run: the background task must be a no-op.
        monkeypatch.setattr(ve, "run_validation", lambda **kw: {})
        with ve._status_lock:
            ve._run_status["running"] = False
        body = client.post("/api/validation/run?horizon=short&universe=us").json()
        assert body.get("status") == "started"
        # Pre-fix the message hardcoded "across all Nifty 100 stocks" for a
        # US run — the message must reference the universe actually requested.
        assert "Nifty 100" not in body.get("message", "")
        assert body.get("job", {}).get("universe_id") == "us"

    def test_already_running_response_identifies_the_active_job(self, client, monkeypatch):
        with ve._status_lock:
            saved = dict(ve._run_status)
            ve._run_status.clear()
            ve._run_status.update({
                "running": True, "progress": 1, "total": 200,
                "started_at": "2026-07-24T00:00:00+00:00", "log": [],
                "job": ve._new_job_identity(
                    market="US", universe_id="us", horizon="short",
                    benchmark="^GSPC", total=200, trigger_type="api",
                ),
            })
        try:
            body = client.post("/api/validation/run?horizon=medium&universe=nifty100").json()
            assert body.get("status") == "already_running"
            # The client must be able to tell the active job is US — the
            # exact information the original defect withheld.
            assert body.get("job", {}).get("market") == "US"
            assert body.get("job", {}).get("universe_id") == "us"
        finally:
            with ve._status_lock:
                ve._run_status.clear()
                ve._run_status.update(saved)


@pytest.mark.regression
class TestClaimedJobIdentityInvariant:
    """Pre-publication independent review finding: run_validation() accepted
    a caller-supplied _claimed_job with no validation that it matched the
    universe/horizon arguments of the SAME call. Unexploitable through the
    one current caller (api/routers/validation.py's /run, which always
    passes a job claimed for the exact same universe/horizon via shared
    closure variables) but structurally unsafe for any future caller or
    refactor — a mismatched job could silently misattribute a run's
    benchmark, market and persisted identity. Now fails closed."""

    def test_mismatched_universe_raises_before_any_io(self, monkeypatch):
        mismatched_job = ve._new_job_identity(
            market="US", universe_id="us", horizon="short",
            benchmark="^GSPC", total=10, trigger_type="api",
        )

        def _boom_init_db():
            raise AssertionError("_init_db was called — mismatch check did not fire first")

        monkeypatch.setattr(ve, "_init_db", _boom_init_db)
        with ve._status_lock:
            ve._run_status.clear()
            ve._run_status.update({
                "running": True, "progress": 0, "total": 10,
                "started_at": mismatched_job["started_at"], "log": [],
                "job": mismatched_job,
            })

        with pytest.raises(ValueError, match="does not match"):
            ve.run_validation(horizon="medium", universe="nifty100", _claimed_job=mismatched_job)

    def test_mismatched_horizon_raises(self, monkeypatch):
        mismatched_job = ve._new_job_identity(
            market="US", universe_id="us", horizon="short",
            benchmark="^GSPC", total=10, trigger_type="api",
        )
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        with ve._status_lock:
            ve._run_status.clear()
            ve._run_status.update({
                "running": True, "progress": 0, "total": 10,
                "started_at": mismatched_job["started_at"], "log": [],
                "job": mismatched_job,
            })

        with pytest.raises(ValueError, match="does not match"):
            ve.run_validation(horizon="long", universe="us", _claimed_job=mismatched_job)

    def test_mismatch_leaves_running_state_truthfully_terminal_not_stuck(self, monkeypatch):
        """The mismatch guard must release `running`, not leave the slot
        permanently stuck True — a real caller must be able to try again."""
        mismatched_job = ve._new_job_identity(
            market="US", universe_id="us", horizon="short",
            benchmark="^GSPC", total=10, trigger_type="api",
        )
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        with ve._status_lock:
            ve._run_status.clear()
            ve._run_status.update({
                "running": True, "progress": 0, "total": 10,
                "started_at": mismatched_job["started_at"], "log": [],
                "job": mismatched_job,
            })

        with pytest.raises(ValueError):
            ve.run_validation(horizon="medium", universe="nifty100", _claimed_job=mismatched_job)

        status = ve.get_run_status()
        assert status["running"] is False
        job = status.get("job")
        assert job is not None
        assert job["status"] == "failed"
        assert job["failure_code"] == "CLAIMED_JOB_MISMATCH"
        # Public-diagnostic sanitization: the public failure_message is the
        # fixed, stable string — never the dynamic universe/horizon detail
        # (that detail is safe in itself, but the public contract stays
        # deterministic regardless of what the internal message says).
        assert job["failure_message"] == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["CLAIMED_JOB_MISMATCH"]

    def test_matching_job_is_unaffected_by_the_new_check(self, monkeypatch):
        """The one real caller (API /run) always supplies a matching job —
        this must keep working exactly as before."""
        _mock_io(monkeypatch, lambda *a, **k: [])
        job = ve.claim_validation_job("short", "us", trigger_type="api")
        assert job is not None
        metrics = ve.run_validation(horizon="short", universe="us", _claimed_job=job)
        assert metrics["job"]["status"] == "completed"


@pytest.mark.regression
class TestBenchmarkUnavailabilityDisclosure:
    """Pre-publication independent review finding: a benchmark fetch
    failure (or insufficient history for the horizon) silently reported
    benchmark_avg_fwd_return_pct/nifty_avg_fwd_return_pct as a verified
    0.0% return — indistinguishable from a genuinely flat benchmark, and
    misleading for every alpha/beat-benchmark figure derived from it. Now
    explicitly disclosed via benchmark_data_available/
    benchmark_unavailable_reason, and the two percentage fields become
    None (not a fabricated 0.0) when unavailable — a change already
    compatible with the frontend's existing `number | null` type contract
    for nifty_avg_fwd_return_pct."""

    def test_benchmark_fetch_exception_is_disclosed_not_fabricated(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])

        class _BoomTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                raise RuntimeError("synthetic provider failure")

        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _BoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["benchmark_data_available"] is False
        assert "benchmark_fetch_failed" in metrics["benchmark_unavailable_reason"]
        assert metrics["benchmark_avg_fwd_return_pct"] is None
        assert metrics["nifty_avg_fwd_return_pct"] is None

    def test_insufficient_benchmark_history_is_disclosed(self, monkeypatch):
        """The shared _mock_io helper's yf mock returns an empty
        DataFrame (not an exception) — the exact 'fetch succeeded but
        there's no usable history' case."""
        _mock_io(monkeypatch, lambda *a, **k: [])
        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["benchmark_data_available"] is False
        assert metrics["benchmark_unavailable_reason"] == "insufficient_benchmark_history_for_horizon"
        assert metrics["benchmark_avg_fwd_return_pct"] is None
        assert metrics["nifty_avg_fwd_return_pct"] is None

    def test_successful_benchmark_fetch_is_marked_available_with_real_numbers(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])

        idx = pd.bdate_range("2020-01-01", periods=800, tz="UTC")
        closes = np.linspace(100, 150, 800)
        real_bench_df = pd.DataFrame({"Close": closes}, index=idx)

        class _RealTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return real_bench_df

        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _RealTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["benchmark_data_available"] is True
        assert metrics["benchmark_unavailable_reason"] is None
        assert isinstance(metrics["benchmark_avg_fwd_return_pct"], float)
        assert isinstance(metrics["nifty_avg_fwd_return_pct"], float)


@pytest.mark.regression
class TestDeadCodeRemoved:
    """_update_job had zero callers repo-wide and an unvalidated **fields
    signature that could silently violate job-identity immutability if
    ever invoked — removed rather than left as a latent footgun."""

    def test_update_job_no_longer_exists(self):
        assert not hasattr(ve, "_update_job")
