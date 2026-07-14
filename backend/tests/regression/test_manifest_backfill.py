"""
StockSense360 Forensic Remediation — Phase 1A.3: Deterministic,
Manifest-Locked Outcome Backfill.

Covers the Phase 1A.2 finding: get_unresolved_predictions had no ORDER BY and
didn't even select predictions.id, so a --batch-size slice of an unbounded
eligible set (confirmed to be 552 rows for a window meant to hold exactly
200) could not be proven to select the same candidates across a dry-run and
a later --execute. This phase adds explicit, stable ordering
(pred_date ASC, symbol ASC, prediction_id ASC), a versioned/checksummed JSON
manifest that freezes both the exact candidate set AND the exact
forward-return values computed at generation time (execution never
re-fetches prices), mandatory live-state preflight re-validation that aborts
the whole batch on any drift, and one all-or-nothing transaction for the
approved batch.

All tests run against the real SQLite backend (services/alpha_engine/store.py)
via a temp file — no mocking of SQL/transaction semantics — plus a handful of
mocked-connection tests against services/postgres_store.py's SQL text (no
live Postgres is available or permitted in this phase). Only
_fetch_return (the real yfinance network call) is ever mocked.
"""

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.alpha_engine import manifest_backfill  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — reuse the same isolated-SQLite pattern as test_outcome_lifecycle_repair.py
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


def _log_prediction(store, symbol="AAPL", horizon="short", market="US",
                     days_ago=10, price=100.0):
    logged_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with store._lock, store._conn() as c:
        cur = c.execute("""
            INSERT INTO predictions (logged_at, symbol, horizon, market, price)
            VALUES (?, ?, ?, ?, ?)
        """, (logged_at, symbol, horizon, market, price))
        return cur.lastrowid


def _pred_date_str(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 1-3, 5-6: deterministic ordering, prediction_id returned, filtering before LIMIT
# ─────────────────────────────────────────────────────────────────────────────

def test_deterministic_order_identical_across_repeated_calls(store):
    for i, days_ago in enumerate([10, 12, 11, 15, 9]):
        _log_prediction(store, symbol=f"SYM{i}", horizon="short", market="US", days_ago=days_ago)

    first = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    second = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    third = store.get_unresolved_predictions("short", min_days_old=3, market="US")

    assert [c["prediction_id"] for c in first] == [c["prediction_id"] for c in second] == \
           [c["prediction_id"] for c in third]
    # Oldest pred_date first.
    dates = [c["pred_date"] for c in first]
    assert dates == sorted(dates)


def test_insertion_order_does_not_affect_selected_order(store, tmp_path, monkeypatch):
    # Insert newest-first (reverse of the expected output order) to prove the
    # ORDER BY, not insertion sequence, determines the result.
    ids = []
    for days_ago in [9, 15, 10, 12, 11]:
        ids.append(_log_prediction(store, symbol=f"REV{days_ago}", horizon="short",
                                    market="US", days_ago=days_ago))

    result = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    dates = [c["pred_date"] for c in result]
    assert dates == sorted(dates), "result must be oldest-first regardless of insertion order"


def test_prediction_id_is_returned(store):
    pid = _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    result = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert len(result) == 1
    assert result[0]["prediction_id"] == pid


def test_market_filtering_occurs_before_limit(store):
    _log_prediction(store, symbol="US_ONE", horizon="short", market="US", days_ago=10)
    _log_prediction(store, symbol="IN_ONE", horizon="short", market="IN", days_ago=10)

    us_result = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert [c["symbol"] for c in us_result] == ["US_ONE"]

    in_result = store.get_unresolved_predictions("short", min_days_old=3, market="IN")
    assert [c["symbol"] for c in in_result] == ["IN_ONE"]


def test_horizon_filtering_occurs_before_limit(store):
    _log_prediction(store, symbol="SHORT_ONE", horizon="short", market="US", days_ago=10)
    _log_prediction(store, symbol="MEDIUM_ONE", horizon="medium", market="US", days_ago=35)

    short_result = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert [c["symbol"] for c in short_result] == ["SHORT_ONE"]

    medium_result = store.get_unresolved_predictions("medium", min_days_old=30, market="US")
    assert [c["symbol"] for c in medium_result] == ["MEDIUM_ONE"]


def test_ambiguous_groups_removed_before_ordering_and_limit(store):
    logged_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO predictions (logged_at, symbol, horizon, market, price) "
                  "VALUES (?, 'AMBIG', 'short', 'US', 100.0)", (logged_at,))
        c.execute("INSERT INTO predictions (logged_at, symbol, horizon, market, price) "
                  "VALUES (?, 'AMBIG', 'short', 'US', 105.0)", (logged_at,))
        c.execute("INSERT INTO predictions (logged_at, symbol, horizon, market, price) "
                  "VALUES (?, 'CLEAN', 'short', 'US', 50.0)", (logged_at,))

    result = store.get_unresolved_predictions("short", min_days_old=3, market="US")
    assert [c["symbol"] for c in result] == ["CLEAN"], \
        "the ambiguous AMBIG group must never reach the ordered/limited output at all"


