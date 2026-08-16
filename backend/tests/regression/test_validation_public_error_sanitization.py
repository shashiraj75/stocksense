"""
Regression: no publicly reachable Validation payload may ever carry a raw
internal exception (str(e)/repr(e)), a traceback, a Python exception class
name, or provider/SQL/filesystem detail — full diagnostic detail must
still reach server-side logs.

Root cause (Product Integrity / Security correction): four write sites in
validation_engine.py previously embedded the live exception object
directly into a field that is either returned by GET /api/validation/status
(the in-memory `_run_status`/`job` dict) or persisted into `val_runs.summary`
and returned by GET /api/validation/results:

  A. benchmark_unavailable_reason = f"benchmark_fetch_failed: {e}"
  B. job["failure_message"] = str(e)                       (run-level)
  C. log.append(f"...: ERROR {e}")                         (per-symbol)
  D. older val_runs.summary rows already persisted with (A)'s raw text

This file proves, against actual returned payloads (not source-code
strings), that a deliberately hostile fixture exception — containing a
fake password, a fake DB host, a filesystem path and the literal word
"Traceback" — never crosses any of these four boundaries, while the same
detail remains fully diagnosable via caplog (server-side logs). No live
network, live provider, or production database is touched anywhere in
this file (SES-003) — every I/O boundary is mocked, matching the existing
pattern in test_validation_job_identity.py.
"""
import json
import logging

import pandas as pd
import pytest
from unittest.mock import MagicMock

import services.validation_engine as ve

HOSTILE_MESSAGE = (
    "OperationalError: password=SECRET host=db.internal "
    "/app/private.py Traceback (most recent call last)"
)
FORBIDDEN_SUBSTRINGS = (
    "/Users/", "/app/", "postgres://", "password=", "SECRET",
    "db.internal", "Traceback", "OperationalError",
)


def _assert_no_hostile_text(payload) -> None:
    """Serialize the payload exactly as the API boundary would (JSON) and
    assert none of the hostile fixture's forbidden substrings survive
    anywhere in it — not just in the one field we expect to be sanitized."""
    serialized = json.dumps(payload, default=str)
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in serialized, f"{forbidden!r} leaked into public payload: {serialized}"


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


