"""
Release 12C — India screener rate-limit recovery & universe-selection
observability.

Root cause (2026-07-04 IN incident): a transient upstream "Too Many
Requests" rate-limit on the FIRST screener attempt fell straight through to
the static NIFTY-100 fallback with zero retry — genuinely transient failures
were treated identically to permanent ones, and the resulting job-status
snapshot's overloaded `total` field (381 = candidates × 3 horizons) was
mistaken for universe breadth by an operator reviewing the incident.

These tests mock `_collect_in_screener_symbols` directly (not yf.screen) —
the retry/backoff/classification logic under test wraps that call, and
`test_in_universe_expansion.py` already covers yf.screen-level pagination
behavior in detail. `time.sleep` is patched everywhere a retry could sleep —
no test in this file waits in real time.
"""

from unittest.mock import MagicMock, patch

import pytest

import services.daily_picks as dp


def _big_universe(n=120, prefix="OK"):
    return [f"{prefix}{i:04d}" for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# Retry and fallback behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestRetrySucceedsOnSecondAttempt:
    def test_rate_limit_then_success_uses_screener_not_fallback(self):
        symbols = _big_universe(120)
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=[
                Exception("Too Many Requests. Rate limited. Try after a while."),
                (symbols, 120),
            ],
        ) as mock_collect, patch.object(dp.time, "sleep") as mock_sleep:
            result = dp._get_universe_by_mcap("IN")

        out_symbols, universe_used, universe_degraded, screener_raw_count, meta = result
        assert mock_collect.call_count == 2
        assert universe_used == "screener"
        assert universe_degraded is False
        assert out_symbols == symbols
        assert screener_raw_count == 120
        assert meta["attempts"] == 2
        assert meta["reason"] == "healthy_screener_universe"
        assert meta["error_category"] == "none"
        # Backoff happened once (between attempt 1 and 2), deterministically, no real sleep.
        mock_sleep.assert_called_once()


class TestRetryExhaustedOnRateLimit:
    def test_every_attempt_rate_limited_stops_at_exact_bound(self):
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("Too Many Requests. Rate limited. Try after a while."),
        ) as mock_collect, patch.object(dp.time, "sleep") as mock_sleep:
            result = dp._get_universe_by_mcap("IN")

        out_symbols, universe_used, universe_degraded, screener_raw_count, meta = result
        assert mock_collect.call_count == dp._IN_SCREENER_MAX_ATTEMPTS
        assert universe_used == "static_fallback"
        assert universe_degraded is True
        assert out_symbols == list(dp._NIFTY_100)
        assert screener_raw_count == 0
        assert meta["attempts"] == dp._IN_SCREENER_MAX_ATTEMPTS
        assert meta["reason"] == "screener_rate_limit_exhausted"
        assert meta["error_category"] == "rate_limited"
        # Backoff between each retry, never after the final (non-retried) attempt.
        assert mock_sleep.call_count == dp._IN_SCREENER_MAX_ATTEMPTS - 1


class TestInsufficientSymbolsNoException:
    def test_small_result_without_exception_does_not_retry(self):
        small = _big_universe(25, prefix="TINY")
        with patch.object(
            dp, "_collect_in_screener_symbols", return_value=(small, 25),
        ) as mock_collect, patch.object(dp.time, "sleep") as mock_sleep:
            result = dp._get_universe_by_mcap("IN")

        out_symbols, universe_used, universe_degraded, screener_raw_count, meta = result
        assert mock_collect.call_count == 1  # no unnecessary retry
        assert universe_used == "static_fallback"
        assert universe_degraded is True
        assert screener_raw_count == 25
        assert meta["reason"] == "screener_insufficient_symbols"
        assert meta["error_category"] == "insufficient_symbols"
        mock_sleep.assert_not_called()