# ─────────────────────────────────────────────────────────────────────────────
# 7: dry-run preview and manifest generation use the same ordered IDs
# ─────────────────────────────────────────────────────────────────────────────

def test_dry_run_and_manifest_generation_use_same_ordering(store):
    for days_ago in [10, 14, 11]:
        _log_prediction(store, symbol=f"S{days_ago}", horizon="short", market="US", days_ago=days_ago)

    from services.alpha_engine import outcome_logger
    with patch.object(outcome_logger, "_fetch_return", return_value=1.0):
        stats = outcome_logger.resolve_pair("US", "short", 3, True, True, False, False, dry_run=True)
    with patch.object(outcome_logger, "_fetch_return", return_value=1.0):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=500)

    # Both paths walk the same get_unresolved_predictions() ordering; the
    # manifest's candidate id sequence must match what a same-order dry-run
    # would have examined.
    ordered_ids = [c["prediction_id"] for c in store.get_unresolved_predictions("short", 3, "US")]
    manifest_ids = [c["prediction_id"] for c in manifest["candidates"]]
    assert manifest_ids == ordered_ids
    assert stats["examined"] == len(ordered_ids)


# ─────────────────────────────────────────────────────────────────────────────
# 8-11, 18: manifest structural validation
# ─────────────────────────────────────────────────────────────────────────────

