"""
StockSense360 Forensic Remediation — Phase 1A.6: Market Integrity Hardening
and Historical Repair Planning (remediation pass).

Covers the Phase 1A.5 finding: predictions were stored with market='IN'
while containing US-only symbols, because log_prediction's market
parameter could silently default and nothing at the write boundary ever
checked whether a symbol could plausibly belong to the claimed market.
Commit 536fd3d closed the one call site that triggered the defect; this
phase closes the API boundary itself with a sentinel-based explicit-market
contract, a dedicated exception hierarchy, structured field-level
observability, database-default removal (application-level; the live
production schema migration is written but unexecuted), resolver/manifest
containment without a bypass consent flag, a byte-deterministic dry-run
repair planner with an offline verifier, and denominator containment on
the two production metric paths that read the predictions table.

All tests run against a temp-file SQLite backend (services/alpha_engine/
store.py) or a mocked connection pool (services/postgres_store.py) — no
live Postgres and no live network calls (yfinance always mocked), and no
database connection of any kind for the repair-planner tests (which only
exercise its pure classification/validation functions). Real symbols from
the canonical universe are used as fixtures rather than fabricated ones:
  AAPL            US_ONLY
  RELIANCE        IN_ONLY
  INFY            BOTH (genuinely dual-listed)
  BRK-A / BRK.A   US_ONLY (canonical hyphenated form / dotted alias form)
  TOTALLYFAKEXYZ123  UNKNOWN (not in either canonical universe)
"""

import importlib
import inspect
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from services.market_integrity import (  # noqa: E402
    MISSING_MARKET,
    EVENT_MANIFEST_CONFLICT_SKIPPED,
    EVENT_MANIFEST_GENERATION_REFUSED,
    EVENT_MANIFEST_POPULATION_EXHAUSTED,
    EVENT_MARKET_CONTEXT_MISSING,
    EVENT_MARKET_INVALID,
    EVENT_MARKET_SYMBOL_CONFLICT,
    EVENT_PERFORMANCE_CONFLICTS_EXCLUDED,
    EVENT_RESOLVER_CONFLICT_SKIPPED,
    EVENT_SYMBOL_CLASSIFICATION_UNKNOWN,
    REASON_DEFINITIVE_CONFLICT,
    REASON_MARKET_INVALID,
    REASON_MARKET_MISSING,
    REASON_UNKNOWN_CLASSIFICATION,
    InvalidMarketError,
    MarketSymbolConflictError,
    MissingMarketContextError,
    SymbolMarketClass,
    classify_symbol,
    classify_symbol_detailed,
    is_definitive_conflict,
    require_explicit_market,
)
from services.alpha_engine import manifest_backfill  # noqa: E402

US_ONLY_SYM = "AAPL"
IN_ONLY_SYM = "RELIANCE"
BOTH_SYM = "INFY"
UNKNOWN_SYM = "TOTALLYFAKEXYZ123"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_POSTGRES", "0")
    import services.alpha_engine.store as store_mod
    importlib.reload(store_mod)
    monkeypatch.setattr(store_mod, "DB_PATH", str(tmp_path / "test_alpha_engine.db"))
    monkeypatch.setattr(store_mod, "USE_POSTGRES", False)
    store_mod.init_db()
    return store_mod


def _mk_zscores():
    return {"tech": 0.1, "fund": 0.2, "sentiment": 0.3, "quality": 0.4}


def _log(store, symbol, market, price=100.0, horizon="short", _writer_source="test"):
    """log_prediction's SQLite path never returns a row id (pre-existing,
    unrelated to this phase). Look the row up by (symbol, price) instead."""
    store.log_prediction(
        symbol, horizon, _mk_zscores(), combined_alpha=0.5, meta_alpha=0.5,
        signal="BUY", price=price, market=market, _writer_source=_writer_source,
    )
    return _find_prediction_id(store, symbol, price)


def _find_prediction_id(store, symbol, price):
    with store._lock, store._conn() as c:
        row = c.execute(
            "SELECT id FROM predictions WHERE symbol = ? AND price = ? ORDER BY id DESC LIMIT 1",
            (symbol, price),
        ).fetchone()
    assert row is not None, f"no persisted row found for symbol={symbol!r} price={price!r}"
    return row[0]


def _raw_insert_prediction(store, symbol="AAPL", horizon="short", market="IN",
                            days_ago=10, price=100.0, is_daily_pick=0):
    """Bypasses require_explicit_market entirely — used only to seed
    already-contaminated rows in tests, exactly the shape of the legacy
    pre-536fd3d defect (direct SQL, mirroring the real bug's effect)."""
    logged_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with store._lock, store._conn() as c:
        cur = c.execute("""
            INSERT INTO predictions (logged_at, symbol, horizon, market, price)
            VALUES (?, ?, ?, ?, ?)
        """, (logged_at, symbol, horizon, market, price))
        return cur.lastrowid


def _raw_insert_outcome(store, symbol, horizon, pred_date, market="IN", return_5d=9.9):
    """Bypasses require_explicit_market entirely — used only to seed an
    already-contaminated outcome row for tests, exactly the shape a
    pre-hardening log_outcome() call could have produced (now impossible
    through the real function)."""
    now = datetime.now(timezone.utc).isoformat()
    with store._lock, store._conn() as c:
        c.execute("""
            INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                                  return_1d, return_5d, return_20d, return_60d)
            VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, NULL)
        """, (now, symbol, horizon, pred_date, market, return_5d))


# ─────────────────────────────────────────────────────────────────────────────
# A. Explicit market — classifier primitives
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_symbol_us_only():
    assert classify_symbol(US_ONLY_SYM) == SymbolMarketClass.US_ONLY


def test_classify_symbol_in_only():
    assert classify_symbol(IN_ONLY_SYM) == SymbolMarketClass.IN_ONLY


def test_classify_symbol_both():
    assert classify_symbol(BOTH_SYM) == SymbolMarketClass.BOTH


def test_classify_symbol_unknown():
    assert classify_symbol(UNKNOWN_SYM) == SymbolMarketClass.UNKNOWN


def test_us_only_symbol_is_a_definitive_conflict_under_in():
    assert is_definitive_conflict(US_ONLY_SYM, "IN") is True
    assert is_definitive_conflict(US_ONLY_SYM, "US") is False


def test_in_only_symbol_is_a_definitive_conflict_under_us():
    assert is_definitive_conflict(IN_ONLY_SYM, "US") is True
    assert is_definitive_conflict(IN_ONLY_SYM, "IN") is False


def test_both_symbol_is_never_a_definitive_conflict():
    assert is_definitive_conflict(BOTH_SYM, "IN") is False
    assert is_definitive_conflict(BOTH_SYM, "US") is False


def test_unknown_symbol_is_never_a_definitive_conflict():
    assert is_definitive_conflict(UNKNOWN_SYM, "IN") is False
    assert is_definitive_conflict(UNKNOWN_SYM, "US") is False


# ─────────────────────────────────────────────────────────────────────────────
# B. Classifier — raw/canonical preservation, class-share alias handling
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_symbol_detailed_preserves_raw_and_canonical():
    result = classify_symbol_detailed(" reliance.ns ")
    assert result.raw_symbol == " reliance.ns "
    assert result.canonical_symbol == "RELIANCE"
    assert result.classification == SymbolMarketClass.IN_ONLY
    assert result.normalization_rule == "suffix_stripped"


def test_direct_universe_match_preferred_over_alias():
    result = classify_symbol_detailed(US_ONLY_SYM)
    assert result.normalization_rule == "none"
    assert result.canonical_symbol == US_ONLY_SYM


def test_brk_dot_a_resolves_via_class_share_alias():
    result = classify_symbol_detailed("BRK.A")
    assert result.classification == SymbolMarketClass.US_ONLY
    assert result.canonical_symbol == "BRK-A"
    assert result.normalization_rule == "class_share_alias"


def test_brk_hyphen_a_resolves_directly_no_alias_needed():
    result = classify_symbol_detailed("BRK-A")
    assert result.classification == SymbolMarketClass.US_ONLY
    assert result.normalization_rule == "none"