class TestNonTransientErrorDoesNotRetry:
    def test_non_transient_error_falls_back_without_retry(self):
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=ValueError("Invalid field name in query"),
        ) as mock_collect, patch.object(dp.time, "sleep") as mock_sleep:
            result = dp._get_universe_by_mcap("IN")

        out_symbols, universe_used, universe_degraded, screener_raw_count, meta = result
        assert mock_collect.call_count == 1  # non-transient — never retried
        assert universe_used == "static_fallback"
        assert universe_degraded is True
        assert out_symbols == list(dp._NIFTY_100)
        assert meta["attempts"] == 1
        assert meta["reason"] == "screener_non_transient_error"
        assert meta["error_category"] == "non_transient_error"
        mock_sleep.assert_not_called()

    def test_transient_upstream_error_does_retry_and_exhausts_bound(self):
        """A timeout-style message is transient and DOES retry, distinguishing
        it from the non-transient case above."""
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("Connection timed out"),
        ) as mock_collect, patch.object(dp.time, "sleep") as mock_sleep:
            result = dp._get_universe_by_mcap("IN")

        _, universe_used, universe_degraded, _, meta = result
        assert mock_collect.call_count == dp._IN_SCREENER_MAX_ATTEMPTS
        assert universe_used == "static_fallback"
        assert universe_degraded is True
        assert meta["reason"] == "screener_transient_failure_exhausted"
        assert meta["error_category"] == "transient_upstream_error"
        assert mock_sleep.call_count == dp._IN_SCREENER_MAX_ATTEMPTS - 1


class TestBackoffIsBoundedAndDeterministic:
    def test_backoff_delay_grows_exponentially_not_linearly(self):
        """With jitter pinned to 0 (deterministic), delay after failed attempt N
        must be base * 2^(N-1), not base * N."""
        with patch.object(dp.random, "uniform", return_value=0.0):
            delays = [dp._in_screener_backoff_delay(n) for n in range(1, 5)]

        base = dp._IN_SCREENER_RETRY_BACKOFF_SECONDS
        expected_exponential = [base * (2 ** (n - 1)) for n in range(1, 5)]
        # None of these small attempt numbers hit the cap at default settings,
        # so the raw exponential values must match exactly with jitter at 0.
        for d, exp_exponential, n in zip(delays, expected_exponential, range(1, 5)):
            if exp_exponential < dp._IN_SCREENER_RETRY_MAX_DELAY_SECONDS:
                assert d == pytest.approx(exp_exponential)
        # Explicitly not linear: doubling ratio between consecutive delays
        # (before the cap kicks in), not a constant additive step.
        assert delays[1] == pytest.approx(delays[0] * 2)
        assert delays[2] == pytest.approx(delays[1] * 2)

    def test_jitter_stays_within_documented_bounded_range(self):
        """Jitter alone (isolated from the exponential base) must fall within
        [0, _IN_SCREENER_RETRY_JITTER_SECONDS)."""
        with patch.object(dp.random, "uniform", return_value=dp._IN_SCREENER_RETRY_JITTER_SECONDS - 0.001) as mock_uniform:
            delay = dp._in_screener_backoff_delay(1)
        mock_uniform.assert_called_once_with(0, dp._IN_SCREENER_RETRY_JITTER_SECONDS)
        base = dp._IN_SCREENER_RETRY_BACKOFF_SECONDS
        jitter_applied = delay - base * (2 ** 0)
        assert 0 <= jitter_applied < dp._IN_SCREENER_RETRY_JITTER_SECONDS + 1e-9

    def test_delay_never_exceeds_the_finite_cap(self):
        """Even for a large attempt number (exponential would otherwise be huge)
        and maximum jitter, the delay must never exceed the named ceiling."""
        with patch.object(dp.random, "uniform", return_value=dp._IN_SCREENER_RETRY_JITTER_SECONDS):
            delay = dp._in_screener_backoff_delay(20)  # 2^19 * base, astronomically large uncapped
        assert delay == dp._IN_SCREENER_RETRY_MAX_DELAY_SECONDS

    def test_no_sleep_after_final_permitted_attempt(self):
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("429 Too Many Requests"),
        ), patch.object(dp.time, "sleep") as mock_sleep:
            dp._get_universe_by_mcap("IN")

        # Bounded — exactly attempts - 1 sleeps; never one after the last attempt.
        assert mock_sleep.call_count == dp._IN_SCREENER_MAX_ATTEMPTS - 1

    def test_full_retry_sequence_uses_exponential_delays_never_real_time(self):
        """End-to-end through _get_universe_by_mcap: with jitter pinned to 0,
        the actual sleep() calls during a full rate-limit-exhausted run must
        match the exponential schedule exactly — never linear, never real sleep."""
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("429 Too Many Requests"),
        ), patch.object(dp.time, "sleep") as mock_sleep, \
             patch.object(dp.random, "uniform", return_value=0.0):
            dp._get_universe_by_mcap("IN")

        base = dp._IN_SCREENER_RETRY_BACKOFF_SECONDS
        expected = [base * (2 ** (n - 1)) for n in range(1, dp._IN_SCREENER_MAX_ATTEMPTS)]
        actual = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual == pytest.approx(expected)
        assert len(actual) == dp._IN_SCREENER_MAX_ATTEMPTS - 1