def test_manifest_checksum_is_stable(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        m1 = manifest_backfill.build_manifest("US", "short", batch_limit=10)
        m2 = manifest_backfill.build_manifest("US", "short", batch_limit=10)
    # generated_at_utc will differ between calls -- recompute checksum over
    # otherwise-identical content to prove the checksum reflects the data,
    # not the wall-clock timestamp.
    m2["generated_at_utc"] = m1["generated_at_utc"]
    m2["manifest_sha256"] = manifest_backfill.compute_checksum(m2)
    assert m1["manifest_sha256"] == m2["manifest_sha256"]


def test_tampered_manifest_fails_checksum_validation(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    tampered["candidates"][0]["reference_price"] = 999999.0  # tamper, checksum left stale

    with pytest.raises(manifest_backfill.ManifestValidationError, match="checksum"):
        manifest_backfill.validate_manifest_structure(tampered, tampered["manifest_sha256"])


def test_duplicate_manifest_ids_fail_closed(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    dup = json.loads(json.dumps(manifest))
    dup["candidates"].append(dict(dup["candidates"][0]))
    dup["candidate_count"] = len(dup["candidates"])
    dup["actual_candidate_count"] = len(dup["candidates"])
    dup["requested_candidate_count"] = len(dup["candidates"])
    dup["manifest_sha256"] = manifest_backfill.compute_checksum(dup)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="duplicate"):
        manifest_backfill.validate_manifest_structure(dup, dup["manifest_sha256"])


def test_unsupported_manifest_version_fails_closed(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    bad = json.loads(json.dumps(manifest))
    bad["manifest_version"] = "999"
    bad["manifest_sha256"] = manifest_backfill.compute_checksum(bad)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="version"):
        manifest_backfill.validate_manifest_structure(bad, bad["manifest_sha256"])


def test_manifest_count_mismatch_fails_closed(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    bad = json.loads(json.dumps(manifest))
    bad["candidate_count"] = bad["candidate_count"] + 5
    bad["manifest_sha256"] = manifest_backfill.compute_checksum(bad)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="candidate_count"):
        manifest_backfill.validate_manifest_structure(bad, bad["manifest_sha256"])


# ─────────────────────────────────────────────────────────────────────────────
# 12-17: preflight drift detection (each fails the WHOLE batch closed)
# ─────────────────────────────────────────────────────────────────────────────

def _build_single_candidate_manifest(store, **kwargs):
    _log_prediction(store, **kwargs)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        return manifest_backfill.build_manifest(kwargs.get("market", "US"),
                                                  kwargs.get("horizon", "short"), batch_limit=10)


def test_missing_prediction_id_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    with store._lock, store._conn() as c:
        c.execute("DELETE FROM predictions WHERE id = ?", (manifest["candidates"][0]["prediction_id"],))

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "no longer exists" in issues[0]["reason"]


def test_market_mismatch_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    pid = manifest["candidates"][0]["prediction_id"]
    with store._lock, store._conn() as c:
        c.execute("UPDATE predictions SET market = 'IN' WHERE id = ?", (pid,))

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "market drifted" in issues[0]["reason"]


def test_horizon_mismatch_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    pid = manifest["candidates"][0]["prediction_id"]
    with store._lock, store._conn() as c:
        c.execute("UPDATE predictions SET horizon = 'medium' WHERE id = ?", (pid,))

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "horizon drifted" in issues[0]["reason"]


def test_date_drift_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    pid = manifest["candidates"][0]["prediction_id"]
    new_logged_at = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
    with store._lock, store._conn() as c:
        c.execute("UPDATE predictions SET logged_at = ? WHERE id = ?", (new_logged_at, pid))

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "prediction_date drifted" in issues[0]["reason"]


def test_price_drift_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10, price=100.0)
    pid = manifest["candidates"][0]["prediction_id"]
    with store._lock, store._conn() as c:
        c.execute("UPDATE predictions SET price = 123.45 WHERE id = ?", (pid,))

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "reference_price drifted" in issues[0]["reason"]


def test_already_resolved_intended_field_fails_closed(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    c0 = manifest["candidates"][0]
    # Simulate the normal resolver having since created an outcome row.
    store.log_outcome(c0["symbol"], "short", c0["prediction_date"], return_1d=2.2, return_5d=None,
                       return_20d=None, market="US")

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "now exists but manifest recorded none" in issues[0]["reason"]


def test_changed_existing_non_null_outcome_data_fails_closed(store):
    _log_prediction(store, symbol="AAPL", horizon="medium", market="US", days_ago=35)
    pred_date = _pred_date_str(35)
    # Pre-existing partial outcome (as if return_5d resolved earlier), matching
    # the Phase 1A "progressive horizon resolution" scenario.
    store.log_outcome("AAPL", "medium", pred_date, return_1d=None, return_5d=3.3, return_20d=None,
                      market="US")

    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=9.9):
        manifest = manifest_backfill.build_manifest("US", "medium", batch_limit=10)
    assert manifest["candidates"][0]["existing_outcome_fields"]["return_5d"] == 3.3

    # Something else changes the recorded return_5d before execution.
    with store._lock, store._conn() as c:
        c.execute("UPDATE outcomes SET return_5d = 7.7 WHERE symbol='AAPL' AND horizon='medium'")

    issues = manifest_backfill.preflight_manifest(manifest)
    assert len(issues) == 1
    assert "return_5d drifted" in issues[0]["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 19-21: CLI-level refusal behavior for manifest execution
# ─────────────────────────────────────────────────────────────────────────────

class TestManifestCLI:

    def _import_script(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        import backfill_outcomes
        importlib.reload(backfill_outcomes)
        return backfill_outcomes

    def test_execute_without_manifest_is_refused(self, store):
        script = self._import_script()
        with patch.object(sys, "argv", ["backfill_outcomes.py", "--execute",
                                        "--confirm", script.CONFIRM_TOKEN]):
            rc = script.main()
        assert rc != 0

    def test_manifest_execute_without_confirm_is_refused(self, store, tmp_path):
        script = self._import_script()
        _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
        manifest_path = str(tmp_path / "m.json")
        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
             patch.object(sys, "argv", ["backfill_outcomes.py", "--market", "US", "--horizon", "short",
                                        "--manifest-out", manifest_path]):
            assert script.main() == 0
        manifest = json.load(open(manifest_path))

        with patch.object(sys, "argv", ["backfill_outcomes.py", "--manifest", manifest_path,
                                        "--manifest-sha256", manifest["manifest_sha256"], "--execute"]):
            rc = script.main()
        assert rc != 0
        with store._lock, store._conn() as c:
            assert c.execute("SELECT * FROM outcomes").fetchall() == []

    def test_manifest_execute_with_mismatched_checksum_is_refused(self, store, tmp_path):
        script = self._import_script()
        _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
        manifest_path = str(tmp_path / "m.json")
        with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
             patch.object(sys, "argv", ["backfill_outcomes.py", "--market", "US", "--horizon", "short",
                                        "--manifest-out", manifest_path]):
            assert script.main() == 0

        with patch.object(sys, "argv", ["backfill_outcomes.py", "--manifest", manifest_path,
                                        "--manifest-sha256", "0" * 64,
                                        "--execute", "--confirm", script.CONFIRM_TOKEN]):
            rc = script.main()
        assert rc != 0
        with store._lock, store._conn() as c:
            assert c.execute("SELECT * FROM outcomes").fetchall() == []


# ─────────────────────────────────────────────────────────────────────────────
# 22-25: zero writes on preview/preflight-failure; exact-set writes on success
# ─────────────────────────────────────────────────────────────────────────────

def test_manifest_generation_performs_zero_writes(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest_backfill.build_manifest("US", "short", batch_limit=10)
    with store._lock, store._conn() as c:
        assert c.execute("SELECT * FROM outcomes").fetchall() == []


def test_preflight_failure_performs_zero_writes(store):
    manifest = _build_single_candidate_manifest(store, symbol="AAPL", horizon="short",
                                                 market="US", days_ago=10)
    pid = manifest["candidates"][0]["prediction_id"]
    with store._lock, store._conn() as c:
        c.execute("DELETE FROM predictions WHERE id = ?", (pid,))

    with pytest.raises(manifest_backfill.ManifestPreflightError):
        manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)

    with store._lock, store._conn() as c:
        assert c.execute("SELECT * FROM outcomes").fetchall() == []


def test_successful_manifest_batch_writes_exactly_the_manifest_ids(store):
    for i, days_ago in enumerate([10, 11, 12]):
        _log_prediction(store, symbol=f"SYM{i}", horizon="short", market="US", days_ago=days_ago)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)
    assert manifest["candidate_count"] == 3

    result = manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)

    assert len(result["written"]) == 3
    written_keys = {(w["symbol"], w["pred_date"]) for w in result["written"]}
    manifest_keys = {(c["symbol"], c["prediction_date"]) for c in manifest["candidates"]}
    assert written_keys == manifest_keys

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes").fetchall()
    assert len(rows) == 3


def test_no_unlisted_prediction_is_touched(store):
    _log_prediction(store, symbol="INCLUDED", horizon="short", market="US", days_ago=10)
    _log_prediction(store, symbol="NOT_INCLUDED", horizon="short", market="US", days_ago=11)

    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=1)
    assert manifest["candidate_count"] == 1
    assert manifest["candidates"][0]["symbol"] == "NOT_INCLUDED"  # oldest first

    manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT symbol FROM outcomes").fetchall()
    assert [r["symbol"] for r in rows] == ["NOT_INCLUDED"], \
        "INCLUDED was never in the manifest and must not have been written"


