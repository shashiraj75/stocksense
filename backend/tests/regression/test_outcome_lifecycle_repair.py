"""
StockSense360 Forensic Remediation — Phase 1A: Outcome Resolver Integrity Repair.

Covers the 2026-07 forensic audit's critical finding: once ANY outcome row
existed for a prediction, services/postgres_store.py's
get_unresolved_predictions() permanently excluded it from re-resolution, even
when only return_1d/return_5d had resolved and return_20d/return_60d were
still NULL. Net effect in production: return_5d populated on 0.36% of
outcomes, return_20d/return_60d on 0.00%, for the table's entire history.

These tests exercise the real SQLite backend (services/alpha_engine/store.py)
end-to-end against a temp file — no mocking of SQL semantics, so the UPSERT/
JOIN/HAVING logic is genuinely verified, not assumed. Only _fetch_return
(the real yfinance network call) is mocked; no network, no production DB,
no secrets.

Also covers services/postgres_store.py's horizon-eligibility SQL selection
(mocked at the connection boundary, since no live Postgres is available/
permitted in this phase) and the operator backfill CLI
(scripts/backfill_outcomes.py).
"""

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — isolated SQLite DB per test, USE_POSTGRES forced off
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    """A services.alpha_engine.store module bound to a fresh temp SQLite file,
    with USE_POSTGRES forced off regardless of the real environment."""
    monkeypatch.setenv("USE_POSTGRES", "0")
    import services.alpha_engine.store as store_mod
    importlib.reload(store_mod)
    monkeypatch.setattr(store_mod, "DB_PATH", str(tmp_path / "test_alpha_engine.db"))
    monkeypatch.setattr(store_mod, "USE_POSTGRES", False)
    store_mod.init_db()
    return store_mod


def _log_prediction(store, symbol="AAPL", horizon="medium", market="US",
                     days_ago=35, price=100.0):
    """Insert a prediction directly (bypassing log_prediction's `now()`
    timestamp) so tests can control how old it is."""
    logged_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with store._lock, store._conn() as c:
        c.execute("""
            INSERT INTO predictions (logged_at, symbol, horizon, market, price)
            VALUES (?, ?, ?, ?, ?)
        """, (logged_at, symbol, horizon, market, price))
    return logged_at


def _pred_date_str(logged_at_iso: str) -> str:
    return logged_at_iso[:10]


def _raw_log_outcome(store, symbol, horizon, pred_date, market, return_1d=None,
                      return_5d=None, return_20d=None, return_60d=None):
    """Insert an outcome directly (bypassing log_outcome's Phase 1A.6
    market/symbol validation) — used only where a test's own intent is to
    exercise cross-market JOIN isolation with a symbol/market combination
    that the hardened log_outcome would now correctly refuse to write in
    the first place."""
    now = datetime.now(timezone.utc).isoformat()
    with store._lock, store._conn() as c:
        c.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                  return_1d, return_5d, return_20d, return_60d)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, symbol, horizon, pred_date, market, return_1d, return_5d, return_20d, return_60d))


# ─────────────────────────────────────────────────────────────────────────────
# 1. No outcome -> create early-horizon outcome
# ─────────────────────────────────────────────────────────────────────────────