def test_brk_dot_b_also_resolves_via_alias():
    result = classify_symbol_detailed("BRK.B")
    assert result.classification == SymbolMarketClass.US_ONLY
    assert result.canonical_symbol == "BRK-B"


def test_ns_suffix_stripped():
    result = classify_symbol_detailed("RELIANCE.NS")
    assert result.classification == SymbolMarketClass.IN_ONLY
    assert result.normalization_rule == "suffix_stripped"
    assert result.canonical_symbol == "RELIANCE"


def test_bo_suffix_stripped():
    result = classify_symbol_detailed("RELIANCE.BO")
    assert result.classification == SymbolMarketClass.IN_ONLY
    assert result.normalization_rule == "suffix_stripped"


def test_both_symbol_detailed():
    result = classify_symbol_detailed(BOTH_SYM)
    assert result.classification == SymbolMarketClass.BOTH


def test_unknown_symbol_detailed():
    result = classify_symbol_detailed(UNKNOWN_SYM)
    assert result.classification == SymbolMarketClass.UNKNOWN
    assert result.normalization_rule == "none"


def test_arbitrary_dotted_unknown_symbol_is_not_corrupted():
    """A dotted symbol whose alias form is ALSO not in any canonical
    universe must stay UNKNOWN, never be silently rewritten to some
    invented hyphenated form."""
    result = classify_symbol_detailed("TOTALLY.FAKE")
    assert result.classification == SymbolMarketClass.UNKNOWN
    assert result.canonical_symbol == "TOTALLY.FAKE"


def test_classification_is_deterministic_and_repeatable():
    r1 = classify_symbol_detailed("BRK.A")
    r2 = classify_symbol_detailed("BRK.A")
    assert r1 == r2


# ─────────────────────────────────────────────────────────────────────────────
# C. Explicit market — write-boundary contract (require_explicit_market)
# ─────────────────────────────────────────────────────────────────────────────

def test_omitted_market_raises_missing_market_context_error():
    with pytest.raises(MissingMarketContextError):
        require_explicit_market(US_ONLY_SYM, MISSING_MARKET, writer_context="test")


def test_omitted_market_logs_structured_fields(caplog):
    with caplog.at_level(logging.ERROR, logger="services.market_integrity"):
        with pytest.raises(MissingMarketContextError):
            require_explicit_market(US_ONLY_SYM, MISSING_MARKET, writer_context="my_writer")
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MARKET_CONTEXT_MISSING]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.reason_code == REASON_MARKET_MISSING
    assert rec.writer_context == "my_writer"
    assert rec.raw_symbol == US_ONLY_SYM


def test_none_market_also_raises_missing_market_context_error():
    with pytest.raises(MissingMarketContextError):
        require_explicit_market(US_ONLY_SYM, None, writer_context="test")


def test_blank_market_raises_invalid_market_error():
    with pytest.raises(InvalidMarketError):
        require_explicit_market(US_ONLY_SYM, "", writer_context="test")


def test_unsupported_market_raises_invalid_market_error():
    with pytest.raises(InvalidMarketError):
        require_explicit_market(US_ONLY_SYM, "GB", writer_context="test")


def test_invalid_market_logs_structured_fields(caplog):
    with caplog.at_level(logging.ERROR, logger="services.market_integrity"):
        with pytest.raises(InvalidMarketError):
            require_explicit_market(US_ONLY_SYM, "GB", writer_context="my_writer")
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MARKET_INVALID]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.reason_code == REASON_MARKET_INVALID
    assert rec.writer_context == "my_writer"
    assert rec.raw_symbol == US_ONLY_SYM


def test_definitive_conflict_raises_market_symbol_conflict_error():
    with pytest.raises(MarketSymbolConflictError) as exc_info:
        require_explicit_market(US_ONLY_SYM, "IN", writer_context="test")
    assert exc_info.value.symbol == US_ONLY_SYM
    assert exc_info.value.market == "IN"
    assert exc_info.value.classification == SymbolMarketClass.US_ONLY


def test_definitive_conflict_logs_structured_fields(caplog):
    with caplog.at_level(logging.ERROR, logger="services.market_integrity"):
        with pytest.raises(MarketSymbolConflictError):
            require_explicit_market(US_ONLY_SYM, "IN", writer_context="my_writer")
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MARKET_SYMBOL_CONFLICT]
    assert len(matching) == 1
    rec = matching[0]
    assert rec.reason_code == REASON_DEFINITIVE_CONFLICT
    assert rec.market == "IN"
    assert rec.classification == SymbolMarketClass.US_ONLY.value


def test_unknown_classification_logs_structured_fields_without_raising(caplog):
    with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
        result = require_explicit_market(UNKNOWN_SYM, "IN", writer_context="my_writer")
    assert result.is_unknown is True
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_SYMBOL_CLASSIFICATION_UNKNOWN]
    assert len(matching) == 1
    assert matching[0].reason_code == REASON_UNKNOWN_CLASSIFICATION


def test_both_symbol_succeeds_under_either_explicit_market():
    r_in = require_explicit_market(BOTH_SYM, "IN", writer_context="test")
    r_us = require_explicit_market(BOTH_SYM, "US", writer_context="test")
    assert r_in.market == "IN" and not r_in.is_conflict
    assert r_us.market == "US" and not r_us.is_conflict


@pytest.mark.parametrize("exc_type,market_arg", [
    (MissingMarketContextError, MISSING_MARKET),
    (InvalidMarketError, "GB"),
    (MarketSymbolConflictError, "IN"),
])
def test_all_three_failures_occur_before_any_database_access(exc_type, market_arg):
    """require_explicit_market itself performs no I/O — this is a structural
    guarantee (pure function over in-memory sets), reconfirmed here so a
    future change can't accidentally introduce a DB call before validation."""
    sentinel_calls = []
    with patch("builtins.open", side_effect=AssertionError("no file/db access expected")):
        with pytest.raises(exc_type):
            require_explicit_market(US_ONLY_SYM, market_arg, writer_context="test")
    assert sentinel_calls == []


# ─────────────────────────────────────────────────────────────────────────────
# D. log_prediction (SQLite path) — no silent IN default anywhere
# ─────────────────────────────────────────────────────────────────────────────

def test_log_prediction_omitted_market_raises_missing_market_context_error(store):
    with pytest.raises(MissingMarketContextError):
        store.log_prediction(
            "AAPL", "short", _mk_zscores(), combined_alpha=0.5, meta_alpha=0.5,
            signal="BUY", price=100.0,
        )
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM predictions WHERE symbol = 'AAPL'").fetchall()
    assert rows == []


def test_log_prediction_blank_market_raises_invalid_market_error(store):
    with pytest.raises(InvalidMarketError):
        _log(store, US_ONLY_SYM, "")


def test_log_prediction_unsupported_market_raises_invalid_market_error(store):
    with pytest.raises(InvalidMarketError):
        _log(store, US_ONLY_SYM, "GB")


def test_us_only_symbol_cannot_persist_as_in(store):
    with pytest.raises(MarketSymbolConflictError):
        _log(store, US_ONLY_SYM, "IN")
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM predictions WHERE symbol = ?", (US_ONLY_SYM,)).fetchall()
    assert rows == [], "a rejected write must leave zero rows behind"


def test_in_only_symbol_cannot_persist_as_us(store):
    with pytest.raises(MarketSymbolConflictError):
        _log(store, IN_ONLY_SYM, "US")
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM predictions WHERE symbol = ?", (IN_ONLY_SYM,)).fetchall()
    assert rows == []


def test_both_symbol_persists_under_explicit_in(store):
    pid = _log(store, BOTH_SYM, "IN")
    with store._lock, store._conn() as c:
        row = c.execute("SELECT market FROM predictions WHERE id = ?", (pid,)).fetchone()
    assert row[0] == "IN"


