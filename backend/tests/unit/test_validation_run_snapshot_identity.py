"""
V-SNAP1B — immutable validation-run snapshot identity.

Confirmed defect (V-SNAP1A forensic investigation, source-proven, not
assumed): `get_latest_results()` (aggregate) and `get_per_stock_results()`
(per-stock) each independently execute their own `ORDER BY id DESC LIMIT
1` query against `val_runs`, at genuinely different wall-clock instants,
with zero shared identity between the two HTTP requests. If a new run for
the same horizon/universe is persisted in the gap between the two
requests, the page can render aggregate metrics from one run alongside
per-stock rows from a different, newer run.

Root cause of why this is safe to close additively (no migration): every
`val_runs` row is inserted exactly once, atomically, by run_validation()'s
single INSERT — with `summary` already fully computed in the SAME
statement (see validation_engine.py's persistence block). There is no
"running"/"failed"/partial row state representable in this schema at all
— a row either does not exist yet, or already has a non-null summary.
`summary IS NOT NULL` is therefore the correct, always-true-today,
future-proofing eligibility predicate — proven, not invented.

This file proves:
  A. get_latest_results() exposes the canonical database run_id,
     authoritative over anything embedded in the stored summary JSON.
  B. resolve_eligible_run_id()/the per-stock endpoint path honor an
     explicit run_id, fail closed on any mismatch, and never silently
     fall back to latest.
  C. Both endpoints share the identical eligibility definition.
  D. Legacy (omitted run_id) callers keep today's latest-run behavior.
"""
import json
import sqlite3

import pytest

import services.validation_engine as ve


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "validation_snapshot_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    ve._init_db()
    return db_path


def _insert_run(db_path, horizon="medium", universe="nifty100", summary=None, run_at="2026-08-01T00:00:00"):
    """summary=None simulates a hypothetical row with no summary yet —
    the application itself never produces one (see module docstring),
    but the SQL must still handle it defensively rather than assume it
    can't happen."""
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
            "VALUES (?, ?, 1, 0, ?, ?)",
            (run_at, horizon, json.dumps(summary) if summary is not None else None, universe),
        )
        return cur.lastrowid