class TestRetryStillDoesNotApplyToNonRetryableOutcomes:
    def test_insufficient_symbols_result_still_never_sleeps(self):
        small = _big_universe(25, prefix="TINY")
        with patch.object(dp, "_collect_in_screener_symbols", return_value=(small, 25)), \
             patch.object(dp.time, "sleep") as mock_sleep:
            dp._get_universe_by_mcap("IN")
        mock_sleep.assert_not_called()

    def test_non_transient_error_still_never_sleeps(self):
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=ValueError("Invalid field name in query"),
        ), patch.object(dp.time, "sleep") as mock_sleep:
            dp._get_universe_by_mcap("IN")
        mock_sleep.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Error classification (unit-level)
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorClassification:
    @pytest.mark.parametrize("message,expected", [
        ("Too Many Requests. Rate limited. Try after a while.", "rate_limited"),
        ("429 Client Error", "rate_limited"),
        ("Connection timed out", "transient_upstream_error"),
        ("503 Service Unavailable", "transient_upstream_error"),
        ("Invalid field name in query", "non_transient_error"),
        ("KeyError: 'quotes'", "non_transient_error"),
    ])
    def test_classify_screener_error(self, message, expected):
        assert dp._classify_screener_error(Exception(message)) == expected

    def test_classification_never_echoes_raw_message(self):
        # The category itself must be a small closed set of stable strings —
        # never the raw exception text, regardless of what's thrown.
        category = dp._classify_screener_error(Exception("some upstream secret-looking token abc123"))
        assert category in {"rate_limited", "transient_upstream_error", "non_transient_error"}
        assert "secret" not in category and "abc123" not in category


# ─────────────────────────────────────────────────────────────────────────────
# Persistence and API contract
# ─────────────────────────────────────────────────────────────────────────────

class TestJobProgressPersistence:
    def test_new_fields_persist_into_job_record(self):
        from services.postgres_store import record_daily_picks_job_progress

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        with patch("services.postgres_store._get_pool", return_value=mock_pool):
            record_daily_picks_job_progress(
                "job-1", "phase_0b_done", 50, 50,
                universe_used="static_fallback", universe_degraded=True,
                screener_raw_count=25,
                universe_candidate_count=134,
                universe_selection_attempts=3,
                universe_selection_reason="screener_rate_limit_exhausted",
                universe_selection_error_category="rate_limited",
            )

        sql, params = mock_conn.execute.call_args[0]
        assert "screener_raw_count" in sql
        assert "universe_candidate_count" in sql
        assert "universe_selection_attempts" in sql
        assert "universe_selection_reason" in sql
        assert "universe_selection_error_category" in sql
        assert "phase_task_processed" in sql
        assert "phase_task_total" in sql
        assert 25 in params
        assert 134 in params
        assert 3 in params
        assert "screener_rate_limit_exhausted" in params
        assert "rate_limited" in params

    def test_phase_task_fields_written_even_without_universe_metadata(self):
        """Every phase update (not just phase_0b_done) writes phase_task_processed/
        phase_task_total as explicit aliases of processed/total."""
        from services.postgres_store import record_daily_picks_job_progress

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        with patch("services.postgres_store._get_pool", return_value=mock_pool):
            record_daily_picks_job_progress("job-1", "phase_1", 30, 150)

        sql, params = mock_conn.execute.call_args[0]
        assert "phase_task_processed" in sql
        assert "phase_task_total" in sql
        assert 30 in params
        assert 150 in params