def test_both_symbol_persists_under_explicit_us(store):
    pid = _log(store, BOTH_SYM, "US")
    with store._lock, store._conn() as c:
        row = c.execute("SELECT market FROM predictions WHERE id = ?", (pid,)).fetchone()
    assert row[0] == "US"


def test_unknown_symbol_persists_as_claimed_market_not_rejected(store):
    pid = _log(store, UNKNOWN_SYM, "IN")
    with store._lock, store._conn() as c:
        row = c.execute("SELECT market FROM predictions WHERE id = ?", (pid,)).fetchone()
    assert row[0] == "IN"


def test_persistence_stores_supplied_market_exactly(store):
    pid_in = _log(store, IN_ONLY_SYM, "IN", price=111.0)
    pid_us = _log(store, US_ONLY_SYM, "US", price=222.0)
    with store._lock, store._conn() as c:
        row_in = c.execute("SELECT market, price FROM predictions WHERE id = ?", (pid_in,)).fetchone()
        row_us = c.execute("SELECT market, price FROM predictions WHERE id = ?", (pid_us,)).fetchone()
    assert tuple(row_in) == ("IN", 111.0)
    assert tuple(row_us) == ("US", 222.0)


def test_sequential_in_then_us_logging_does_not_leak_market(store):
    pid_in = _log(store, IN_ONLY_SYM, "IN")
    pid_us = _log(store, US_ONLY_SYM, "US")
    pid_in2 = _log(store, IN_ONLY_SYM, "IN", price=101.0)
    with store._lock, store._conn() as c:
        markets = [c.execute("SELECT market FROM predictions WHERE id = ?", (pid,)).fetchone()[0]
                   for pid in (pid_in, pid_us, pid_in2)]
    assert markets == ["IN", "US", "IN"]


def test_concurrent_style_interleaved_in_us_calls_stay_isolated(store):
    results = []
    for i in range(6):
        if i % 2 == 0:
            results.append((IN_ONLY_SYM, "IN", _log(store, IN_ONLY_SYM, "IN", price=float(i))))
        else:
            results.append((US_ONLY_SYM, "US", _log(store, US_ONLY_SYM, "US", price=float(i))))
    with store._lock, store._conn() as c:
        for symbol, expected_market, pid in results:
            row = c.execute("SELECT symbol, market FROM predictions WHERE id = ?", (pid,)).fetchone()
            assert tuple(row) == (symbol, expected_market)


# ─────────────────────────────────────────────────────────────────────────────
# E. postgres_store.py parity — validation before any pool access
# ─────────────────────────────────────────────────────────────────────────────

class TestPostgresStoreWriteBoundaryParity:

    def test_log_prediction_omitted_market_raises_missing_market_context_error(self):
        from services import postgres_store as pg
        with pytest.raises(MissingMarketContextError):
            pg.log_prediction(
                "AAPL", "short", _mk_zscores(), combined_alpha=0.5, meta_alpha=0.5,
                signal="BUY", price=100.0,
            )

    def test_us_only_symbol_rejected_before_any_pool_access(self):
        from services import postgres_store as pg
        with patch.object(pg, "_get_pool", side_effect=AssertionError("pool should never be touched")):
            with pytest.raises(MarketSymbolConflictError):
                pg.log_prediction(
                    "AAPL", "short", _mk_zscores(), combined_alpha=0.5, meta_alpha=0.5,
                    signal="BUY", price=100.0, market="IN",
                )

    def test_invalid_market_rejected_before_any_pool_access(self):
        from services import postgres_store as pg
        with patch.object(pg, "_get_pool", side_effect=AssertionError("pool should never be touched")):
            with pytest.raises(InvalidMarketError):
                pg.log_prediction(
                    "AAPL", "short", _mk_zscores(), combined_alpha=0.5, meta_alpha=0.5,
                    signal="BUY", price=100.0, market="XX",
                )


# ─────────────────────────────────────────────────────────────────────────────
# F. Schema — no database-level silent default
# ─────────────────────────────────────────────────────────────────────────────

def test_postgres_schema_sql_has_no_market_default():
    """Scoped to predictions/outcomes specifically — daily_picks_cache.market
    is a different table (the cached payload's market tag, unrelated to
    per-prediction contamination) and is deliberately left untouched."""
    from services.postgres_store import SCHEMA_SQL
    assert "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'IN'" not in SCHEMA_SQL
    assert "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'IN'" not in SCHEMA_SQL
    assert "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL;" in SCHEMA_SQL
    assert "ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS market TEXT NOT NULL;" in SCHEMA_SQL


def test_sqlite_fresh_create_table_has_no_market_default():
    import services.alpha_engine.store as store_mod
    source = inspect.getsource(store_mod.init_db)
    assert "market         TEXT    NOT NULL DEFAULT 'IN'" not in source
    assert "market       TEXT NOT NULL DEFAULT 'IN'," not in source


def test_sqlite_legacy_alter_fallback_still_has_a_default():
    """The narrow backward-compat path for a genuinely ancient, already-
    populated SQLite file predating the market column intentionally keeps
    a default — SQLite requires one to ADD a NOT NULL column to a non-empty
    table. This never fires against any DB created by the fresh schema."""
    import services.alpha_engine.store as store_mod
    source = inspect.getsource(store_mod.init_db)
    assert "ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'" in source


def test_fresh_sqlite_db_actually_has_no_default_at_pragma_level(store):
    with store._lock, store._conn() as c:
        cols = c.execute("PRAGMA table_info(predictions)").fetchall()
    market_col = [c for c in cols if c[1] == "market"][0]
    assert market_col[4] is None, f"expected no default clause, got {market_col[4]!r}"


def test_migration_file_exists_with_drop_default_statements():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "migrations",
                         "phase_1a6_drop_predictions_market_default.sql")
    assert os.path.exists(path), "the standalone migration file must exist"
    with open(path) as f:
        content = f.read()
    assert "ALTER TABLE predictions ALTER COLUMN market DROP DEFAULT" in content
    assert "ALTER TABLE outcomes ALTER COLUMN market DROP DEFAULT" in content


def test_migration_file_is_never_imported_or_executed_by_application_code():
    """Source-grep guard: the migration must never be *executed* — no
    open()/subprocess/psycopg call anywhere references it as a path to
    read or run. A prose mention in a comment (e.g. pointing a reader at
    it from SCHEMA_SQL's own docstring) is fine and expected; this test
    file's own assertions about the filename are also expected. What must
    never happen is the filename appearing as an argument to something
    that would actually open/execute it."""
    this_file = os.path.abspath(__file__)
    backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    execution_markers = ("open(", "subprocess", "psycopg.connect", "exec(", "os.system", ".read()")
    hits = []
    for root, dirs, files in os.walk(backend_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "venv", ".git", "migrations")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.abspath(os.path.join(root, fname))
            if path == this_file:
                continue
            with open(path, errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    if "phase_1a6_drop_predictions_market_default" in line and \
                       any(marker in line for marker in execution_markers):
                        hits.append(f"{path}:{lineno}")
    assert hits == [], f"migration file must never be executed by application code: {hits}"


# ─────────────────────────────────────────────────────────────────────────────
# G. Resolver conflict exclusion (structured event)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolver_excludes_market_conflicts_before_price_lookup(store, caplog):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)

    from services.alpha_engine import outcome_logger
    fetch_calls = []

    def _tracking_fetch(symbol, pred_date_str, days, market="IN"):
        fetch_calls.append(symbol)
        return 1.0

    with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
        with patch.object(outcome_logger, "_fetch_return", side_effect=_tracking_fetch):
            stats = outcome_logger.resolve_pair("IN", "short", 3, True, True, False, False, dry_run=True)

    assert US_ONLY_SYM not in fetch_calls
    assert IN_ONLY_SYM in fetch_calls
    assert stats["market_conflict_excluded"] == 1
    assert stats["eligible_scanned"] == 2
    assert stats["pending"] == 1

    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_RESOLVER_CONFLICT_SKIPPED]
    assert len(matching) == 1
    assert matching[0].excluded_count == 1
    assert matching[0].eligible_count == 2


