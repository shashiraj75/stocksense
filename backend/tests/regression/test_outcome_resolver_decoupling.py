"""
Product Integrity Workstream #002K — Decouple outcome resolution from
Daily Picks generation.

Verifies:
  1. The periodic _outcome_resolver_loop (api/main.py) still invokes
     resolve_pending_outcomes() on its own schedule.
  2. resolve_pending_outcomes()'s existing functional behavior (market/horizon
     coverage, resolved/skipped counting, persistence calls) is unchanged.
  3. The new observability logging emits backlog counts and total elapsed
     time for a resolver sweep.

All tests are fully mocked — no DB, no network, no external providers.
"""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. Periodic loop still calls resolve_pending_outcomes()
# ─────────────────────────────────────────────────────────────────────────────

def test_outcome_resolver_loop_still_invokes_resolve_pending_outcomes():
    """_outcome_resolver_loop must still call resolve_pending_outcomes() each cycle."""
    import api.main as main_mod

    calls = []

    def fake_resolve():
        calls.append("resolved")

    async def _run_one_iteration():
        # Patch sleep so the 120s startup delay and the 6h cycle delay are
        # instantaneous; raise on the second sleep call to stop after one
        # iteration instead of looping forever.
        sleep_calls = {"n": 0}

        async def fake_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=fake_sleep), \
             patch("services.alpha_engine.outcome_logger.resolve_pending_outcomes",
                   side_effect=fake_resolve):
            try:
                await main_mod._outcome_resolver_loop()
            except asyncio.CancelledError:
                pass

    asyncio.run(_run_one_iteration())

    assert calls == ["resolved"], (
        "The periodic outcome resolver loop must still call resolve_pending_outcomes() "
        "on its own schedule, independent of Daily Picks generation."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. resolve_pending_outcomes() functional behavior is unchanged
# ─────────────────────────────────────────────────────────────────────────────

def _make_pending_row(symbol="AAPL", pred_date="2026-01-01"):
    return {"symbol": symbol, "horizon": "short", "pred_date": pred_date, "price": 100.0}


def test_resolve_pending_outcomes_covers_both_markets_and_all_horizons():
    """resolve_pending_outcomes() must still sweep IN and US across all three horizons."""
    from services.alpha_engine import outcome_logger

    seen_market_horizon_pairs = []

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        seen_market_horizon_pairs.append((market, horizon))
        return []

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome"):
        outcome_logger.resolve_pending_outcomes()

    assert set(seen_market_horizon_pairs) == {
        ("IN", "short"), ("IN", "medium"), ("IN", "long"),
        ("US", "short"), ("US", "medium"), ("US", "long"),
    }, f"Expected all 6 (market, horizon) pairs, got {seen_market_horizon_pairs}"


def test_resolve_pending_outcomes_logs_outcome_only_when_a_return_resolves():
    """Existing behavior: log_outcome() is called only when at least one return is non-None."""
    from services.alpha_engine import outcome_logger

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        if market == "IN" and horizon == "short":
            return [_make_pending_row("RELIANCE")]
        return []

    logged = []

    def fake_log_outcome(symbol, horizon, pred_date, r1, r5, r20, return_60d=None, market="IN"):
        logged.append((symbol, horizon, market))

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome", side_effect=fake_log_outcome), \
         patch.object(outcome_logger, "_fetch_return", return_value=1.23):
        outcome_logger.resolve_pending_outcomes()

    assert logged == [("RELIANCE", "short", "IN")]


def test_resolve_pending_outcomes_skips_when_no_return_resolves():
    """Existing behavior: a row with all-None returns is never logged (partial-window guard)."""
    from services.alpha_engine import outcome_logger

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        if market == "US" and horizon == "medium":
            return [_make_pending_row("AAPL")]
        return []

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome") as mock_log, \
         patch.object(outcome_logger, "_fetch_return", return_value=None):
        outcome_logger.resolve_pending_outcomes()

    mock_log.assert_not_called()


def test_resolve_pending_outcomes_never_raises_on_internal_error():
    """Existing behavior: exceptions inside the sweep are swallowed, not propagated."""
    from services.alpha_engine import outcome_logger

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=RuntimeError("boom")):
        # Must not raise.
        outcome_logger.resolve_pending_outcomes()


# ─────────────────────────────────────────────────────────────────────────────
# 3. New observability: backlog counts + total elapsed time are logged
# ─────────────────────────────────────────────────────────────────────────────

def test_resolver_logs_per_market_horizon_backlog_count(caplog):
    """Each (market, horizon) sweep must log how many unresolved records were found."""
    from services.alpha_engine import outcome_logger

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        if market == "IN" and horizon == "short":
            return [_make_pending_row("RELIANCE"), _make_pending_row("TCS")]
        return []

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome"), \
         patch.object(outcome_logger, "_fetch_return", return_value=None), \
         caplog.at_level(logging.INFO, logger="services.alpha_engine.outcome_logger"):
        outcome_logger.resolve_pending_outcomes()

    assert any(
        "IN/short" in r.message and "2 unresolved" in r.message
        for r in caplog.records
    ), f"Expected a backlog-count log line for IN/short with 2 records; got: {[r.message for r in caplog.records]}"


def test_resolver_logs_total_elapsed_time_and_summary_counts(caplog):
    """The sweep-complete summary must report examined/resolved/skipped counts and elapsed time."""
    from services.alpha_engine import outcome_logger

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        if market == "US" and horizon == "long":
            return [_make_pending_row("MSFT")]
        return []

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome"), \
         patch.object(outcome_logger, "_fetch_return", return_value=5.0), \
         caplog.at_level(logging.INFO, logger="services.alpha_engine.outcome_logger"):
        outcome_logger.resolve_pending_outcomes()

    summary_lines = [r.message for r in caplog.records if "sweep complete" in r.message]
    assert len(summary_lines) == 1, f"Expected exactly one sweep-complete summary; got: {summary_lines}"
    summary = summary_lines[0]
    assert "examined=1" in summary
    assert "resolved=1" in summary
    assert "skipped=0" in summary
    assert "elapsed=" in summary


def test_resolver_logs_elapsed_time_even_on_error(caplog):
    """An internal error must still produce an elapsed-time log line, not just a bare error."""
    from services.alpha_engine import outcome_logger

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=RuntimeError("boom")), \
         caplog.at_level(logging.WARNING, logger="services.alpha_engine.outcome_logger"):
        outcome_logger.resolve_pending_outcomes()

    assert any(
        "sweep error" in r.message and "after" in r.message
        for r in caplog.records
    ), f"Expected an elapsed-time-bearing error log line; got: {[r.message for r in caplog.records]}"


def test_resolver_observability_does_not_log_secrets_or_full_payloads(caplog):
    """Observability logs must stay confined to counts/timing — no per-symbol or payload detail."""
    from services.alpha_engine import outcome_logger

    def fake_get_unresolved(horizon, min_days_old, market="IN"):
        if market == "IN" and horizon == "short":
            return [_make_pending_row("SECRET_LOOKING_SYMBOL_XYZ")]
        return []

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=fake_get_unresolved), \
         patch("services.alpha_engine.store.log_outcome"), \
         patch.object(outcome_logger, "_fetch_return", return_value=None), \
         caplog.at_level(logging.INFO, logger="services.alpha_engine.outcome_logger"):
        outcome_logger.resolve_pending_outcomes()

    all_messages = " ".join(r.message for r in caplog.records)
    assert "SECRET_LOOKING_SYMBOL_XYZ" not in all_messages, (
        "Resolver observability logs must not include per-symbol detail, only counts/timing."
    )