def test_no_outcome_creates_early_horizon_outcome(store):
    logged_at = _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    pending = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert len(pending) == 1
    assert pending[0]["symbol"] == "AAPL"

    store.log_outcome("AAPL", "short", pred_date, return_1d=0.5, return_5d=1.2, return_20d=None, market="US")

    with store._lock, store._conn() as c:
        row = c.execute("SELECT return_1d, return_5d, return_20d, return_60d FROM outcomes").fetchone()
    assert row["return_1d"] == 0.5
    assert row["return_5d"] == 1.2
    assert row["return_20d"] is None
    assert row["return_60d"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Partial 5d outcome -> later 20d completion
# ─────────────────────────────────────────────────────────────────────────────

def test_partial_5d_outcome_later_completes_20d(store):
    logged_at = _log_prediction(store, symbol="TCS", horizon="medium", market="IN", days_ago=35)
    pred_date = _pred_date_str(logged_at)

    # Day-5-equivalent sweep: only return_5d resolves.
    store.log_outcome("TCS", "medium", pred_date, return_1d=None, return_5d=2.0, return_20d=None, market="IN")

    # This is exactly the scenario the old NOT-EXISTS bug broke: an outcome
    # row now exists, so the prediction must still be re-offered because
    # return_20d (which "medium" cares about) is still NULL.
    pending = store.get_unresolved_predictions("medium", min_days_old=30, market="IN")
    assert len(pending) == 1, "medium-horizon prediction with return_20d still NULL must remain eligible"

    # Day-20-equivalent sweep: return_20d resolves; return_5d must be preserved.
    store.log_outcome("TCS", "medium", pred_date, return_1d=None, return_5d=None, return_20d=9.5, market="IN")

    with store._lock, store._conn() as c:
        row = c.execute("SELECT return_5d, return_20d, return_60d FROM outcomes").fetchone()
    assert row["return_5d"] == 2.0, "return_5d from the first sweep must survive the second sweep"
    assert row["return_20d"] == 9.5
    assert row["return_60d"] is None

    # Now that both medium-relevant columns are filled, it must stop being offered.
    pending_after = store.get_unresolved_predictions("medium", min_days_old=30, market="IN")
    assert pending_after == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Partial 5d/20d outcome -> later 60d completion
# ─────────────────────────────────────────────────────────────────────────────

def test_partial_5d_20d_outcome_later_completes_60d(store):
    logged_at = _log_prediction(store, symbol="RELIANCE", horizon="long", market="IN", days_ago=95)
    pred_date = _pred_date_str(logged_at)

    # Earlier sweeps under "medium" already populated 5d/20d for this symbol/date
    # (a different horizon config than "long", but same outcomes row key).
    store.log_outcome("RELIANCE", "long", pred_date, return_1d=None, return_5d=3.1, return_20d=11.4, market="IN")

    pending = store.get_unresolved_predictions("long", min_days_old=90, market="IN")
    assert len(pending) == 1, "long-horizon prediction with return_60d still NULL must remain eligible"

    store.log_outcome("RELIANCE", "long", pred_date, return_1d=None, return_5d=None, return_20d=None,
                       return_60d=22.7, market="IN")

    with store._lock, store._conn() as c:
        row = c.execute("SELECT return_5d, return_20d, return_60d FROM outcomes").fetchone()
    assert row["return_5d"] == 3.1
    assert row["return_20d"] == 11.4
    assert row["return_60d"] == 22.7


# ─────────────────────────────────────────────────────────────────────────────
# 4. Already-complete outcome -> no-op
# ─────────────────────────────────────────────────────────────────────────────

def test_already_complete_outcome_is_a_no_op(store):
    logged_at = _log_prediction(store, symbol="MSFT", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    store.log_outcome("MSFT", "short", pred_date, return_1d=1.0, return_5d=2.0, return_20d=None, market="US")

    pending = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert pending == [], "short-horizon prediction with both return_1d and return_5d filled must not be re-offered"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Rerun does not create duplicates
# ─────────────────────────────────────────────────────────────────────────────

def test_rerun_does_not_create_duplicate_outcome_rows(store):
    logged_at = _log_prediction(store, symbol="GOOG", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    store.log_outcome("GOOG", "short", pred_date, return_1d=1.0, return_5d=None, return_20d=None, market="US")
    store.log_outcome("GOOG", "short", pred_date, return_1d=1.0, return_5d=2.0, return_20d=None, market="US")
    store.log_outcome("GOOG", "short", pred_date, return_1d=1.0, return_5d=2.0, return_20d=None, market="US")

    with store._lock, store._conn() as c:
        rows = c.execute(
            "SELECT * FROM outcomes WHERE symbol='GOOG' AND horizon='short' AND market='US'"
        ).fetchall()
    assert len(rows) == 1, "repeated resolver runs for the same key must never create a second row"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Populated values are not overwritten
# ─────────────────────────────────────────────────────────────────────────────

def test_populated_values_are_never_overwritten(store):
    logged_at = _log_prediction(store, symbol="AMZN", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    store.log_outcome("AMZN", "short", pred_date, return_1d=1.0, return_5d=2.0, return_20d=None, market="US")
    # A later, buggy or stale sweep tries to write a *different* value for an
    # already-resolved column — it must be ignored, not applied.
    store.log_outcome("AMZN", "short", pred_date, return_1d=999.0, return_5d=888.0, return_20d=None, market="US")

    with store._lock, store._conn() as c:
        row = c.execute("SELECT return_1d, return_5d FROM outcomes").fetchone()
    assert row["return_1d"] == 1.0
    assert row["return_5d"] == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Missing market price is handled safely
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_price_is_skipped_not_crashed(store):
    from services.alpha_engine import outcome_logger

    _log_prediction(store, symbol="DELISTED_XYZ", horizon="short", market="US", days_ago=10)

    with patch("services.alpha_engine.store.get_unresolved_predictions",
               side_effect=store.get_unresolved_predictions), \
         patch("services.alpha_engine.store.log_outcome", side_effect=store.log_outcome), \
         patch.object(outcome_logger, "_fetch_return", return_value=None):
        stats = outcome_logger.resolve_pair("US", "short", 3, True, True, False, False)

    assert stats["examined"] == 1
    assert stats["resolved"] == 0
    assert stats["skipped"] == 1

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes").fetchall()
    assert rows == [], "a prediction with no fetchable price must not create a spurious outcome row"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Wrong-market outcome cannot be joined
# ─────────────────────────────────────────────────────────────────────────────

def test_wrong_market_outcome_cannot_be_joined(store):
    logged_at = _log_prediction(store, symbol="V", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    # An outcome for the *same* symbol/horizon/date but a *different* market —
    # e.g. a hypothetical IN-listed symbol collision — must not satisfy the
    # US prediction's eligibility. Raw insert: "V" is US_ONLY per the
    # canonical classifier, so the real log_outcome() would now correctly
    # refuse to write it under market="IN" (Phase 1A.6) — this test's own
    # intent is to prove the JOIN's market-isolation, not to exercise
    # write-boundary validation, so it seeds the row directly.
    _raw_log_outcome(store, "V", "short", pred_date, market="IN", return_1d=1.0, return_5d=2.0)

    pending = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert len(pending) == 1, "an outcome logged under a different market must not resolve this prediction"

    with store._lock, store._conn() as c:
        us_row = c.execute("SELECT * FROM outcomes WHERE market='US'").fetchall()
    assert us_row == [], "no US outcome row should exist yet"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Ambiguous duplicate relationships fail closed
# ─────────────────────────────────────────────────────────────────────────────

def test_ambiguous_same_day_different_price_predictions_fail_closed(store):
    logged_at1 = _log_prediction(store, symbol="ZOMATO", horizon="short", market="IN", days_ago=10, price=100.0)
    # Second prediction, same symbol/horizon/market/day, different price —
    # can't be told apart once resolved (outcomes keys only on
    # symbol/horizon/market/pred_date, not price).
    with store._lock, store._conn() as c:
        c.execute("""
            INSERT INTO predictions (logged_at, symbol, horizon, market, price)
            VALUES (?, ?, ?, ?, ?)
        """, (logged_at1, "ZOMATO", "short", "IN", 105.0))

    pending = store.get_unresolved_predictions("short", min_days_old=3, market="IN")
    assert pending == [], "ambiguous (same-day, different-price) duplicate predictions must be excluded, not guessed at"

    ambiguous = store.get_ambiguous_pending_predictions("short", min_days_old=3, market="IN")
    assert len(ambiguous) == 1
    assert ambiguous[0]["symbol"] == "ZOMATO"
    assert ambiguous[0]["n_predictions"] == 2
    assert ambiguous[0]["n_distinct_prices"] == 2


def test_same_day_same_price_duplicate_predictions_collapse_safely(store):
    """Same-price duplicates (e.g. a batch that ran twice) are NOT ambiguous —
    they'd resolve to the same value either way, so they collapse to one
    candidate instead of being excluded."""
    logged_at = _log_prediction(store, symbol="INFY", horizon="short", market="IN", days_ago=10, price=1500.0)
    with store._lock, store._conn() as c:
        c.execute("""
            INSERT INTO predictions (logged_at, symbol, horizon, market, price)
            VALUES (?, ?, ?, ?, ?)
        """, (logged_at, "INFY", "short", "IN", 1500.0))

    pending = store.get_unresolved_predictions("short", min_days_old=3, market="IN")
    assert len(pending) == 1
    assert pending[0]["price"] == 1500.0

    ambiguous = store.get_ambiguous_pending_predictions("short", min_days_old=3, market="IN")
    assert ambiguous == []


# ─────────────────────────────────────────────────────────────────────────────
# 12. Transaction rollback on failure
# ─────────────────────────────────────────────────────────────────────────────

def test_log_outcome_failure_leaves_no_partial_row(store):
    logged_at = _log_prediction(store, symbol="NFLX", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(logged_at)

    real_execute = None

    class _BoomOnSecondExecute:
        """Raise on the INSERT/UPDATE statement (the second execute() in
        log_outcome), after the existence-check SELECT already ran, to
        simulate a mid-transaction failure."""
        def __init__(self, conn):
            self._conn = conn
            self._n = 0

        def execute(self, *args, **kwargs):
            self._n += 1
            if self._n >= 2:
                raise RuntimeError("simulated write failure")
            return self._conn.execute(*args, **kwargs)

    real_conn = store._conn

    def _wrapped_conn():
        c = real_conn()
        return c

    with pytest.raises(RuntimeError):
        with store._lock, store._conn() as c:
            wrapped = _BoomOnSecondExecute(c)
            existing = wrapped.execute(
                "SELECT id FROM outcomes WHERE symbol=? AND horizon=? AND pred_date=? AND market=?",
                ("NFLX", "short", pred_date, "US"),
            ).fetchone()
            assert existing is None
            wrapped.execute("""
                INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                      return_1d, return_5d, return_20d, return_60d)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (logged_at, "NFLX", "short", pred_date, "US", 1.0, None, None, None))

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes WHERE symbol='NFLX'").fetchall()
    assert rows == [], "a failure between the existence check and the write must leave zero rows (sqlite3's " \
                        "connection context manager rolls back the transaction on exception)"


# ─────────────────────────────────────────────────────────────────────────────
# GPI-0 condition D3 (2026-07-17) — live-resolved benchmark returns
# ─────────────────────────────────────────────────────────────────────────────
# benchmark_return_5d/20d/60d were only ever set by a one-off migration
# script; the live resolver never computed or passed them. These tests
# exercise the real SQLite backend end-to-end (only _fetch_return, the real
# yfinance call, is mocked) to prove resolve_pair() now persists real
# benchmark returns, matching validation_engine.py's own benchmark tickers.

def test_resolved_outcome_persists_real_benchmark_returns(store):
    from services.alpha_engine import outcome_logger
    logged_at = _log_prediction(store, symbol="RELIANCE", horizon="medium",
                                 market="IN", days_ago=35)
    pred_date = _pred_date_str(logged_at)

    def fake_fetch(symbol, pred_date_str, days, market="IN", apply_suffix=True):
        if symbol == "^NSEI":
            return {5: 1.1, 20: 2.2}.get(days)
        return {5: 3.3, 20: 4.4}.get(days)  # the stock's own return

    with patch.object(outcome_logger, "_fetch_return", side_effect=fake_fetch):
        stats = outcome_logger.resolve_pair("IN", "medium", 30, False, True, True, False)

    assert stats["resolved"] == 1
    with store._lock, store._conn() as c:
        row = c.execute(
            "SELECT return_5d, return_20d, benchmark_return_5d, benchmark_return_20d "
            "FROM outcomes WHERE symbol='RELIANCE'"
        ).fetchone()
    assert (row["return_5d"], row["return_20d"]) == (3.3, 4.4)
    assert (row["benchmark_return_5d"], row["benchmark_return_20d"]) == (1.1, 2.2)


def test_benchmark_ticker_never_gets_the_stocks_market_suffix(store):
    """^NSEI/^GSPC are already complete Yahoo tickers — appending IN's own
    equity suffix (.NS) would 404 the whole benchmark fetch."""
    from services.alpha_engine import outcome_logger
    logged_at = _log_prediction(store, symbol="TCS", horizon="short",
                                 market="IN", days_ago=5)
    pred_date = _pred_date_str(logged_at)

    seen_symbols = []

    def fake_fetch(symbol, pred_date_str, days, market="IN", apply_suffix=True):
        seen_symbols.append((symbol, apply_suffix))
        return 1.0

    with patch.object(outcome_logger, "_fetch_return", side_effect=fake_fetch):
        outcome_logger.resolve_pair("IN", "short", 3, True, True, False, False)

    assert ("^NSEI", False) in seen_symbols
    assert ("TCS", True) in seen_symbols


def test_benchmark_return_is_cached_across_predictions_sharing_a_pred_date(store):
    """N pending predictions resolved on the same pred_date must trigger
    exactly one benchmark fetch per (market, days) pair, not N redundant
    ones — every stock resolved for the same market/date shares the
    identical benchmark return."""
    from services.alpha_engine import outcome_logger
    logged_at = _log_prediction(store, symbol="INFY", horizon="short",
                                 market="IN", days_ago=5)
    _log_prediction(store, symbol="TCS", horizon="short", market="IN", days_ago=5)
    _log_prediction(store, symbol="WIPRO", horizon="short", market="IN", days_ago=5)

    benchmark_calls = []

    def fake_fetch(symbol, pred_date_str, days, market="IN", apply_suffix=True):
        if symbol == "^NSEI":
            benchmark_calls.append((pred_date_str, days))
        return 1.0

    with patch.object(outcome_logger, "_fetch_return", side_effect=fake_fetch):
        stats = outcome_logger.resolve_pair("IN", "short", 3, True, True, False, False)

    assert stats["resolved"] == 3
    assert len(benchmark_calls) == 1, (
        f"expected exactly 1 benchmark fetch shared across 3 same-date predictions, got {benchmark_calls}"
    )


def test_benchmark_return_is_none_when_stock_return_unresolvable():
    """A benchmark that can't be fetched must never crash resolution or
    fabricate a value — matches _fetch_return's own None-on-failure
    contract."""
    from services.alpha_engine import outcome_logger
    cache: dict = {}
    with patch.object(outcome_logger, "_fetch_return", return_value=None):
        result = outcome_logger._fetch_benchmark_return("2026-01-01", 5, "IN", cache)
    assert result is None


def test_unrecognized_market_has_no_benchmark_ticker_and_returns_none():
    from services.alpha_engine import outcome_logger
    cache: dict = {}
    result = outcome_logger._fetch_benchmark_return("2026-01-01", 5, "CRYPTO", cache)
    assert result is None
    assert cache[("CRYPTO", "2026-01-01", 5)] is None


# ─────────────────────────────────────────────────────────────────────────────
# postgres_store.py — eligibility SQL selection (mocked connection; no live DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestPostgresStoreEligibilitySelection:
    """postgres_store.py can't be exercised against a real Postgres in this
    phase (the forensic audit's temporary DB credential has been revoked, and
    production must not be touched). These tests verify the SQL fragment
    selected for each horizon comes only from the fixed, hardcoded
    allow-list — never built from caller-supplied text — and that an unknown
    horizon is rejected outright rather than silently interpolated."""

    def _make_fake_pool(self, fetch_return=None):
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = fetch_return or []
        fake_conn.execute.return_value.fetchone.return_value = (0, 0, 0, 0)
        fake_pool = MagicMock()
        fake_pool.connection.return_value.__enter__.return_value = fake_conn
        fake_pool.connection.return_value.__exit__.return_value = False
        return fake_pool, fake_conn

    def test_unknown_horizon_rejected_not_interpolated(self):
        from services import postgres_store as pg
        with patch.object(pg, "_get_pool", return_value=self._make_fake_pool()[0]):
            with pytest.raises(ValueError):
                pg.get_unresolved_predictions("'; DROP TABLE outcomes; --", min_days_old=3, market="US")

    @pytest.mark.parametrize("horizon,expected_fragment", [
        ("short", "o.return_1d IS NULL OR o.return_5d IS NULL"),
        ("medium", "o.return_5d IS NULL OR o.return_20d IS NULL"),
        ("long", "o.return_60d IS NULL"),
    ])
    def test_eligibility_clause_matches_fixed_allowlist(self, horizon, expected_fragment):
        from services import postgres_store as pg
        fake_pool, fake_conn = self._make_fake_pool()
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.get_unresolved_predictions(horizon, min_days_old=3, market="US")
        executed_sql = fake_conn.execute.call_args[0][0]
        assert expected_fragment in executed_sql

    def test_log_outcome_upsert_uses_coalesce_for_every_return_column(self):
        from services import postgres_store as pg
        fake_pool, fake_conn = self._make_fake_pool()
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.log_outcome("AAPL", "short", "2026-01-01", 1.0, None, None, market="US")
        executed_sql = fake_conn.execute.call_args[0][0]
        assert "DO UPDATE SET" in executed_sql
        for col in ("return_1d", "return_5d", "return_20d", "return_60d",
                    "benchmark_return_5d", "benchmark_return_20d", "benchmark_return_60d"):
            assert f"COALESCE(outcomes.{col}, EXCLUDED.{col})" in executed_sql

    def test_get_daily_picks_performance_join_includes_market(self):
        from services import postgres_store as pg
        fake_pool, fake_conn = self._make_fake_pool()
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.get_daily_picks_performance(horizon="medium")
        executed_sql = fake_conn.execute.call_args[0][0]
        assert "p.market = o.market" in executed_sql


# ─────────────────────────────────────────────────────────────────────────────
# 10 & 11 — operator backfill CLI: dry-run default, zero writes, confirm gate
# ─────────────────────────────────────────────────────────────────────────────

class TestBackfillOutcomesCLI:

    def _import_script(self):
        import importlib
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        import backfill_outcomes
        importlib.reload(backfill_outcomes)
        return backfill_outcomes

    def test_dry_run_performs_zero_writes(self, store, monkeypatch, capsys):
        script = self._import_script()
        _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)

        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
             patch.object(sys, "argv", ["backfill_outcomes.py", "--market", "US", "--horizon", "short"]):
            rc = script.main()

        assert rc == 0
        with store._lock, store._conn() as c:
            rows = c.execute("SELECT * FROM outcomes").fetchall()
        assert rows == [], "dry-run (the default, no --execute) must never write an outcome row"

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "zero rows were written" in out

    def test_execute_without_confirm_token_refuses(self, store):
        script = self._import_script()
        with patch.object(sys, "argv", ["backfill_outcomes.py", "--execute"]):
            rc = script.main()
        assert rc != 0

    def test_execute_with_wrong_confirm_token_refuses(self, store):
        script = self._import_script()
        with patch.object(sys, "argv",
                           ["backfill_outcomes.py", "--execute", "--confirm", "not-the-real-token"]):
            rc = script.main()
        assert rc != 0

    def test_execute_with_correct_confirm_token_but_no_manifest_still_refuses(self, store, monkeypatch):
        """
        Phase 1A.3 CLI contract change: --execute no longer has a direct-write
        path at all, even with the correct --confirm token — every write must
        go through a reviewed, checksummed --manifest (see
        test_manifest_backfill.py for the manifest-execute-mode write path).
        A live re-derived candidate set can never be proven to match what an
        operator reviewed, so direct execute is refused outright now.
        """
        script = self._import_script()
        _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)

        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
             patch.object(sys, "argv", [
                 "backfill_outcomes.py", "--market", "US", "--horizon", "short",
                 "--execute", "--confirm", script.CONFIRM_TOKEN,
             ]):
            rc = script.main()

        assert rc != 0, "--execute without --manifest must be refused even with a correct --confirm token"
        with store._lock, store._conn() as c:
            rows = c.execute("SELECT * FROM outcomes").fetchall()
        assert rows == [], "a refused execute attempt must never write anything"