def test_resolver_reports_unknown_symbol_count_without_excluding_it(store):
    _raw_insert_prediction(store, symbol=UNKNOWN_SYM, horizon="short", market="IN", days_ago=10)
    from services.alpha_engine import outcome_logger
    with patch.object(outcome_logger, "_fetch_return", return_value=1.0):
        stats = outcome_logger.resolve_pair("IN", "short", 3, True, True, False, False, dry_run=True)
    assert stats["unknown_symbol_count"] == 1
    assert stats["pending"] == 1


def test_resolver_batch_limit_applies_after_conflict_exclusion(store):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=15)
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    from services.alpha_engine import outcome_logger
    with patch.object(outcome_logger, "_fetch_return", return_value=1.0):
        stats = outcome_logger.resolve_pair("IN", "short", 3, True, True, False, False,
                                             dry_run=True, batch_limit=1)
    assert stats["pending"] == 1
    assert stats["resolved"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# H. Manifest generation — no allow_partial, exhaustion semantics
# ─────────────────────────────────────────────────────────────────────────────

def test_no_allow_partial_parameter_exists():
    sig = inspect.signature(manifest_backfill.build_manifest)
    assert "allow_partial" not in sig.parameters


def test_no_allow_partial_cli_flag_exists():
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    with open(os.path.join(scripts_dir, "backfill_outcomes.py")) as f:
        content = f.read()
    assert "allow-partial" not in content
    assert "allow_partial" not in content


def test_manifest_full_count_reached_when_pool_has_enough(store):
    for i in range(3):
        _raw_insert_prediction(store, symbol=f"CLEAN{i}", horizon="short", market="IN", days_ago=10 + i)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=2)
    assert manifest["requested_candidate_count"] == 2
    assert manifest["actual_candidate_count"] == 2
    assert manifest["source_population_exhausted"] is False


def test_manifest_source_exhaustion_produces_explicit_partial_metadata(store, caplog):
    _raw_insert_prediction(store, symbol="ONLYONE", horizon="short", market="IN", days_ago=10)
    with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
            manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    assert manifest["requested_candidate_count"] == 10
    assert manifest["actual_candidate_count"] == 1
    assert manifest["source_population_exhausted"] is True
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MANIFEST_POPULATION_EXHAUSTED]
    assert len(matching) == 1
    assert matching[0].requested_count == 10
    assert matching[0].actual_count == 1


def test_manifest_zero_usable_candidates_refused(store, caplog):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with caplog.at_level(logging.ERROR, logger="services.market_integrity"):
        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
            with pytest.raises(manifest_backfill.ManifestGenerationError, match="zero usable candidates"):
                manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MANIFEST_GENERATION_REFUSED]
    assert len(matching) == 1


def test_manifest_all_null_resolved_values_excluded_and_refused(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=None):
        with pytest.raises(manifest_backfill.ManifestGenerationError, match="zero usable candidates"):
            manifest_backfill.build_manifest("IN", "short", batch_limit=10)


def test_manifest_conflicts_excluded_before_usable_limit(store, caplog):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=9)
    with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
            manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    symbols = [c["symbol"] for c in manifest["candidates"]]
    assert US_ONLY_SYM not in symbols
    assert IN_ONLY_SYM in symbols
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MANIFEST_CONFLICT_SKIPPED]
    assert len(matching) == 1
    assert matching[0].excluded_count == 1


def test_manifest_continues_scanning_past_conflicts_to_reach_batch_limit(store):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=20)
    for i in range(3):
        _raw_insert_prediction(store, symbol=f"CLEAN{i}", horizon="short", market="IN", days_ago=10 + i)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=3)
    assert manifest["actual_candidate_count"] == 3
    assert all(c["symbol"] != US_ONLY_SYM for c in manifest["candidates"])


def test_invalid_rows_cannot_consume_the_full_requested_batch(store):
    for i in range(5):
        _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN",
                                days_ago=30 - i, price=float(i))
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=5)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=1)
    assert manifest["actual_candidate_count"] == 1
    assert manifest["candidates"][0]["symbol"] == IN_ONLY_SYM


def test_deterministic_ordering_stable_with_conflicts_mixed_in(store):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=15)
    _raw_insert_prediction(store, symbol="CLEAN0", horizon="short", market="IN", days_ago=12)
    _raw_insert_prediction(store, symbol="CLEAN1", horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        m1 = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
        m2 = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    assert [c["prediction_id"] for c in m1["candidates"]] == [c["prediction_id"] for c in m2["candidates"]]
    assert [c["symbol"] for c in m1["candidates"]] == ["CLEAN0", "CLEAN1"]


def test_validate_manifest_structure_rejects_underfilled_manifest_without_exhausted_flag(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    assert tampered["source_population_exhausted"] is True
    tampered["source_population_exhausted"] = False  # hide the under-fill
    tampered["manifest_sha256"] = manifest_backfill.compute_checksum(tampered)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="under-filled"):
        manifest_backfill.validate_manifest_structure(tampered, tampered["manifest_sha256"])


def test_validate_manifest_structure_rejects_actual_exceeding_requested(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    tampered["requested_candidate_count"] = 0  # actual (1) now exceeds requested (0)
    tampered["manifest_sha256"] = manifest_backfill.compute_checksum(tampered)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="exceeds requested_candidate_count"):
        manifest_backfill.validate_manifest_structure(tampered, tampered["manifest_sha256"])


def test_validate_manifest_structure_rejects_actual_count_mismatch(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    tampered["actual_candidate_count"] = 99
    tampered["manifest_sha256"] = manifest_backfill.compute_checksum(tampered)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="actual_candidate_count"):
        manifest_backfill.validate_manifest_structure(tampered, tampered["manifest_sha256"])


def test_validate_manifest_structure_rejects_hand_injected_conflict_candidate(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    tampered["candidates"][0]["symbol"] = US_ONLY_SYM
    tampered["manifest_sha256"] = manifest_backfill.compute_checksum(tampered)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="definitive market"):
        manifest_backfill.validate_manifest_structure(tampered, tampered["manifest_sha256"])


# ─────────────────────────────────────────────────────────────────────────────
# I. Metric containment — postgres_store.py and alpha_engine/store.py
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_pool(fetch_return=None):
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = fetch_return or []
    fake_pool = MagicMock()
    fake_pool.connection.return_value.__enter__.return_value = fake_conn
    fake_pool.connection.return_value.__exit__.return_value = False
    return fake_pool, fake_conn


class TestMetricContainment:

    def test_daily_picks_performance_excludes_definitive_conflicts(self, caplog):
        from services import postgres_store as pg
        rows = [
            (US_ONLY_SYM, "IN", "2026-01-01", 100.0, 0.5, 0.5, 1.0, None, None, None, None, None),
            (IN_ONLY_SYM, "IN", "2026-01-01", 200.0, 0.6, 0.6, 2.0, None, None, None, None, None),
        ]
        fake_pool, _ = _make_fake_pool(rows)
        with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
            with patch.object(pg, "_get_pool", return_value=fake_pool):
                result = pg.get_daily_picks_performance(horizon="short")
        symbols = [r["symbol"] for r in result]
        assert US_ONLY_SYM not in symbols
        assert IN_ONLY_SYM in symbols
        matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_PERFORMANCE_CONFLICTS_EXCLUDED]
        assert len(matching) == 1
        assert matching[0].excluded_count == 1
        assert matching[0].fetched_count == 2
        assert matching[0].eligible_count == 1

    def test_daily_picks_performance_retains_both_and_valid_in(self):
        from services import postgres_store as pg
        rows = [
            (BOTH_SYM, "IN", "2026-01-01", 100.0, 0.5, 0.5, 1.0, None, None, None, None, None),
            (IN_ONLY_SYM, "IN", "2026-01-01", 50.0, 0.5, 0.5, 1.0, None, None, None, None, None),
        ]
        fake_pool, _ = _make_fake_pool(rows)
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            result = pg.get_daily_picks_performance(horizon="short")
        assert {r["symbol"] for r in result} == {BOTH_SYM, IN_ONLY_SYM}

    def test_daily_picks_performance_market_none_omits_sql_filter(self):
        """GPI-0 condition D5: market=None (the default) must preserve the
        exact prior no-filter behavior — no market clause, no market param."""
        from services import postgres_store as pg
        fake_pool, fake_conn = _make_fake_pool([])
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.get_daily_picks_performance(horizon="short")
        sql, params = fake_conn.execute.call_args[0]
        assert "p.market = %s" not in sql
        assert params == ("short", "90")

    def test_daily_picks_performance_market_in_adds_sql_filter(self):
        from services import postgres_store as pg
        fake_pool, fake_conn = _make_fake_pool([])
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.get_daily_picks_performance(horizon="short", market="IN")
        sql, params = fake_conn.execute.call_args[0]
        assert "p.market = %s" in sql
        assert params == ("short", "90", "IN")

    def test_daily_picks_performance_market_us_scopes_to_us_only(self):
        from services import postgres_store as pg
        rows = [
            (BOTH_SYM, "US", "2026-01-01", 100.0, 0.5, 0.5, 1.0, None, None, None, None, None),
        ]
        fake_pool, fake_conn = _make_fake_pool(rows)
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            pg.get_daily_picks_performance(horizon="short", market="US")
        sql, params = fake_conn.execute.call_args[0]
        assert params[-1] == "US"

    def test_training_data_excludes_definitive_conflicts(self, caplog):
        from services import postgres_store as pg
        rows = [
            (US_ONLY_SYM, 0.1, 0.2, 0.3, 0.4, 0.5, 0.5, "BUY", "BULL", 1.5),
            (IN_ONLY_SYM, 0.1, 0.2, 0.3, 0.4, 0.5, 0.5, "BUY", "BULL", 2.5),
        ]
        fake_pool, _ = _make_fake_pool(rows)
        with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
            with patch.object(pg, "_get_pool", return_value=fake_pool):
                result = pg.get_training_data("short", market="IN")
        assert len(result) == 1
        assert result[0]["fwd_return"] == 2.5
        assert "symbol" not in result[0]
        matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_PERFORMANCE_CONFLICTS_EXCLUDED]
        assert len(matching) == 1
        assert matching[0].excluded_count == 1

    def test_training_data_retains_both_symbol(self):
        from services import postgres_store as pg
        rows = [(BOTH_SYM, 0.1, 0.2, 0.3, 0.4, 0.5, 0.5, "BUY", "BULL", 1.5)]
        fake_pool, _ = _make_fake_pool(rows)
        with patch.object(pg, "_get_pool", return_value=fake_pool):
            result = pg.get_training_data("short", market="IN")
        assert len(result) == 1


