"""
Intelligence Engine shadow-run telemetry — persistence only. This module
never touches paper_trades, daily_picks' cache file, or any table the
published product reads from; it owns exactly one additive table,
intelligence_engine_shadow_runs, created idempotently in postgres_store.py.

Phase 3B adds Tradability/Liquidity/Confidence fields — all new, optional
(nullable) columns. Phase 3A callers/rows are entirely unaffected: the new
parameters all default to None/empty so a caller that hasn't been updated
(there are none left in this codebase, but the principle matters for any
future one) would still work, and old already-persisted rows simply have
NULL in the new columns rather than needing a backfill.
"""
import json
import os
from datetime import datetime, timezone


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def persist_shadow_run(
    market: str,
    universe_version: str,
    raw_count: int,
    evaluated_count: int,
    passed_count: int,
    excluded_count: int,
    excluded_counts_by_reason: dict,
    instrument_type_counts: dict,
    sample_exclusions: list,
    source_commit: str | None,
    generation_job_id: str | None,
    # ── Phase 3B additions — all optional, all default to "not evaluated" ──
    tradability_passed: int | None = None,
    tradability_failed: int | None = None,
    liquidity_passed: int | None = None,
    liquidity_failed: int | None = None,
    data_confidence_average: float | None = None,
    top_failure_reasons: dict | None = None,
    sample_liquidity_rejections: list | None = None,
) -> None:
    """Best-effort insert. Callers (shadow_run.py) are responsible for
    catching exceptions — this function does not swallow errors itself,
    so a genuine bug here is still visible in logs, just never allowed to
    propagate into the real Daily Picks generation path that calls it."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO intelligence_engine_shadow_runs
               (market, run_at, universe_version, raw_count, evaluated_count,
                passed_count, excluded_count, excluded_counts_by_reason,
                instrument_type_counts, sample_exclusions, source_commit, generation_job_id,
                tradability_passed, tradability_failed, liquidity_passed, liquidity_failed,
                data_confidence_average, top_failure_reasons, sample_liquidity_rejections)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                market,
                datetime.now(timezone.utc),
                universe_version,
                raw_count,
                evaluated_count,
                passed_count,
                excluded_count,
                json.dumps(excluded_counts_by_reason),
                json.dumps(instrument_type_counts),
                json.dumps(sample_exclusions),
                source_commit,
                generation_job_id,
                tradability_passed,
                tradability_failed,
                liquidity_passed,
                liquidity_failed,
                data_confidence_average,
                json.dumps(top_failure_reasons) if top_failure_reasons is not None else None,
                json.dumps(sample_liquidity_rejections) if sample_liquidity_rejections is not None else None,
            ),
        )


def get_latest_shadow_run(market: str) -> dict | None:
    """Read-only lookup for the inspection endpoint. Returns None if no
    shadow run has ever completed for this market (e.g. the flag has
    never been enabled) — callers must handle that explicitly, not
    fabricate a placeholder result."""
    with _conn() as conn:
        row = conn.execute(
            """SELECT market, run_at, universe_version, raw_count, evaluated_count,
                      passed_count, excluded_count, excluded_counts_by_reason,
                      instrument_type_counts, sample_exclusions, source_commit, generation_job_id,
                      tradability_passed, tradability_failed, liquidity_passed, liquidity_failed,
                      data_confidence_average, top_failure_reasons, sample_liquidity_rejections
               FROM intelligence_engine_shadow_runs
               WHERE market = %s
               ORDER BY run_at DESC
               LIMIT 1""",
            (market,),
        ).fetchone()
    if row is None:
        return None
    (mkt, run_at, version, raw, evaluated, passed, excluded,
     excl_by_reason, type_counts, samples, commit, job_id,
     tradability_passed, tradability_failed, liquidity_passed, liquidity_failed,
     data_confidence_average, top_failure_reasons, sample_liquidity_rejections) = row
    return {
        "market": mkt,
        "run_at": run_at.isoformat() if run_at else None,
        "universe_version": version,
        "raw_count": raw,
        "evaluated_count": evaluated,
        "passed_count": passed,
        "excluded_count": excluded,
        "excluded_counts_by_reason": excl_by_reason,
        "instrument_type_counts": type_counts,
        "sample_exclusions": samples,
        "source_commit": commit,
        "generation_job_id": job_id,
        "tradability": {
            "available": tradability_passed is not None or tradability_failed is not None,
            "passed": tradability_passed,
            "failed": tradability_failed,
        },
        "liquidity": {
            "available": liquidity_passed is not None or liquidity_failed is not None,
            "passed": liquidity_passed,
            "failed": liquidity_failed,
            "sample_rejections": sample_liquidity_rejections,
        },
        "data_confidence": {
            "available": data_confidence_average is not None,
            "average": data_confidence_average,
        },
        "top_failure_reasons": top_failure_reasons,
    }