# ─────────────────────────────────────────────────────────────────────────────
# 26: transaction failure rolls back the whole batch
# ─────────────────────────────────────────────────────────────────────────────

def test_transaction_failure_rolls_back_all_candidate_writes(store):
    for i in range(3):
        _log_prediction(store, symbol=f"TX{i}", horizon="short", market="US", days_ago=10 + i)

    writes = [
        {"symbol": "TX0", "horizon": "short", "market": "US", "pred_date": _pred_date_str(10),
         "return_1d": 1.0, "return_5d": 2.0, "return_20d": None, "return_60d": None,
         "expected_existing": {"id": None, "return_1d": None, "return_5d": None,
                                "return_20d": None, "return_60d": None}},
        {"symbol": "TX1", "horizon": "short", "market": "US", "pred_date": _pred_date_str(11),
         "return_1d": 1.0, "return_5d": 2.0, "return_20d": None, "return_60d": None,
         # Deliberately wrong expectation to force OutcomeDriftError mid-batch.
         "expected_existing": {"id": 999999, "return_1d": None, "return_5d": None,
                                "return_20d": None, "return_60d": None}},
        {"symbol": "TX2", "horizon": "short", "market": "US", "pred_date": _pred_date_str(12),
         "return_1d": 1.0, "return_5d": 2.0, "return_20d": None, "return_60d": None,
         "expected_existing": {"id": None, "return_1d": None, "return_5d": None,
                                "return_20d": None, "return_60d": None}},
    ]

    with pytest.raises(store.OutcomeDriftError):
        store.execute_outcome_writes_transactional(writes)

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes").fetchall()
    assert rows == [], "TX0's write (processed before the TX1 failure) must have been rolled back too"