def test_training_data_sqlite_path_excludes_definitive_conflicts(store, caplog):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=10, price=50.0)
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    _raw_insert_outcome(store, US_ONLY_SYM, "short", pred_date, market="IN", return_5d=9.9)
    with caplog.at_level(logging.WARNING, logger="services.market_integrity"):
        data = store.get_training_data("short", market="IN")
    assert data == []
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_PERFORMANCE_CONFLICTS_EXCLUDED]
    assert len(matching) == 1


def test_postgres_and_sqlite_training_data_exclusion_behavior_matches():
    """Both backends must exclude on the exact same predicate
    (is_definitive_conflict), proven by calling the same function on
    identical synthetic inputs conceptually — here verified by asserting
    both code paths import and call the same shared classifier function.

    2026-08 (Supabase egress incident): store.get_training_data() is now a
    thin TTL-caching wrapper around _get_training_data_uncached() — the
    exclusion predicate lives in the uncached implementation, which is what
    every call (cached or not) ultimately runs, so that's what this
    inspects. postgres_store.get_training_data() is unchanged."""
    import services.postgres_store as pg
    import services.alpha_engine.store as sq
    pg_source = inspect.getsource(pg.get_training_data)
    sq_source = inspect.getsource(sq._get_training_data_uncached)
    assert "is_definitive_conflict" in pg_source
    assert "is_definitive_conflict" in sq_source


# ─────────────────────────────────────────────────────────────────────────────
# J. Repair planner — determinism, verification, precedence, dependencies
# ─────────────────────────────────────────────────────────────────────────────

import plan_market_contamination_repair as planner  # noqa: E402


def _mk_candidate(prediction_id=1, symbol=US_ONLY_SYM, horizon="short", price=150.0,
                   pred_date="2026-01-01", is_daily_pick=False):
    return {"prediction_id": prediction_id, "symbol": symbol, "horizon": horizon,
            "price": price, "pred_date": pred_date, "is_daily_pick": is_daily_pick}


def test_planner_classifies_orphan_as_safe_relabel():
    result = planner.classify_candidate(_mk_candidate(), {}, set(), {}, {})
    assert result["category"] == planner.CATEGORY_SAFE_RELABEL


def test_planner_classifies_exact_price_match_as_exact_duplicate():
    us_index = {US_ONLY_SYM: {"2026-01-01": [(99, 150.0)]}}
    result = planner.classify_candidate(_mk_candidate(), us_index, set(), {}, {})
    assert result["category"] == planner.CATEGORY_EXACT_DUP
    assert result["matched_us_prediction_id"] == 99


def test_planner_classifies_price_mismatch_as_near_duplicate():
    us_index = {US_ONLY_SYM: {"2026-01-01": [(99, 151.25)]}}
    result = planner.classify_candidate(_mk_candidate(), us_index, set(), {}, {})
    assert result["category"] == planner.CATEGORY_NEAR_DUP


def test_planner_classifies_downstream_referenced_row_as_quarantine():
    result = planner.classify_candidate(_mk_candidate(is_daily_pick=True), {}, set(), {}, {})
    assert result["category"] == planner.CATEGORY_QUARANTINE
    deps = {d["dependency_domain"]: d for d in result["downstream_dependencies"]}
    assert deps["daily_picks"]["blocks_automatic_relabel"] is True
    assert deps["daily_picks"]["confidence"] == planner.CONFIDENCE_DIRECT


def test_planner_classifies_universe_drift_as_unclassifiable():
    result = planner.classify_candidate(_mk_candidate(symbol=IN_ONLY_SYM), {}, set(), {}, {})
    assert result["category"] == planner.CATEGORY_UNCLASSIFIABLE


def test_planner_classifies_missing_price_as_unclassifiable():
    result = planner.classify_candidate(_mk_candidate(price=None), {}, set(), {}, {})
    assert result["category"] == planner.CATEGORY_UNCLASSIFIABLE


def test_planner_dependency_blocks_even_when_exact_duplicate_also_exists():
    """Overlapping-condition precedence proof: a row with BOTH a blocking
    dependency AND an exact duplicate match must classify as QUARANTINE,
    never EXACT_CROSS_MARKET_DUPLICATE — a material dependency is never
    silently outranked by a duplicate match."""
    us_index = {US_ONLY_SYM: {"2026-01-01": [(99, 150.0)]}}
    result = planner.classify_candidate(_mk_candidate(is_daily_pick=True), us_index, set(), {}, {})
    assert result["category"] == planner.CATEGORY_QUARANTINE


def test_planner_dependency_blocks_even_when_near_duplicate_also_exists():
    us_index = {US_ONLY_SYM: {"2026-01-01": [(99, 175.0)]}}
    outcome_keys = {(US_ONLY_SYM, "short", "IN", "2026-01-01")}
    result = planner.classify_candidate(_mk_candidate(), us_index, outcome_keys, {}, {})
    assert result["category"] == planner.CATEGORY_QUARANTINE


def test_planner_outcome_dependency_is_direct_confidence():
    outcome_keys = {(US_ONLY_SYM, "short", "IN", "2026-01-01")}
    result = planner.classify_candidate(_mk_candidate(), {}, outcome_keys, {}, {})
    deps = {d["dependency_domain"]: d for d in result["downstream_dependencies"]}
    assert deps["outcomes"]["confidence"] == planner.CONFIDENCE_DIRECT
    assert deps["outcomes"]["blocks_automatic_relabel"] is True