class TestStatusApiContract:
    def test_status_exposes_all_new_additive_metadata(self):
        from fastapi.testclient import TestClient
        from api.main import app
        from datetime import datetime, timezone

        fake_job = {
            "job_id": "job-xyz",
            "status": "completed",
            "phase": "persisting",
            "processed": 381,
            "total": 381,
            "last_runner_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "last_progress_at": datetime.now(timezone.utc).isoformat(),
            "universe_used": "static_fallback",
            "universe_degraded": True,
            "screener_raw_count": 0,
            "universe_candidate_count": 134,
            "universe_selection_attempts": 3,
            "universe_selection_reason": "screener_rate_limit_exhausted",
            "universe_selection_error_category": "rate_limited",
            "phase_task_processed": 381,
            "phase_task_total": 381,
        }

        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.daily_picks.picks_generated_today", return_value=True), \
             patch("services.postgres_store.get_latest_daily_picks_job", return_value=fake_job):
            client = TestClient(app)
            resp = client.get("/api/picks/status?market=IN")

        assert resp.status_code == 200
        data = resp.json()
        assert data["universe_used"] == "static_fallback"
        assert data["universe_degraded"] is True
        assert data["screener_raw_count"] == 0
        assert data["universe_candidate_count"] == 134
        assert data["universe_selection_attempts"] == 3
        assert data["universe_selection_reason"] == "screener_rate_limit_exhausted"
        assert data["universe_selection_error_category"] == "rate_limited"
        # The literal 381 total/processed values are still present (backward
        # compatible) but are also mirrored explicitly as phase_task_* —
        # a consumer can use the unambiguous field instead of guessing.
        assert data["total"] == 381
        assert data["phase_task_total"] == 381
        # 381 must never be misrepresentable as universe size: the explicit
        # universe_candidate_count field (134) is distinct from phase_task_total (381).
        assert data["universe_candidate_count"] != data["phase_task_total"]

    def test_status_handles_historical_job_missing_new_fields(self):
        """A job row recorded before Release 12C has no new columns — dict.get()
        returns None, and the endpoint must not error or fabricate values."""
        from fastapi.testclient import TestClient
        from api.main import app
        from datetime import datetime, timezone

        legacy_job = {
            "job_id": "job-old",
            "status": "completed",
            "phase": "persisting",
            "processed": None,
            "total": None,
            "last_runner_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "last_progress_at": datetime.now(timezone.utc).isoformat(),
            "universe_used": "screener",
            "universe_degraded": False,
            # no screener_raw_count / universe_candidate_count / etc. keys at all
        }

        with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
             patch("services.daily_picks.picks_generated_today", return_value=True), \
             patch("services.postgres_store.get_latest_daily_picks_job", return_value=legacy_job):
            client = TestClient(app)
            resp = client.get("/api/picks/status?market=IN")

        assert resp.status_code == 200
        data = resp.json()
        assert data["screener_raw_count"] is None
        assert data["universe_candidate_count"] is None
        assert data["universe_selection_reason"] is None
        assert data["universe_used"] == "screener"  # existing fields unaffected


class TestDailyResultApiContract:
    def test_daily_endpoint_exposes_universe_selection_metadata(self):
        from fastapi.testclient import TestClient
        from api.main import app

        fake_payload = {
            "generated_at": "2026-07-04T02:00:00+00:00",
            "market": "IN",
            "currency": "₹",
            "picks": {"short": [], "medium": [], "long": []},
            "screener_raw_count": 0,
            "universe_candidate_count": 134,
            "universe_selection_attempts": 3,
            "universe_selection_reason": "screener_rate_limit_exhausted",
            "universe_selection_error_category": "rate_limited",
            "phase_task_processed": 381,
            "phase_task_total": 381,
            "universe_used": "static_fallback",
            "universe_degraded": True,
        }
        with patch("services.daily_picks.get_cached_picks", return_value=fake_payload), \
             patch("services.daily_picks._generating", {"IN": False, "US": False}):
            client = TestClient(app)
            resp = client.get("/api/picks/daily?market=IN")

        assert resp.status_code == 200
        data = resp.json()
        assert data["universe_candidate_count"] == 134
        assert data["universe_selection_reason"] == "screener_rate_limit_exhausted"
        assert data["phase_task_total"] == 381
        # The literal incident scenario: 381 is a task count, never mistaken
        # for universe breadth (134) by this contract.
        assert data["universe_candidate_count"] != data["phase_task_total"]

    def test_daily_endpoint_backward_compatible_with_historical_payload(self):
        """A payload cached before Release 12C has none of the new keys — the
        endpoint must still return 200 with the existing fields intact."""
        from fastapi.testclient import TestClient
        from api.main import app

        legacy_payload = {
            "generated_at": "2026-06-01T02:00:00+00:00",
            "market": "IN",
            "currency": "₹",
            "picks": {"short": [], "medium": [], "long": []},
            "universe_used": "screener",
            "universe_degraded": False,
        }
        with patch("services.daily_picks.get_cached_picks", return_value=legacy_payload), \
             patch("services.daily_picks._generating", {"IN": False, "US": False}):
            client = TestClient(app)
            resp = client.get("/api/picks/daily?market=IN")

        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "IN"
        assert data["universe_used"] == "screener"
        assert "universe_candidate_count" not in data  # absent, not fabricated


