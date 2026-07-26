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


def _mock_io(monkeypatch, backtest_fake):
    """Mock every I/O boundary run_validation() touches — no live network,
    no real DB read/write (matches test_validation_job_identity.py's
    _mock_io exactly, duplicated here so this file stays self-contained)."""
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
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
        fake_row = {"summary": json.dumps(legacy_summary)}
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
        fake_row = {"summary": json.dumps(legacy_summary)}
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
        fake_row = {"summary": json.dumps(legacy_summary)}
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
        fake_row = {"summary": json.dumps(legacy_summary)}
        monkeypatch.setattr(ve, "_init_db", lambda: None)
        monkeypatch.setattr(ve, "_USE_POSTGRES", False)
        monkeypatch.setattr(ve, "_fetchone", lambda sql_pg, sql_sq, params=(): fake_row)

        result = ve.get_latest_results(horizon="short", universe="us")
        assert result["benchmark_unavailable_reason"] == "insufficient_benchmark_history_for_horizon"

    def test_legacy_row_with_no_reason_key_unaffected(self, monkeypatch):
        """A row that never had benchmark_unavailable_reason at all (very
        old legacy rows, or a successful run) must not gain a spurious key."""
        legacy_summary = {"buy_hit_rate_pct": 61.0}
        fake_row = {"summary": json.dumps(legacy_summary)}
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
    def client(self):
        from fastapi.testclient import TestClient
        from api.main import app
        return TestClient(app)

    def test_status_endpoint_has_no_raw_exception(self, client, monkeypatch, caplog):
        def fake_backtest(sym, hor, benchmark_df, market, universe=None, **kwargs):
            if sym == "AAPL":
                raise RuntimeError(HOSTILE_MESSAGE)
            return []

        _mock_io(monkeypatch, fake_backtest)

        with caplog.at_level(logging.ERROR, logger="services.validation_engine"):
            run_resp = client.post("/api/validation/run?horizon=short&universe=us")
            assert run_resp.status_code == 200
            status_resp = client.get("/api/validation/status")

        assert status_resp.status_code == 200
        _assert_no_hostile_text(status_resp.json())
        assert "password=SECRET" in caplog.text

    def test_results_endpoint_has_no_raw_exception(self, client, monkeypatch):
        legacy_summary = {
            "benchmark_unavailable_reason": f"benchmark_fetch_failed: {HOSTILE_MESSAGE}",
            "buy_hit_rate_pct": 50.0,
        }
        fake_row = {"summary": json.dumps(legacy_summary)}
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