def test_planner_paper_trade_dependency_is_strong_logical_confidence():
    result = planner.classify_candidate(_mk_candidate(), {}, set(), {US_ONLY_SYM: 3}, {})
    deps = {d["dependency_domain"]: d for d in result["downstream_dependencies"]}
    assert deps["paper_trades"]["confidence"] == planner.CONFIDENCE_STRONG_LOGICAL
    assert deps["paper_trades"]["match_count"] == 3
    assert deps["paper_trades"]["blocks_automatic_relabel"] is True


def test_planner_score_snapshot_dependency_is_weak_logical_confidence():
    key = (US_ONLY_SYM, "short", "2026-01-01")
    result = planner.classify_candidate(_mk_candidate(), {}, set(), {}, {key: 2})
    deps = {d["dependency_domain"]: d for d in result["downstream_dependencies"]}
    assert deps["score_snapshots"]["confidence"] == planner.CONFIDENCE_WEAK_LOGICAL
    assert deps["score_snapshots"]["match_count"] == 2
    assert deps["score_snapshots"]["blocks_automatic_relabel"] is True


def test_planner_absent_dependency_does_not_block():
    result = planner.classify_candidate(_mk_candidate(), {}, set(), {}, {})
    for dep in result["downstream_dependencies"]:
        assert dep["blocks_automatic_relabel"] is False
        assert dep["match_count"] == 0
    assert result["category"] == planner.CATEGORY_SAFE_RELABEL


def _build_synthetic_plan():
    from services.alpha_engine.manifest_backfill import compute_checksum
    from collections import Counter as _Counter

    candidates_raw = [_mk_candidate(1, US_ONLY_SYM, price=150.0, pred_date="2026-01-01")]
    counts = {c: 0 for c in planner.ALL_CATEGORIES}
    dep_summary = _Counter()
    candidates_out = []
    for cand in candidates_raw:
        r = planner.classify_candidate(cand, {}, set(), {}, {})
        counts[r["category"]] += 1
        for d in r["downstream_dependencies"]:
            if d["blocks_automatic_relabel"]:
                dep_summary[d["dependency_domain"]] += 1
        candidates_out.append({
            "prediction_id": cand["prediction_id"], "symbol": cand["symbol"], "horizon": cand["horizon"],
            "stored_market": "IN", "pred_date": cand["pred_date"], "price": cand["price"],
            "category": r["category"], "category_reason": r["category_reason"],
            "matched_us_prediction_id": r["matched_us_prediction_id"],
            "downstream_dependencies": r["downstream_dependencies"],
        })
    plan = {
        "manifest_version": planner.PLAN_SCHEMA_VERSION, "planner_version": planner.PLANNER_VERSION,
        "source_commit": "deadbeef", "database_identity": {"host": "h", "port": 1, "dbname": "d"},
        "total_contaminated_rows": len(candidates_raw), "classification_counts": counts,
        "dependency_summary": dict(dep_summary),
        "canonical_serialization": "sort_keys+compact_separators+no_nan",
        "residual_risk_notes": list(planner.RESIDUAL_RISK_NOTES),
        "candidates": candidates_out,
    }
    plan["manifest_sha256"] = compute_checksum(plan)
    return plan


def test_planner_plan_produces_byte_identical_json_for_identical_input():
    from services.alpha_engine.manifest_backfill import canonical_json
    p1 = _build_synthetic_plan()
    p2 = _build_synthetic_plan()
    assert canonical_json(p1).encode() == canonical_json(p2).encode()


def test_planner_plan_has_no_timestamp_field_anywhere():
    plan = _build_synthetic_plan()
    assert "generated_at_utc" not in plan
    text = json.dumps(plan)
    assert "generated_at_utc" not in text


def test_planner_checksum_is_stable_across_repeated_generation():
    p1 = _build_synthetic_plan()
    p2 = _build_synthetic_plan()
    assert p1["manifest_sha256"] == p2["manifest_sha256"]


def test_planner_checksum_excludes_only_itself():
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan = _build_synthetic_plan()
    payload_without_hash = {k: v for k, v in plan.items() if k != "manifest_sha256"}
    assert compute_checksum(payload_without_hash) == plan["manifest_sha256"]


def test_verifier_passes_a_valid_synthetic_plan():
    plan = _build_synthetic_plan()
    planner.validate_repair_plan(plan)  # must not raise


def test_verifier_rejects_tampered_plan():
    plan = _build_synthetic_plan()
    plan["candidates"][0]["price"] = 999.0  # checksum now stale
    with pytest.raises(planner.RepairPlanValidationError, match="checksum"):
        planner.validate_repair_plan(plan)


def test_verifier_rejects_missing_required_field():
    plan = _build_synthetic_plan()
    del plan["dependency_summary"]
    with pytest.raises(planner.RepairPlanValidationError, match="dependency_summary"):
        planner.validate_repair_plan(plan)


def test_verifier_rejects_unsupported_schema_version():
    plan = _build_synthetic_plan()
    plan["manifest_version"] = "999"
    with pytest.raises(planner.RepairPlanValidationError, match="manifest_version"):
        planner.validate_repair_plan(plan)


def test_verifier_rejects_duplicate_prediction_ids():
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["candidates"].append(dict(plan["candidates"][0]))
    plan["total_contaminated_rows"] = len(plan["candidates"])
    plan["classification_counts"][plan["candidates"][0]["category"]] += 1
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="duplicate"):
        planner.validate_repair_plan(plan)


def test_verifier_rejects_forbidden_executable_field():
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["execute"] = True
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="forbidden"):
        planner.validate_repair_plan(plan)


def test_verifier_performs_no_database_access(tmp_path, monkeypatch):
    """--verify must never import psycopg or read AUDIT_DATABASE_URL/
    DATABASE_URL — proven by clearing both env vars and confirming
    verification still succeeds purely from the file's own contents."""
    monkeypatch.delenv("AUDIT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    plan = _build_synthetic_plan()
    path = tmp_path / "plan.json"
    from services.alpha_engine.manifest_backfill import canonical_json
    path.write_text(canonical_json(plan))

    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                                       "plan_market_contamination_repair.py"),
         "--verify", str(path)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k not in ("AUDIT_DATABASE_URL", "DATABASE_URL")},
    )
    assert result.returncode == 0, result.stderr
    assert "no database connection" in result.stdout.lower()


def test_planner_has_no_execute_apply_write_repair_mode():
    source = inspect.getsource(planner)
    for flag in ('"--execute"', '"--apply"', '"--write"', '"--repair"'):
        assert f"add_argument({flag}" not in source
    for fn_name in ("execute_repair_plan", "apply_repair_plan", "write_repair_plan", "repair"):
        assert not hasattr(planner, fn_name)


def test_planner_redacts_credentials_from_database_identity():
    identity = planner._redact_database_identity(
        "postgresql://myuser:supersecret@example-host.pooler.supabase.com:5432/postgres"
    )
    text = json.dumps(identity)
    assert "myuser" not in text
    assert "supersecret" not in text
    assert identity["host"] == "example-host.pooler.supabase.com"


def test_planner_rejects_non_finite_price():
    plan = {"candidates": [{"prediction_id": 1, "price": float("nan")}]}
    with pytest.raises(planner.RepairPlanError):
        planner._validate_finite(plan)
    plan_inf = {"candidates": [{"prediction_id": 1, "price": float("inf")}]}
    with pytest.raises(planner.RepairPlanError):
        planner._validate_finite(plan_inf)


def test_planner_plan_contains_no_email_addresses_or_credentials():
    plan = _build_synthetic_plan()
    text = json.dumps(plan)
    assert "@" not in text