# ─────────────────────────────────────────────────────────────────────────────
# Isolation and regression
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketIsolation:
    def test_in_universe_selection_never_touches_us_screener(self):
        """Selecting IN's universe (including retries) must never invoke the
        US code path (a single yf.screen(count=...) call) at all."""
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("Too Many Requests. Rate limited."),
        ), patch.object(dp.time, "sleep"), patch.object(dp.yf, "screen") as mock_us_screen:
            dp._get_universe_by_mcap("IN")

        mock_us_screen.assert_not_called()

    def test_job_progress_write_targets_only_the_given_job_id(self):
        """record_daily_picks_job_progress's UPDATE is always scoped by job_id
        — an IN job's write can never affect a US job's row."""
        from services.postgres_store import record_daily_picks_job_progress

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        with patch("services.postgres_store._get_pool", return_value=mock_pool):
            record_daily_picks_job_progress(
                "in-job-123", "phase_0b_done", 50, 50,
                universe_used="static_fallback", universe_degraded=True,
                screener_raw_count=0, universe_candidate_count=134,
                universe_selection_attempts=3,
                universe_selection_reason="screener_rate_limit_exhausted",
                universe_selection_error_category="rate_limited",
            )

        sql, params = mock_conn.execute.call_args[0]
        assert "WHERE job_id = %s" in sql
        assert params[-1] == "in-job-123"


class TestExistingFallbackBehaviorRemainsSafeAndBounded:
    def test_static_fallback_list_is_still_bounded_nifty_100(self):
        with patch.object(
            dp, "_collect_in_screener_symbols",
            side_effect=Exception("Too Many Requests. Rate limited."),
        ), patch.object(dp.time, "sleep"):
            symbols, used, degraded, _, _ = dp._get_universe_by_mcap("IN")
        assert symbols == list(dp._NIFTY_100)
        assert len(symbols) < 300
        assert used == "static_fallback"
        assert degraded is True


# ─────────────────────────────────────────────────────────────────────────────
# Release 12C final safety correction: query-construction failure must not
# escape either market's selection path, and no log line may ever contain
# raw exception text.
# ─────────────────────────────────────────────────────────────────────────────

class TestQueryConstructionFailureSafety:
    def test_in_query_construction_failure_uses_static_fallback_with_zero_attempts(self):
        with patch.object(dp.yf, "EquityQuery", side_effect=Exception("bad query shape")), \
             patch.object(dp, "_collect_in_screener_symbols") as mock_collect, \
             patch.object(dp.time, "sleep") as mock_sleep:
            symbols, used, degraded, raw_count, meta = dp._get_universe_by_mcap("IN")

        assert used == "static_fallback"
        assert degraded is True
        assert symbols == list(dp._NIFTY_100)
        assert raw_count == 0
        assert meta["attempts"] == 0
        assert meta["reason"] == "screener_non_transient_error"
        assert meta["error_category"] == "non_transient_error"
        # No screener request was ever attempted, and therefore nothing to retry.
        mock_collect.assert_not_called()
        mock_sleep.assert_not_called()

    def test_us_query_construction_failure_uses_anchor_without_escaping_or_retry(self):
        with patch.object(dp.yf, "EquityQuery", side_effect=Exception("bad query shape")), \
             patch.object(dp.yf, "screen") as mock_screen, \
             patch.object(dp.time, "sleep") as mock_sleep:
            symbols, used, degraded, raw_count, meta = dp._get_universe_by_mcap("US")

        assert used == "anchor"
        assert degraded is True
        assert symbols == list(dp._US_MEGACAP_100)
        assert raw_count is None
        # US has no retry, with or without a query-construction failure.
        mock_screen.assert_not_called()
        mock_sleep.assert_not_called()


class TestRetryLogSanitization:
    def test_retry_warning_never_logs_raw_exception_text(self, caplog):
        import logging
        marker = "secret-looking-marker-DO-NOT-LOG"
        with caplog.at_level(logging.WARNING, logger="services.daily_picks"):
            with patch.object(
                dp, "_collect_in_screener_symbols",
                side_effect=Exception(f"Too Many Requests. Rate limited. {marker}"),
            ), patch.object(dp.time, "sleep"):
                dp._get_universe_by_mcap("IN")

        all_log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert marker not in all_log_text
        assert "rate_limited" in all_log_text

    def test_us_screener_failure_warning_never_logs_raw_exception_text(self, caplog):
        import logging
        marker = "secret-looking-marker-DO-NOT-LOG"
        with caplog.at_level(logging.WARNING, logger="services.daily_picks"):
            with patch.object(
                dp.yf, "screen",
                side_effect=Exception(f"Connection timed out {marker}"),
            ):
                dp._get_universe_by_mcap("US")

        all_log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert marker not in all_log_text
        assert "transient_upstream_error" in all_log_text