# ─────────────────────────────────────────────────────────────────────────────
# 27: rerunning an already-applied manifest fails clearly, never overwrites
# ─────────────────────────────────────────────────────────────────────────────

def test_rerun_already_applied_manifest_fails_clearly_without_overwriting(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    first = manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)
    assert len(first["written"]) == 1

    with store._lock, store._conn() as c:
        after_first = dict(c.execute("SELECT * FROM outcomes").fetchone())

    with pytest.raises(manifest_backfill.ManifestPreflightError):
        manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)

    with store._lock, store._conn() as c:
        after_rerun = dict(c.execute("SELECT * FROM outcomes").fetchone())
    assert after_first == after_rerun, "the already-applied outcome must be completely unchanged"


# ─────────────────────────────────────────────────────────────────────────────
# 28: concurrent resolver-style fill is not claimed as a canary write
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_resolver_fill_not_claimed_as_canary_write(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    pred_date = manifest["candidates"][0]["prediction_date"]
    # Simulate the normal 6-hourly resolver independently resolving this
    # exact prediction between manifest generation and canary execution.
    store.log_outcome("AAPL", "short", pred_date, return_1d=4.4, return_5d=None,
                       return_20d=None, market="US")

    with pytest.raises(manifest_backfill.ManifestPreflightError) as exc_info:
        manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)
    assert "resolver" in exc_info.value.issues[0]["reason"]

    with store._lock, store._conn() as c:
        row = dict(c.execute("SELECT * FROM outcomes").fetchone())
    assert row["return_1d"] == 4.4, "the resolver's own value must be exactly what remains — untouched"


# ─────────────────────────────────────────────────────────────────────────────
# 29: Postgres and SQLite deterministic-ordering equivalence
# ─────────────────────────────────────────────────────────────────────────────