def _insert_signal(db_path, run_id, symbol, horizon, predicted, correct, fwd_return_pct=1.0,
                    signal_date="2026-01-01"):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO val_signals
               (run_id, symbol, horizon, signal_date, composite_score, tech_score,
                rs_score, obv_score, mfi_score, predicted, fwd_return_pct,
                nifty_fwd_ret_pct, alpha_pct, actual_direction, correct)
               VALUES (?, ?, ?, ?, 65, 60, 60, 60, 60, ?, ?, 0, 0, 'UP', ?)""",
            (run_id, symbol, horizon, signal_date, predicted, fwd_return_pct, correct),
        )


# ── A. Aggregate endpoint exposes the canonical database run_id ────────────

def test_get_latest_results_exposes_canonical_database_run_id(isolated_db):
    run_id = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 55.0})
    result = ve.get_latest_results(horizon="medium", universe="nifty100")
    assert result["available"] is True
    assert result["run_id"] == run_id


def test_summary_json_cannot_override_canonical_database_run_id(isolated_db):
    """A hostile/stale run_id embedded inside the stored summary JSON
    must never override the database row's own id — the DB row is the
    single source of truth."""
    run_id = _insert_run(isolated_db, summary={"run_id": 999999, "buy_hit_rate_pct": 55.0})
    result = ve.get_latest_results(horizon="medium", universe="nifty100")
    assert result["run_id"] == run_id
    assert result["run_id"] != 999999


def test_get_latest_results_selects_the_truly_latest_eligible_run(isolated_db):
    _insert_run(isolated_db, summary={"buy_hit_rate_pct": 40.0}, run_at="2026-08-01T00:00:00")
    run_b = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 60.0}, run_at="2026-08-02T00:00:00")
    result = ve.get_latest_results(horizon="medium", universe="nifty100")
    assert result["run_id"] == run_b
    assert result["buy_hit_rate_pct"] == 60.0


# ── B. resolve_eligible_run_id() — explicit run pinning, fail-closed ───────

def test_resolve_eligible_run_id_returns_the_given_run_when_valid(isolated_db):
    run_id = _insert_run(isolated_db, summary={})
    resolved = ve.resolve_eligible_run_id(run_id, "medium", "nifty100")
    assert resolved == run_id


def test_explicit_run_a_remains_selected_after_newer_run_b_appears(isolated_db):
    """The core race-closure guarantee: an explicitly pinned run_id must
    not be silently upgraded to a newer run that appears afterward."""
    run_a = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 40.0}, run_at="2026-08-01T00:00:00")
    resolved_before = ve.resolve_eligible_run_id(run_a, "medium", "nifty100")
    run_b = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 60.0}, run_at="2026-08-02T00:00:00")
    resolved_after = ve.resolve_eligible_run_id(run_a, "medium", "nifty100")
    assert resolved_before == run_a
    assert resolved_after == run_a
    assert resolved_after != run_b


def test_explicit_run_a_per_stock_returns_only_run_a_signals(isolated_db):
    run_a = _insert_run(isolated_db, summary={})
    run_b = _insert_run(isolated_db, summary={})
    _insert_signal(isolated_db, run_a, "AAA", "medium", "BUY", correct=1)
    _insert_signal(isolated_db, run_b, "BBB", "medium", "BUY", correct=1)

    resolved = ve.resolve_eligible_run_id(run_a, "medium", "nifty100")
    rows = ve.get_per_stock_results(run_id=resolved, horizon="medium", universe="nifty100")
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"AAA"}
    assert "BBB" not in symbols


def test_run_horizon_mismatch_fails_closed(isolated_db):
    run_id = _insert_run(isolated_db, horizon="medium", summary={})
    resolved = ve.resolve_eligible_run_id(run_id, "long", "nifty100")
    assert resolved is None


def test_run_universe_mismatch_fails_closed(isolated_db):
    run_id = _insert_run(isolated_db, universe="nifty100", summary={})
    resolved = ve.resolve_eligible_run_id(run_id, "medium", "us")
    assert resolved is None


def test_missing_run_fails_closed(isolated_db):
    resolved = ve.resolve_eligible_run_id(999999, "medium", "nifty100")
    assert resolved is None


def test_ineligible_run_with_no_summary_fails_closed(isolated_db):
    """Proves the eligibility predicate is genuinely enforced, not
    vacuously true — a hypothetical summary-less row (never produced by
    the application itself, but not schema-prevented) must be rejected
    exactly like a missing run, never silently served."""
    run_id = _insert_run(isolated_db, summary=None)
    resolved = ve.resolve_eligible_run_id(run_id, "medium", "nifty100")
    assert resolved is None


def test_explicit_invalid_run_never_falls_back_to_latest(isolated_db):
    """An explicit but invalid run_id must produce unavailability, never
    silently substitute the latest run for the same horizon/universe —
    proven by constructing a scenario where a valid latest run DOES
    exist, yet the explicit invalid request must still fail closed."""
    _insert_run(isolated_db, summary={"buy_hit_rate_pct": 77.0})  # a genuine latest run exists
    resolved = ve.resolve_eligible_run_id(999999, "medium", "nifty100")
    assert resolved is None  # must NOT silently resolve to the genuine latest run


def test_omitted_run_id_retains_backward_compatible_latest_behavior(isolated_db):
    _insert_run(isolated_db, summary={"buy_hit_rate_pct": 40.0}, run_at="2026-08-01T00:00:00")
    run_b = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 60.0}, run_at="2026-08-02T00:00:00")
    resolved = ve.resolve_eligible_run_id(None, "medium", "nifty100")
    assert resolved == run_b


# ── C. Both endpoints share the identical eligibility definition ──────────

def test_aggregate_and_per_stock_share_identical_eligibility_definition(isolated_db):
    """get_latest_results()'s own WHERE clause and resolve_eligible_run_id()
    must agree on which run is "latest displayable" — proven by
    constructing a run with no summary (ineligible) sitting AFTER a run
    with a summary (eligible), and confirming both paths skip the
    ineligible row identically."""
    eligible_run = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 50.0}, run_at="2026-08-01T00:00:00")
    _insert_run(isolated_db, summary=None, run_at="2026-08-02T00:00:00")  # ineligible, inserted LATER

    aggregate_result = ve.get_latest_results(horizon="medium", universe="nifty100")
    resolved_via_helper = ve.resolve_eligible_run_id(None, "medium", "nifty100")

    assert aggregate_result["run_id"] == eligible_run
    assert resolved_via_helper == eligible_run


def test_get_per_stock_results_direct_no_run_id_path_applies_same_eligibility_rule(isolated_db):
    """V-SNAP1C — get_per_stock_results(run_id=None)'s internal 'resolve
    latest' branch (the exact path get_single_stock_accuracy calls
    directly, with no run_id to pin against) must skip an ineligible
    (summary-less) latest row exactly like get_latest_results() and
    resolve_eligible_run_id() do — proven by constructing a summary-less
    row AFTER an eligible one and confirming get_per_stock_results()
    returns signals from the eligible run, not an empty/wrong result from
    treating the ineligible row as latest."""
    eligible_run = _insert_run(isolated_db, summary={"buy_hit_rate_pct": 50.0}, run_at="2026-08-01T00:00:00")
    _insert_run(isolated_db, summary=None, run_at="2026-08-02T00:00:00")  # ineligible, inserted LATER
    _insert_signal(isolated_db, eligible_run, "ELIGIBLE", "medium", "BUY", correct=1)

    rows = ve.get_per_stock_results(horizon="medium", universe="nifty100")  # no run_id — internal resolution
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"ELIGIBLE"}


def test_get_per_stock_results_no_run_id_returns_empty_when_only_ineligible_run_exists(isolated_db):
    """When the only run for a horizon/universe is ineligible (no
    summary), the direct no-run_id path must return [] — exactly like
    get_latest_results() reports unavailable and resolve_eligible_run_id()
    returns None for the same scenario — not silently treat the
    ineligible row as displayable."""
    _insert_run(isolated_db, summary=None)
    rows = ve.get_per_stock_results(horizon="medium", universe="nifty100")
    assert rows == []


# ── D. SQL safety and bounded queries ───────────────────────────────────────

def test_resolve_eligible_run_id_is_parameterized_against_injection(isolated_db):
    """A hostile horizon/universe string must never be interpolated
    unsafely — proven by passing a value containing SQL metacharacters
    and confirming it simply fails to match (parameterized behavior),
    not that it raises or alters query structure."""
    run_id = _insert_run(isolated_db, summary={})
    resolved = ve.resolve_eligible_run_id(run_id, "medium", "nifty100'; DROP TABLE val_runs; --")
    assert resolved is None
    # Table must still exist and be queryable — proves no injection occurred.
    still_resolves = ve.resolve_eligible_run_id(run_id, "medium", "nifty100")
    assert still_resolves == run_id


# ── E. get_track_record_summary() compatibility ────────────────────────────

def test_get_track_record_summary_remains_compatible_with_additive_run_id(isolated_db):
    _insert_run(isolated_db, horizon="short", universe="nifty100",
                summary={"buy_hit_rate_pct": 55.0, "beat_benchmark_pct": 60.0, "buy_signals": 10, "run_at": "2026-08-01T00:00:00"})
    out = ve.get_track_record_summary("IN", "short")
    assert any(r["universe"] == "nifty100" for r in out)
    entry = next(r for r in out if r["universe"] == "nifty100")
    assert entry["buy_hit_rate_pct"] == 55.0
    # get_track_record_summary's own output shape is untouched by the
    # additive run_id field on get_latest_results() — no new key leaks
    # into it unless explicitly added.
    assert "run_id" not in entry