def _valid_benchmark_df(n=300, seed=99):
    """A benchmark DataFrame that passes _validate_benchmark_acquisition
    for every horizon (matches test_validation_job_identity.py's own
    copy, duplicated here so this file stays self-contained)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    rets = rng.normal(0.0003, 0.008, n)
    close = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close}, index=dates)


def _worker_real_run_validation_hostile_per_symbol(result_queue, horizon, universe, max_workers, trigger_type):
    """V-USACT1-B-C3 — top-level, spawn-safe worker that configures its
    OWN freshly imported copy of services.validation_engine (no
    inheritance from any parent monkeypatch) and calls the REAL
    run_validation() with a per-symbol backtest that raises HOSTILE_
    MESSAGE for every symbol — proving the CHILD's own sanitization
    boundary (safe_error_message inside _validation_child_worker) works
    correctly end-to-end under genuine spawn, not merely in-process."""
    import threading as _t
    from unittest.mock import MagicMock as _EMagicMock
    import numpy as _enp
    import pandas as _epd
    from datetime import datetime as _edatetime, timezone as _etimezone
    import services.validation_engine as _ve

    hostile_message = (
        "OperationalError: password=SECRET host=db.internal "
        "/app/private.py Traceback (most recent call last)"
    )

    def _fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
        raise RuntimeError(hostile_message)
    _ve._backtest_stock = _fake_backtest

    def _bench_df():
        dates = _epd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _enp.cumprod(1 + _enp.random.default_rng(99).normal(0.0003, 0.008, 300))
        return _epd.DataFrame({"Close": close}, index=dates)
    mock_yf = _EMagicMock()
    mock_yf.Ticker.return_value.history.return_value = _bench_df()
    _ve.yf = mock_yf
    _ve.time.sleep = lambda *a, **k: None

    try:
        metrics = _ve.run_validation(horizon=horizon, universe=universe, max_workers=max_workers,
                                      trigger_type=trigger_type, _persist=False)
        payload = metrics.get("_persist_payload")
        now_iso = _edatetime.now(_etimezone.utc).isoformat()
        if payload is None:
            result_queue.put({"type": "error", "category": "NO_RESULT_RUN_ID", "message": None,
                               "completed_at_utc": now_iso})
            return
        result_queue.put({"type": "result", "payload": payload, "completed_at_utc": now_iso})
    except Exception as e:
        from services.validation_engine import safe_error_message as _safe_error_message
        now_iso = _edatetime.now(_etimezone.utc).isoformat()
        result_queue.put({"type": "error", "category": type(e).__name__,
                           "message": _safe_error_message(_ve.log, "validation.child_worker", e,
                                                            "Validation computation failed.")[:200],
                           "completed_at_utc": now_iso})


def _mock_io(monkeypatch, backtest_fake, auto_window_stats=True):
    """Mock every I/O boundary run_validation() touches — no live network,
    no real DB read/write (matches test_validation_job_identity.py's
    _mock_io exactly, duplicated here so this file stays self-contained).

    `auto_window_stats=True` (default) makes every _backtest_stock call
    report one genuinely benchmark-valid window before delegating to
    `backtest_fake` — this file's tests are about error sanitization, not
    benchmark signal-coverage itself, so without this every test whose
    stub backtest returns `[]` would spuriously trip the post-alignment
    coverage gate (2026-07-26 hardening, Finding D)."""
    def _wrapped_backtest(*args, **kwargs):
        if auto_window_stats:
            window_stats = kwargs.get("_window_stats")
            if window_stats is not None:
                window_stats["considered"] = window_stats.get("considered", 0) + 1
                window_stats["benchmark_valid"] = window_stats.get("benchmark_valid", 0) + 1
        return backtest_fake(*args, **kwargs)

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
    monkeypatch.setattr(ve, "_backtest_stock", _wrapped_backtest)
    monkeypatch.setattr(ve, "yf", mock_yf)
    monkeypatch.setattr(ve, "_init_db", lambda: None)
    monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _NoWriteConn())
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)  # bounded retry — never slow a test
    with ve._status_lock:
        ve._run_status.clear()
        ve._run_status.update(
            {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
        )


@pytest.mark.regression
class TestBenchmarkHostileExceptionSanitization:
    """Items 1-4, 14, 16: a hostile benchmark-provider exception must
    never reach benchmark_unavailable_reason, must collapse to the one
    stable public code, must survive JSON serialization intact, and the
    real exception must still be logged server-side."""

    def _boom_yf(self, monkeypatch):
        class _BoomTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                raise RuntimeError(HOSTILE_MESSAGE)

        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = _BoomTicker
        monkeypatch.setattr(ve, "yf", mock_yf)

    def test_hostile_benchmark_exception_never_reaches_status(self, monkeypatch, caplog):
        """Benchmark evidence integrity closure (separate, later fix): a
        provider exception now fails the whole run closed before any
        metrics dict is even produced (see test_validation_benchmark_
        evidence.py for the full contract) — so this sanitization
        regression now checks the public job status instead of a
        completed metrics dict, but the core guarantee is unchanged: the
        hostile exception text must never cross the public boundary,
        while remaining fully diagnosable server-side."""
        _mock_io(monkeypatch, lambda *a, **k: [])
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)
        self._boom_yf(monkeypatch)

        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            with pytest.raises(ValueError):
                ve.run_validation(horizon="short", universe="us")

        status = ve.get_run_status()
        _assert_no_hostile_text(status)
        job = status.get("job")
        assert job is not None
        assert job["failure_code"] == "BENCHMARK_EVIDENCE_UNAVAILABLE"
        # Real exception is fully diagnosable server-side.
        assert "password=SECRET" in caplog.text
        assert "db.internal" in caplog.text

    def test_hostile_benchmark_exception_prevents_any_persistence(self, monkeypatch):
        """A provider exception must persist nothing at all — no val_runs
        row, no val_signals rows — rather than merely sanitizing a row
        that gets written anyway. Proven by a DB connection stand-in whose
        execute()/executemany() raise if ever called."""
        class _BoomIfWrittenConn:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def execute(self, *a, **k):
                raise AssertionError("val_runs write attempted despite unavailable benchmark evidence")
            def executemany(self, *a, **k):
                raise AssertionError("val_signals write attempted despite unavailable benchmark evidence")

        _mock_io(monkeypatch, lambda *a, **k: [])
        monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _BoomIfWrittenConn())
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)
        self._boom_yf(monkeypatch)

        with pytest.raises(ValueError):
            ve.run_validation(horizon="short", universe="us")

    def test_distinct_from_insufficient_history(self, monkeypatch):
        """Item 5: a genuine 'not enough bars' condition must keep its own
        distinct stable evidence status ('insufficient_history') — never
        collapsed into the provider-failure/empty-fetch status. Exercised
        directly against the pure validator, which both run_validation
        code paths funnel through."""
        too_few_rows = _valid_benchmark_df(n=10)  # long horizon needs 64 rows
        insufficient_evidence = ve._validate_benchmark_acquisition(too_few_rows, "^GSPC", "US", "long")
        assert insufficient_evidence.status == "insufficient_history"

        fetch_failed_evidence = ve._validate_benchmark_acquisition(None, "^GSPC", "US", "long")
        assert fetch_failed_evidence.status == "empty"
        assert fetch_failed_evidence.status != insufficient_evidence.status


@pytest.mark.regression
class TestPerSymbolHostileExceptionSanitization:
    """Items 8-9: a hostile per-symbol exception must never reach the
    public progress log, but must retain symbol/universe/horizon context
    in the server-side log, and other symbols must keep processing."""

    def test_hostile_symbol_exception_never_reaches_public_log(self, monkeypatch, caplog):
        def fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
            if sym == "AAPL":
                raise RuntimeError(HOSTILE_MESSAGE)
            return []

        _mock_io(monkeypatch, fake_backtest)
        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            ve.run_validation(horizon="short", universe="us")

        status = ve.get_run_status()
        _assert_no_hostile_text(status)
        assert any("SYMBOL_VALIDATION_FAILED" in line for line in status["log"])

    def test_server_log_retains_symbol_and_exception_context(self, monkeypatch, caplog):
        def fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
            if sym == "AAPL":
                raise RuntimeError(HOSTILE_MESSAGE)
            return []

        _mock_io(monkeypatch, fake_backtest)
        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            ve.run_validation(horizon="short", universe="us")

        assert "AAPL" in caplog.text
        assert "password=SECRET" in caplog.text

    def test_other_symbols_still_processed_after_one_raises(self, monkeypatch):
        """Failure containment: one symbol's exception must not abort
        the run for every other symbol."""
        processed = []

        def fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
            processed.append(sym)
            if sym == ve.US_BASKET[0]:
                raise RuntimeError(HOSTILE_MESSAGE)
            return []

        _mock_io(monkeypatch, fake_backtest)
        metrics = ve.run_validation(horizon="short", universe="us")
        assert set(processed) == set(ve.US_BASKET)
        assert metrics["n_stocks_requested"] == len(ve.US_BASKET)


@pytest.mark.regression
class TestLegacyPersistedSummarySanitization:
    """Items 3, 12-13: a val_runs.summary row persisted before this
    sanitization existed must be sanitized on read, and never rewritten."""

    def test_legacy_raw_reason_is_sanitized_on_read(self, monkeypatch):
        legacy_summary = {
            "benchmark_unavailable_reason": f"benchmark_fetch_failed: {HOSTILE_MESSAGE}",
            "buy_hit_rate_pct": 55.5,
            "universe": "us",
            "horizon": "short",
        }
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")

        assert result["available"] is True
        assert result["benchmark_unavailable_reason"] == "benchmark_fetch_failed"
        _assert_no_hostile_text(result)
        # Genuine numeric metric passes through completely unchanged (item 15).
        assert result["buy_hit_rate_pct"] == 55.5

    def test_legacy_connection_data_science_provenance_example_from_brief(self, monkeypatch):
        """The exact example fixture named in the governing brief."""
        legacy_summary = {
            "benchmark_unavailable_reason": "benchmark_fetch_failed: connection to db.internal failed",
        }
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["benchmark_unavailable_reason"] == "benchmark_fetch_failed"
        assert "db.internal" not in result["benchmark_unavailable_reason"]

    def test_legacy_row_is_never_rewritten(self, monkeypatch):
        """get_latest_results is a pure read path — proving no write
        function is ever invoked confirms the stored row cannot be
        mutated by this sanitization."""
        legacy_summary = {"benchmark_unavailable_reason": f"benchmark_fetch_failed: {HOSTILE_MESSAGE}"}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        write_attempted = {"called": False}

        def _no_write_conn():
            write_attempted["called"] = True
            return _NoWriteConn()

        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)
        monkeypatch.setattr(ve, "_get_sqlite_conn", _no_write_conn)

        ve.get_latest_results(horizon="short", universe="us")
        assert write_attempted["called"] is False

    def test_legacy_row_with_only_genuine_insufficient_history_reason_unchanged(self, monkeypatch):
        """A legacy row whose reason was ALREADY the genuine, non-exception
        stable code must pass through unchanged — sanitization must not
        alter a value that was never unsafe."""
        legacy_summary = {"benchmark_unavailable_reason": "insufficient_benchmark_history_for_horizon"}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["benchmark_unavailable_reason"] == "insufficient_benchmark_history_for_horizon"

    def test_legacy_row_with_no_reason_key_unaffected(self, monkeypatch):
        """A row that never had benchmark_unavailable_reason at all (very
        old legacy rows, or a successful run) must not gain a spurious key."""
        legacy_summary = {"buy_hit_rate_pct": 61.0}
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert "benchmark_unavailable_reason" not in result
        assert result["buy_hit_rate_pct"] == 61.0


@pytest.mark.regression
class TestPublicAPIBoundary:
    """Items 10-11: hitting the actual FastAPI routes (not just the
    validation_engine functions directly) must never surface hostile
    fixture text — the router boundary itself must be safe end-to-end."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        # V-SCHED1C1 — /run now admits through the V-SCHED1B durable ledger,
        # which requires the ledger tables; isolate them per-test so this
        # suite never depends on global module state left behind by
        # another test's run and never touches the real local/production
        # database.
        import services.validation_engine as ve
        monkeypatch.setattr(ve, "_DB_PATH", str(tmp_path / "public_error_sanitization_test.db"))
        monkeypatch.setattr(ve, "_db_initialised", False)
        ve._init_db()
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def test_status_endpoint_has_no_raw_exception(self, client, monkeypatch, caplog):
        """V-USACT1-B note: the manual endpoint's background execution now
        runs run_validation() inside a genuinely killable CHILD process
        (see services.validation_engine._run_validation_in_subprocess) —
        every _run_status mutation run_validation() makes happens in that
        child's own, separate memory, and is never visible to the PARENT
        process's _run_status (the dict GET /api/validation/status
        actually reads) or this test's own `caplog` (log capture is
        process-local too). This is a genuine, disclosed behavior change
        from the pre-V-USACT1-B design, not a test-infrastructure quirk —
        GET /api/validation/status no longer reflects live per-symbol
        progress/log detail for ANY run (scheduler, catchup, or manual),
        since all of them now execute through the same subprocess
        boundary. Flagged prominently for independent review.

        The original security property this test protects — hostile
        provider/exception text must never reach a public payload — is,
        if anything, now trivially and MORE strongly guaranteed for
        /status specifically (nothing from the child ever reaches it at
        all). The genuine, still-relevant sanitization proof — that the
        CHILD's own sanitization boundary (safe_error_message inside
        _validation_child_worker) works correctly end-to-end — is
        verified instead via the real, persisted, authenticated attempt-
        history record this run produces.

        V-USACT1-B-C3 — the child now runs under genuine "spawn" (no
        fork override, no inherited monkeypatch): a top-level,
        self-contained worker (_worker_real_run_validation_hostile_per_
        symbol) configures its OWN freshly-spawned copy of
        services.validation_engine internally and calls the REAL
        run_validation() with the hostile per-symbol exception, so the
        genuine sanitization boundary is exercised inside an actual
        spawned child process, never a real (and here, irrelevant)
        network call."""
        monkeypatch.setattr(ve, "_validation_child_worker",
                             _worker_real_run_validation_hostile_per_symbol)
        # V-SEC1: /run is now X-Secret-protected — unrelated to this test's
        # own concern (hostile exception text sanitization), just needs a
        # valid header to keep exercising the real background run.
        import api.routers.validation as validation_router
        monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", "test-secret")

        run_resp = client.post(
            "/api/validation/run?horizon=short&universe=us",
            headers={"X-Secret": "test-secret"},
        )
        assert run_resp.status_code == 200
        status_resp = client.get("/api/validation/status")
        assert status_resp.status_code == 200
        _assert_no_hostile_text(status_resp.json())

        attempts_resp = client.get(
            "/api/validation/attempts?universe=us&horizon=short",
            headers={"X-Secret": "test-secret"},
        )
        assert attempts_resp.status_code == 200
        body = attempts_resp.json()
        assert body["available"] is True
        assert len(body["attempts"]) == 1
        # Every symbol's own exception is caught individually inside
        # run_validation's per-symbol loop (V-FRESH1B diagnostic path) —
        # it does not itself fail the whole run; the exact terminal
        # category (e.g. NO_RESULT_RUN_ID from zero signals surviving
        # every symbol raising) is incidental to THIS test's actual
        # concern. What matters is proven below: no hostile text survives
        # anywhere in the authenticated attempt-history payload either.
        _assert_no_hostile_text(body)

    def test_results_endpoint_has_no_raw_exception(self, client, monkeypatch):
        legacy_summary = {
            "benchmark_unavailable_reason": f"benchmark_fetch_failed: {HOSTILE_MESSAGE}",
            "buy_hit_rate_pct": 50.0,
        }
        fake_row = {"id": 1, "summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        resp = client.get("/api/validation/results?horizon=short&universe=us")
        assert resp.status_code == 200
        body = resp.json()
        _assert_no_hostile_text(body)
        assert body["benchmark_unavailable_reason"] == "benchmark_fetch_failed"


@pytest.mark.regression
class TestGenuineFieldsAndBehaviorUnaffected:
    """Items 15, 16, 17: sanitization must not touch genuine market,
    universe, horizon or progress fields, and a normal successful run's
    behavior is unchanged."""

    def test_genuine_fields_unchanged_on_a_failing_run(self, monkeypatch):
        def fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
            if sym == "AAPL":
                raise RuntimeError(HOSTILE_MESSAGE)
            return []

        _mock_io(monkeypatch, fake_backtest)
        metrics = ve.run_validation(horizon="short", universe="us")
        assert metrics["horizon"] == "short"
        assert metrics["universe"] == "us"
        assert metrics["market"] == "US"
        assert metrics["n_stocks_requested"] == len(ve.US_BASKET)
        assert metrics["job"]["market"] == "US"
        assert metrics["job"]["universe_id"] == "us"
        assert metrics["job"]["horizon"] == "short"

    def test_normal_successful_run_unaffected(self, monkeypatch):
        _mock_io(monkeypatch, lambda *a, **k: [])
        idx = pd.bdate_range("2020-01-01", periods=800, tz="UTC")
        import numpy as np
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
        assert metrics["job"]["status"] == "completed"
        assert metrics["job"]["failure_code"] is None


@pytest.mark.regression
class TestClaimedJobMismatchRemainsFailClosed:
    """Item 18: the pre-existing fail-closed identity invariant must be
    completely unaffected by sanitization — still raises, still marks the
    job failed — only the public failure_message text changed (a fixed
    string instead of the dynamic one)."""

    def test_mismatch_still_raises_and_fails_closed(self, monkeypatch):
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
        assert status["job"]["failure_code"] == "CLAIMED_JOB_MISMATCH"
        assert status["job"]["failure_message"] == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["CLAIMED_JOB_MISMATCH"]


@pytest.mark.regression
class TestStableCodesSurviveSerialization:
    """Item 14: every stable code/message must round-trip through
    json.dumps/json.loads unchanged (proves nothing non-serializable or
    format-dependent was introduced)."""

    def test_all_public_failure_messages_are_json_safe(self):
        round_tripped = json.loads(json.dumps(ve.VALIDATION_PUBLIC_FAILURE_MESSAGES))
        assert round_tripped == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES

    def test_sanitized_benchmark_reason_round_trips(self):
        for raw in (
            None,
            "insufficient_benchmark_history_for_horizon",
            "benchmark_fetch_failed",
            f"benchmark_fetch_failed: {HOSTILE_MESSAGE}",
        ):
            sanitized = ve._sanitize_benchmark_unavailable_reason(raw)
            round_tripped = json.loads(json.dumps({"r": sanitized}))["r"]
            assert round_tripped == sanitized
