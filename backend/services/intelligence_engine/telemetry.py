"""
Intelligence Engine shadow-run telemetry — persistence only. This module
never touches paper_trades, daily_picks' cache file, or any table the
published product reads from; it owns exactly one additive table,
intelligence_engine_shadow_runs, created idempotently in postgres_store.py.
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
                instrument_type_counts, sample_exclusions, source_commit, generation_job_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
                      instrument_type_counts, sample_exclusions, source_commit, generation_job_id
               FROM intelligence_engine_shadow_runs
               WHERE market = %s
               ORDER BY run_at DESC
               LIMIT 1""",
            (market,),
        ).fetchone()
    if row is None:
        return None
    (mkt, run_at, version, raw, evaluated, passed, excluded,
     excl_by_reason, type_counts, samples, commit, job_id) = row
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
    }