def test_postgres_and_sqlite_ordering_are_equivalent():
    """SQLite's real behavior is exercised by the tests above; here we assert
    postgres_store.py's SQL text carries the identical ORDER BY semantics
    (no live Postgres is available/permitted in this phase)."""
    from services import postgres_store as pg

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = []
    fake_pool = MagicMock()
    fake_pool.connection.return_value.__enter__.return_value = fake_conn
    fake_pool.connection.return_value.__exit__.return_value = False

    with patch.object(pg, "_get_pool", return_value=fake_pool):
        pg.get_unresolved_predictions("short", min_days_old=3, market="US")

    executed_sql = fake_conn.execute.call_args[0][0]
    assert "ORDER BY date(p.logged_at) ASC, p.symbol ASC, min(p.id) ASC" in executed_sql
    assert "min(p.id) AS prediction_id" in executed_sql


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial review follow-ups: confirmed transactional-writer race bug fix,
# real multi-thread concurrency, manifest numeric/size hardening, source_commit,
# CLI non-override, and no-op/empty-manifest/no-secret guarantees.
# ─────────────────────────────────────────────────────────────────────────────

def test_transactional_writer_detects_field_filled_since_manifest_generation(store):
    """
    Reproduces the exact gap found in adversarial review: a partial outcome
    row already existed (return_5d filled) when the manifest was generated,
    with return_20d still NULL and intended for this batch to populate. The
    normal resolver then independently fills return_20d before the canary's
    transaction reaches that row. Before the fix, execute_outcome_writes_
    transactional's drift check only looked at columns with a non-None
    `expected` value, so a column that went NULL -> filled was invisible to
    it — the COALESCE upsert would silently preserve the resolver's value
    while still reporting the row as this batch's own successful write.
    """
    _log_prediction(store, symbol="AAPL", horizon="medium", market="US", days_ago=35)
    pred_date = _pred_date_str(35)
    existing_id = store.log_outcome("AAPL", "medium", pred_date, return_1d=None, return_5d=3.3,
                                     return_20d=None, market="US")
    with store._lock, store._conn() as c:
        existing_id = c.execute("SELECT id FROM outcomes").fetchone()["id"]

    # Resolver fills return_20d before the canary gets there.
    store.log_outcome("AAPL", "medium", pred_date, return_1d=None, return_5d=None,
                       return_20d=42.0, market="US")

    writes = [{
        "symbol": "AAPL", "horizon": "medium", "market": "US", "pred_date": pred_date,
        "return_1d": None, "return_5d": None, "return_20d": 999.0, "return_60d": None,
        "expected_existing": {"id": existing_id, "return_1d": None, "return_5d": 3.3,
                               "return_20d": None, "return_60d": None},
    }]

    with pytest.raises(store.OutcomeDriftError, match="return_20d"):
        store.execute_outcome_writes_transactional(writes)

    with store._lock, store._conn() as c:
        row = dict(c.execute("SELECT * FROM outcomes WHERE symbol='AAPL'").fetchone())
    assert row["return_20d"] == 42.0, "the resolver's real value must survive untouched"
    assert row["return_5d"] == 3.3, "the pre-existing value must also be untouched"