def test_planner_all_five_categories_are_reachable():
    reachable = set()
    reachable.add(planner.classify_candidate(_mk_candidate(price=None), {}, set(), {}, {})["category"])
    reachable.add(planner.classify_candidate(_mk_candidate(is_daily_pick=True), {}, set(), {}, {})["category"])
    reachable.add(planner.classify_candidate(
        _mk_candidate(), {US_ONLY_SYM: {"2026-01-01": [(1, 150.0)]}}, set(), {}, {})["category"])
    reachable.add(planner.classify_candidate(
        _mk_candidate(), {US_ONLY_SYM: {"2026-01-01": [(1, 999.0)]}}, set(), {}, {})["category"])
    reachable.add(planner.classify_candidate(_mk_candidate(), {}, set(), {}, {})["category"])
    assert reachable == set(planner.ALL_CATEGORIES)


def test_planner_no_environment_specific_output_in_candidates():
    plan = _build_synthetic_plan()
    text = json.dumps(plan["candidates"])
    assert "/Users/" not in text
    assert "/tmp/" not in text


# ─────────────────────────────────────────────────────────────────────────────
# K. log_outcome persistence boundary — same explicit-market contract as
# log_prediction (Phase 1A.6 remediation pass, following the forensic diff
# review that found log_outcome still silently defaulted to "IN")
# ─────────────────────────────────────────────────────────────────────────────

def test_log_outcome_omitted_market_raises_missing_market_context_error(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with pytest.raises(MissingMarketContextError):
        store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=1.0, return_5d=None, return_20d=None)
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes WHERE symbol = ?", (US_ONLY_SYM,)).fetchall()
    assert rows == [], "a rejected write must leave zero rows behind"


def test_log_outcome_invalid_market_raises_invalid_market_error(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with pytest.raises(InvalidMarketError):
        store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=1.0, return_5d=None,
                           return_20d=None, market="GB")


def test_log_outcome_blank_market_raises_invalid_market_error(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with pytest.raises(InvalidMarketError):
        store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=1.0, return_5d=None,
                           return_20d=None, market="")


def test_log_outcome_definitive_conflict_raises_market_symbol_conflict_error(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with pytest.raises(MarketSymbolConflictError) as exc_info:
        store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=1.0, return_5d=None,
                           return_20d=None, market="IN")
    assert exc_info.value.symbol == US_ONLY_SYM
    assert exc_info.value.market == "IN"
    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes WHERE symbol = ?", (US_ONLY_SYM,)).fetchall()
    assert rows == []


@pytest.mark.parametrize("kwargs", [
    {},
    {"market": "GB"},
    {"market": "IN"},  # US_ONLY_SYM under IN — definitive conflict
])
def test_log_outcome_all_rejection_paths_occur_before_any_database_write(store, kwargs):
    """Structural proof: whatever the failure reason, zero rows exist in
    outcomes afterward — the SQLite connection/cursor is never reached
    before require_explicit_market either raises or returns cleanly."""
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with pytest.raises((MissingMarketContextError, InvalidMarketError, MarketSymbolConflictError)):
        store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=1.0, return_5d=None,
                           return_20d=None, **kwargs)
    with store._lock, store._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 0


