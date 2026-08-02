"""
Market Leadership shadow-run telemetry — persistence only, mirrors
intelligence_engine/telemetry.py's proven pattern. Owns exactly one
additive table (`ml_shadow_runs`), created idempotently here. Postgres-only
(matches the existing shadow-telemetry precedent — this is
operational/production telemetry, not a local-dev-required path).

Callers (orchestration.py) are responsible for catching exceptions — this
module does not swallow errors, so a genuine bug is still visible in logs
without ever being allowed to break the calculation path that calls it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def _init_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_shadow_runs (
            id SERIAL PRIMARY KEY,
            market TEXT NOT NULL,
            run_at TIMESTAMPTZ NOT NULL,
            universe_id TEXT NOT NULL,
            universe_version TEXT NOT NULL,
            methodology_versions JSONB NOT NULL,
            symbols_evaluated INT NOT NULL,
            symbols_scored INT NOT NULL,
            symbols_insufficient_data INT NOT NULL,
            groups_evaluated INT NOT NULL,
            breadth_state TEXT,
            duration_ms INT,
            source_commit TEXT,
            errors JSONB
        )"""
    )


def persist_shadow_run(
    market: str,
    universe_id: str,
    universe_version: str,
    methodology_versions: dict,
    symbols_evaluated: int,
    symbols_scored: int,
    symbols_insufficient_data: int,
    groups_evaluated: int,
    breadth_state: str | None,
    duration_ms: int | None,
    source_commit: str | None,
    errors: list | None = None,
) -> None:
    with _conn() as conn:
        _init_table(conn)
        conn.execute(
            """INSERT INTO ml_shadow_runs
               (market, run_at, universe_id, universe_version, methodology_versions,
                symbols_evaluated, symbols_scored, symbols_insufficient_data,
                groups_evaluated, breadth_state, duration_ms, source_commit, errors)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                market, datetime.now(timezone.utc), universe_id, universe_version,
                json.dumps(methodology_versions), symbols_evaluated, symbols_scored,
                symbols_insufficient_data, groups_evaluated, breadth_state, duration_ms,
                source_commit, json.dumps(errors) if errors is not None else None,
            ),
        )


def get_latest_shadow_run(market: str) -> dict | None:
    with _conn() as conn:
        _init_table(conn)
        row = conn.execute(
            """SELECT market, run_at, universe_id, universe_version, methodology_versions,
                      symbols_evaluated, symbols_scored, symbols_insufficient_data,
                      groups_evaluated, breadth_state, duration_ms, source_commit, errors
               FROM ml_shadow_runs WHERE market = %s ORDER BY run_at DESC LIMIT 1""",
            (market,),
        ).fetchone()
    if row is None:
        return None
    (mkt, run_at, uid, uver, methodology_versions, sym_eval, sym_scored, sym_insuff,
     groups_eval, breadth_state, duration_ms, commit, errors) = row
    return {
        "market": mkt,
        "run_at": run_at.isoformat() if run_at else None,
        "universe_id": uid,
        "universe_version": uver,
        "methodology_versions": methodology_versions,
        "symbols_evaluated": sym_eval,
        "symbols_scored": sym_scored,
        "symbols_insufficient_data": sym_insuff,
        "groups_evaluated": groups_eval,
        "breadth_state": breadth_state,
        "duration_ms": duration_ms,
        "source_commit": commit,
        "errors": errors,
    }