def test_real_concurrent_canary_and_resolver_resolve_safely(store):
    """
    Genuine two-thread test against the real SQLite file (not mocked): a
    canary batch and a simulated resolver both attempt to write the same
    brand-new outcome key at essentially the same wall-clock moment. The
    module-level store._lock fully serializes them (in-process), so exactly
    one of two safe outcomes is possible — never a third, corrupted one:
      (a) the canary's transaction acquires the lock first, commits its
          value, and the resolver's later write safely COALESCE-preserves
          it (no exception, no double-write); or
      (b) the resolver's autocommit write lands first, and the canary's own
          drift check (this phase's fix) detects the now-existing row it
          didn't expect and raises, leaving zero net effect from the canary.
    Both threads are released via a Barrier so the OS scheduler — not the
    test — decides which one actually goes first; the assertions below hold
    regardless of which branch fires.
    """
    import threading

    _log_prediction(store, symbol="RACE", horizon="short", market="US", days_ago=10)
    pred_date = _pred_date_str(10)

    writes = [{
        "symbol": "RACE", "horizon": "short", "market": "US", "pred_date": pred_date,
        "return_1d": 1.11, "return_5d": None, "return_20d": None, "return_60d": None,
        "expected_existing": {"id": None, "return_1d": None, "return_5d": None,
                               "return_20d": None, "return_60d": None},
    }]

    barrier = threading.Barrier(2)
    outcome = {}

    def _canary():
        barrier.wait(timeout=5)
        try:
            outcome["canary_result"] = store.execute_outcome_writes_transactional(writes)
        except store.OutcomeDriftError as e:
            outcome["canary_error"] = e

    def _resolver():
        barrier.wait(timeout=5)
        try:
            store.log_outcome("RACE", "short", pred_date, return_1d=9.99, return_5d=None,
                               return_20d=None, market="US")
        except Exception as e:  # pragma: no cover - only if something truly broke
            outcome["resolver_error"] = e

    t1 = threading.Thread(target=_canary)
    t2 = threading.Thread(target=_resolver)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "both threads must finish, not deadlock"
    assert "resolver_error" not in outcome, "the resolver's own write must never itself fail"

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT * FROM outcomes WHERE symbol='RACE'").fetchall()
    assert len(rows) == 1, "exactly one outcome row must exist — never zero, never a duplicate"
    row = dict(rows[0])

    if "canary_error" in outcome:
        # Resolver won the race; canary correctly detected it and aborted.
        assert row["return_1d"] == 9.99
    else:
        # Canary won the race; resolver's later write safely preserved it.
        assert outcome["canary_result"][0]["operation"] == "INSERT"
        assert row["return_1d"] == 1.11


def test_manifest_generation_rejects_non_finite_values(store):
    """canonical_json's allow_nan=False must reject NaN/Infinity rather than
    silently freezing a non-spec-compliant value into a 'checksummed' manifest."""
    with pytest.raises(ValueError):
        manifest_backfill.canonical_json({"x": float("nan")})
    with pytest.raises(ValueError):
        manifest_backfill.canonical_json({"x": float("inf")})