def test_log_outcome_valid_explicit_in_call_succeeds(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    store.log_outcome(IN_ONLY_SYM, "short", pred_date, return_1d=1.5, return_5d=None,
                       return_20d=None, market="IN")
    with store._lock, store._conn() as c:
        row = c.execute("SELECT symbol, market, return_1d FROM outcomes WHERE symbol = ?",
                         (IN_ONLY_SYM,)).fetchone()
    assert tuple(row) == (IN_ONLY_SYM, "IN", 1.5)


def test_log_outcome_valid_explicit_us_call_succeeds(store):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    store.log_outcome(US_ONLY_SYM, "short", pred_date, return_1d=2.5, return_5d=None,
                       return_20d=None, market="US")
    with store._lock, store._conn() as c:
        row = c.execute("SELECT symbol, market, return_1d FROM outcomes WHERE symbol = ?",
                         (US_ONLY_SYM,)).fetchone()
    assert tuple(row) == (US_ONLY_SYM, "US", 2.5)


def test_log_outcome_structured_log_fields_emitted_on_conflict(caplog):
    pred_date = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    with caplog.at_level(logging.ERROR, logger="services.market_integrity"):
        with pytest.raises(MarketSymbolConflictError):
            require_explicit_market(US_ONLY_SYM, "IN", writer_context="log_outcome_test")
    matching = [r for r in caplog.records if getattr(r, "event", None) == EVENT_MARKET_SYMBOL_CONFLICT]
    assert len(matching) == 1
    assert matching[0].writer_context == "log_outcome_test"


class TestPostgresStoreLogOutcomeWriteBoundaryParity:

    def test_log_outcome_omitted_market_raises_before_pool_access(self):
        from services import postgres_store as pg
        with patch.object(pg, "_get_pool", side_effect=AssertionError("pool should never be touched")):
            with pytest.raises(MissingMarketContextError):
                pg.log_outcome("AAPL", "short", "2026-01-01", return_1d=1.0, return_5d=None, return_20d=None)

    def test_log_outcome_conflict_raises_before_pool_access(self):
        from services import postgres_store as pg
        with patch.object(pg, "_get_pool", side_effect=AssertionError("pool should never be touched")):
            with pytest.raises(MarketSymbolConflictError):
                pg.log_outcome("AAPL", "short", "2026-01-01", return_1d=1.0, return_5d=None,
                                return_20d=None, market="IN")

    def test_log_outcome_no_default_parameter_exists(self):
        """log_outcome must not have a bare `market: str = "IN"` default —
        confirmed via signature inspection: the parameter's default must be
        the MISSING_MARKET sentinel, never a literal market string."""
        from services import postgres_store as pg
        sig = inspect.signature(pg.log_outcome)
        assert sig.parameters["market"].default is MISSING_MARKET
        assert sig.parameters["market"].kind == inspect.Parameter.KEYWORD_ONLY


def test_log_outcome_sqlite_no_default_parameter_exists():
    import services.alpha_engine.store as store_mod
    sig = inspect.signature(store_mod.log_outcome)
    assert sig.parameters["market"].default is MISSING_MARKET
    assert sig.parameters["market"].kind == inspect.Parameter.KEYWORD_ONLY


# ─────────────────────────────────────────────────────────────────────────────
# L. migrate_sqlite_to_postgres.py — behavioral proof both log_prediction and
# log_outcome receive the source row's own market explicitly
# ─────────────────────────────────────────────────────────────────────────────

def test_migrate_sqlite_to_postgres_passes_source_market_explicitly(tmp_path, monkeypatch):
    """Builds a real temp SQLite db shaped like alpha_engine.db, runs the
    migration script's main() against it with the Postgres side
    (services.postgres_store) mocked out entirely — no real database
    connection anywhere in this test — and asserts the captured
    log_prediction/log_outcome calls both received the source row's own
    market value explicitly, never omitted, never a default."""
    import sqlite3 as _sqlite3

    db_path = tmp_path / "alpha_engine.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, logged_at TEXT, symbol TEXT, horizon TEXT, market TEXT,
            tech_z REAL, fund_z REAL, sentiment_z REAL, quality_z REAL,
            combined_alpha REAL, meta_alpha REAL, signal TEXT, price REAL, regime_label TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE outcomes (
            id INTEGER PRIMARY KEY, resolved_at TEXT, symbol TEXT, horizon TEXT, pred_date TEXT,
            market TEXT, return_1d REAL, return_5d REAL, return_20d REAL, return_60d REAL
        )
    """)
    conn.execute("CREATE TABLE regime_log (id INTEGER PRIMARY KEY, regime_id INTEGER, label TEXT, features TEXT)")
    conn.execute("""
        INSERT INTO predictions (logged_at, symbol, horizon, market, tech_z, fund_z, sentiment_z,
                                  quality_z, combined_alpha, meta_alpha, signal, price, regime_label)
        VALUES ('2026-01-01T00:00:00+00:00', 'AAPL', 'short', 'US', 0.1, 0.2, 0.3, 0.4, 0.5, 0.5, 'BUY', 150.0, '')
    """)
    conn.execute("""
        INSERT INTO outcomes (resolved_at, symbol, horizon, pred_date, market,
                              return_1d, return_5d, return_20d, return_60d)
        VALUES ('2026-01-01T00:00:00+00:00', 'AAPL', 'short', '2026-01-01', 'US', 1.0, 2.0, NULL, NULL)
    """)
    conn.commit()
    conn.close()

    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    sys.path.insert(0, scripts_dir)
    import migrate_sqlite_to_postgres as migrate_mod
    importlib.reload(migrate_mod)
    monkeypatch.setattr(migrate_mod, "SQLITE_PATH", str(db_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-host-never-contacted/fake")

    captured_prediction_calls = []
    captured_outcome_calls = []
    fake_pg = MagicMock()
    fake_pg.log_prediction = MagicMock(side_effect=lambda **kw: captured_prediction_calls.append(kw))
    fake_pg.log_outcome = MagicMock(side_effect=lambda **kw: captured_outcome_calls.append(kw))
    monkeypatch.setattr(migrate_mod, "pg", fake_pg)

    migrate_mod.main()

    assert len(captured_prediction_calls) == 1
    assert captured_prediction_calls[0]["market"] == "US"
    assert len(captured_outcome_calls) == 1
    assert captured_outcome_calls[0]["market"] == "US"
    assert captured_outcome_calls[0]["_writer_source"] == "migrate_sqlite_to_postgres"


# ─────────────────────────────────────────────────────────────────────────────
# M. Manifest schema version "2" — deliberate breaking safety boundary
# ─────────────────────────────────────────────────────────────────────────────

def test_newly_generated_manifest_is_version_2(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    assert manifest["manifest_version"] == "2"
    assert manifest_backfill.MANIFEST_VERSION == "2"


def test_valid_version_2_manifest_validates_cleanly(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    manifest_backfill.validate_manifest_structure(manifest, manifest["manifest_sha256"])  # must not raise


def test_version_1_manifest_gets_clear_compatibility_error(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    old = json.loads(json.dumps(manifest))
    old["manifest_version"] = "1"
    old["manifest_sha256"] = manifest_backfill.compute_checksum(old)
    with pytest.raises(manifest_backfill.ManifestValidationError, match="predates the current schema"):
        manifest_backfill.validate_manifest_structure(old, old["manifest_sha256"])


def test_unknown_manifest_version_fails_closed(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    unknown = json.loads(json.dumps(manifest))
    unknown["manifest_version"] = "999"
    unknown["manifest_sha256"] = manifest_backfill.compute_checksum(unknown)
    with pytest.raises(manifest_backfill.ManifestValidationError, match="unsupported manifest_version"):
        manifest_backfill.validate_manifest_structure(unknown, unknown["manifest_sha256"])


def test_version_1_manifest_never_reaches_preflight_or_execution(store):
    """execute_manifest must refuse a deprecated-version manifest at the
    validate_manifest_structure stage — never touching preflight or write."""
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    old = json.loads(json.dumps(manifest))
    old["manifest_version"] = "1"
    old["manifest_sha256"] = manifest_backfill.compute_checksum(old)

    with patch.object(manifest_backfill, "preflight_manifest",
                       side_effect=AssertionError("preflight must never run for a deprecated manifest")):
        with pytest.raises(manifest_backfill.ManifestValidationError, match="predates the current schema"):
            manifest_backfill.execute_manifest(old, old["manifest_sha256"], dry_run=True)


def test_version_2_manifest_missing_new_fields_fails_clearly(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)
    stripped = json.loads(json.dumps(manifest))
    del stripped["actual_candidate_count"]
    stripped["manifest_sha256"] = manifest_backfill.compute_checksum(stripped)
    with pytest.raises(manifest_backfill.ManifestValidationError, match="actual_candidate_count"):
        manifest_backfill.validate_manifest_structure(stripped, stripped["manifest_sha256"])


def test_genuine_zero_return_value_is_accepted_not_treated_as_missing(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10, price=100.0)

    def fake_fetch(symbol, pred_date_str, days, market="IN"):
        return 0.0 if days == 1 else None  # a genuine, legitimate flat/zero return

    with patch("services.alpha_engine.outcome_logger._fetch_return", side_effect=fake_fetch):
        manifest = manifest_backfill.build_manifest("IN", "short", batch_limit=10)

    assert manifest["actual_candidate_count"] == 1
    candidate = manifest["candidates"][0]
    assert candidate["resolved_values"]["return_1d"] == 0.0
    assert "return_1d" in candidate["fields_to_populate"]


def test_zero_usable_candidates_still_rejected_under_version_2(store):
    _raw_insert_prediction(store, symbol=US_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        with pytest.raises(manifest_backfill.ManifestGenerationError, match="zero usable candidates"):
            manifest_backfill.build_manifest("IN", "short", batch_limit=10)


def test_all_null_population_still_rejected_under_version_2(store):
    _raw_insert_prediction(store, symbol=IN_ONLY_SYM, horizon="short", market="IN", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=None):
        with pytest.raises(manifest_backfill.ManifestGenerationError, match="zero usable candidates"):
            manifest_backfill.build_manifest("IN", "short", batch_limit=10)


# ─────────────────────────────────────────────────────────────────────────────
# N. Repair plan — recursive forbidden-field validation
# ─────────────────────────────────────────────────────────────────────────────

def test_repair_plan_forbidden_field_at_top_level_rejected():
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["execute"] = True
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="forbidden"):
        planner.validate_repair_plan(plan)


def test_repair_plan_forbidden_field_in_candidate_rejected():
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["candidates"][0]["apply"] = True
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="forbidden"):
        planner.validate_repair_plan(plan)


def test_repair_plan_forbidden_field_inside_downstream_dependencies_rejected():
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["candidates"][0]["downstream_dependencies"][0]["write"] = True
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="forbidden") as exc_info:
        planner.validate_repair_plan(plan)
    assert "downstream_dependencies" in str(exc_info.value)


def test_repair_plan_deeply_nested_list_tampering_rejected():
    """Forbidden field injected inside an arbitrary, deeply nested
    list-of-dicts structure (not one of the plan's normal fields) — proves
    the recursive validator isn't hardcoded to only the known shape."""
    plan = _build_synthetic_plan()
    from services.alpha_engine.manifest_backfill import compute_checksum
    plan["candidates"][0]["extra_nested"] = [{"a": [{"b": {"repair_action": "do_it"}}]}]
    plan["manifest_sha256"] = compute_checksum(plan)
    with pytest.raises(planner.RepairPlanValidationError, match="forbidden") as exc_info:
        planner.validate_repair_plan(plan)
    assert "extra_nested" in str(exc_info.value)


def test_repair_plan_recursive_validation_bounded_depth():
    """A pathologically deep structure (beyond _MAX_VALIDATION_DEPTH) fails
    closed with a clear depth-exceeded error, never a Python RecursionError
    escaping to the caller."""
    deep = {}
    node = deep
    for _ in range(50):
        node["child"] = {}
        node = node["child"]
    with pytest.raises(planner.RepairPlanValidationError, match="maximum validation depth"):
        planner._check_forbidden_fields_recursive(deep)


def test_repair_plan_normal_valid_plan_still_byte_deterministic_after_recursive_validation():
    """The recursive validator is read-only — running it must not mutate
    the plan or otherwise affect determinism."""
    from services.alpha_engine.manifest_backfill import canonical_json
    p1 = _build_synthetic_plan()
    planner.validate_repair_plan(p1)
    p2 = _build_synthetic_plan()
    planner.validate_repair_plan(p2)
    assert canonical_json(p1).encode() == canonical_json(p2).encode()


def test_repair_plan_verify_still_requires_no_database_credentials_after_recursive_validation(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    plan = _build_synthetic_plan()
    path = tmp_path / "plan.json"
    from services.alpha_engine.manifest_backfill import canonical_json
    path.write_text(canonical_json(plan))

    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                                       "plan_market_contamination_repair.py"),
         "--verify", str(path)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k not in ("AUDIT_DATABASE_URL", "DATABASE_URL")},
    )
    assert result.returncode == 0, result.stderr