def test_manifest_size_bound_is_enforced(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    oversized = json.loads(json.dumps(manifest))
    # Synthesize extra candidates beyond the bound (structurally, without a
    # real DB round-trip — this test targets the size guard, not selection).
    template = oversized["candidates"][0]
    for i in range(manifest_backfill.MAX_MANIFEST_CANDIDATES + 1):
        c = dict(template)
        c["prediction_id"] = 10_000 + i
        oversized["candidates"].append(c)
    oversized["candidate_count"] = len(oversized["candidates"])
    oversized["manifest_sha256"] = manifest_backfill.compute_checksum(oversized)

    with pytest.raises(manifest_backfill.ManifestValidationError, match="MAX_MANIFEST_CANDIDATES"):
        manifest_backfill.validate_manifest_structure(oversized, oversized["manifest_sha256"])


def test_source_commit_mismatch_is_reported_not_silently_ignored(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    tampered = json.loads(json.dumps(manifest))
    tampered["source_commit"] = "0" * 40  # a commit that is not currently checked out
    tampered["manifest_sha256"] = manifest_backfill.compute_checksum(tampered)

    result = manifest_backfill.execute_manifest(tampered, tampered["manifest_sha256"], dry_run=True)
    assert result["source_commit_mismatch"] is True
    assert result["manifest_source_commit"] == "0" * 40


def test_execute_manifest_never_calls_fetch_return(store):
    """Proves execution uses only the resolved_values frozen at generation
    time — it must never re-fetch or recalculate historical prices."""
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    with patch("services.alpha_engine.outcome_logger._fetch_return",
               side_effect=AssertionError("execute_manifest must never call _fetch_return")):
        result = manifest_backfill.execute_manifest(manifest, manifest["manifest_sha256"], dry_run=False)
    assert len(result["written"]) == 1


def test_empty_manifest_reports_zero_written_not_false_success(store):
    # Phase 1A.6: a manifest generation must fail closed when candidate_count
    # would be zero, rather than silently producing an empty-but-"valid"
    # manifest that could later execute as an unremarked no-op — this
    # supersedes the pre-1A.6 behavior where an empty manifest was buildable.
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        with pytest.raises(manifest_backfill.ManifestGenerationError, match="zero usable candidates"):
            manifest_backfill.build_manifest("US", "short", batch_limit=10)


def test_unknown_extra_manifest_field_does_not_alter_behavior(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    with_extra = json.loads(json.dumps(manifest))
    with_extra["totally_unrecognized_field"] = "should be ignored"
    with_extra["candidates"][0]["another_unrecognized_field"] = 12345
    with_extra["manifest_sha256"] = manifest_backfill.compute_checksum(with_extra)

    # Must not raise, and must preflight identically to the manifest without the extra fields.
    issues = manifest_backfill.preflight_manifest(with_extra)
    assert issues == []


def test_manifest_contains_no_credentials_or_emails(store):
    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5):
        manifest = manifest_backfill.build_manifest("US", "short", batch_limit=10)

    text = json.dumps(manifest)
    assert "@" not in text, "no email addresses should ever appear in a manifest"
    assert "postgresql://" not in text
    assert "password" not in text.lower()
    assert "DATABASE_URL" not in text


def test_cli_manifest_out_refuses_to_overwrite_without_flag(store, tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import backfill_outcomes
    importlib.reload(backfill_outcomes)

    manifest_path = str(tmp_path / "existing.json")
    with open(manifest_path, "w") as f:
        f.write("{}")

    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
         patch.object(sys, "argv", ["backfill_outcomes.py", "--market", "US", "--horizon", "short",
                                    "--manifest-out", manifest_path]):
        rc = backfill_outcomes.main()
    assert rc != 0

    with open(manifest_path) as f:
        assert f.read() == "{}", "the existing file must be untouched without --manifest-overwrite"


def test_cli_market_horizon_args_cannot_override_manifest_execute(store, tmp_path):
    """--market/--horizon are preview-mode filters; in --manifest execute mode
    they must have no effect at all — only the manifest's own recorded
    market/horizon govern which candidates are touched."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    import backfill_outcomes
    importlib.reload(backfill_outcomes)

    _log_prediction(store, symbol="AAPL", horizon="short", market="US", days_ago=10)
    manifest_path = str(tmp_path / "m.json")
    with patch("services.alpha_engine.outcome_logger._fetch_return", return_value=1.5), \
         patch.object(sys, "argv", ["backfill_outcomes.py", "--market", "US", "--horizon", "short",
                                    "--manifest-out", manifest_path]):
        assert backfill_outcomes.main() == 0
    manifest = json.load(open(manifest_path))

    # Pass CONTRADICTORY --market/--horizon alongside --manifest execute — must be ignored.
    with patch.object(sys, "argv", [
        "backfill_outcomes.py", "--market", "IN", "--horizon", "long",
        "--manifest", manifest_path, "--manifest-sha256", manifest["manifest_sha256"],
        "--execute", "--confirm", backfill_outcomes.CONFIRM_TOKEN,
    ]):
        rc = backfill_outcomes.main()
    assert rc == 0

    with store._lock, store._conn() as c:
        rows = c.execute("SELECT symbol, market, horizon FROM outcomes").fetchall()
    assert len(rows) == 1
    assert rows[0]["market"] == "US" and rows[0]["horizon"] == "short", \
        "the manifest's own recorded market/horizon must govern, not the contradictory CLI flags"
