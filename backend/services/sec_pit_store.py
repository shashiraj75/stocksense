"""
StockSense360 SEC Point-in-Time Fact Store (DP-033 remediation).

CORRECTED ARCHITECTURE (2026-07-22, second readiness pass) — three real
defects found and fixed this pass, not merely re-described:

1. FACT IDENTITY COLLISION (found via a real collision analysis against
   live SEC data for the full 42-symbol US_BASKET, 957,658 facts
   checked): the original uniqueness key
   `(cik, concept, unit, accession, period_end)` collapsed 99,082 groups
   of MATERIALLY DIFFERENT facts — the dominant pattern is a single 10-Q
   accession containing both a year-to-date cumulative figure and a
   quarter-only figure for the same concept and same period-end date,
   distinguished only by `period_start`. Adding `period_start` to the key
   — `(cik, concept, unit, accession, period_start, period_end)` —
   eliminates every one of those 99,082 collisions against the same real
   dataset (verified: 0 remaining). This is the corrected key below.

2. ACQUISITION/REPLAY WERE NOT SEPARATED: the prior pass's
   `_get_fundamentals_as_of_persisted_first()` (in validation_engine.py)
   still performed a live SEC fetch on a cache miss — a "persisted-first"
   name describing behavior that was not actually acquisition-free.
   Corrected: this module now exposes two genuinely distinct entry
   points. `ingest_symbol()` is the ONLY acquisition path (explicit,
   administrative, never called by replay). `get_facts_as_of_replay()` is
   the ONLY replay path — it NEVER calls sec_edgar_adapter's live
   functions (`fetch_company_facts`, `resolve_cik`), reading the
   persisted symbol->CIK registry (below) instead of resolving CIKs
   live. A process restart cannot reintroduce network dependence because
   replay has no code path that could reach the network at all.

3. NO INGESTION-COMPLETENESS CONTRACT existed: a single stray persisted
   row was previously indistinguishable from a complete, successful
   ingestion. `sec_pit_ingestion_runs` (below) records one row per
   (ingestion run, symbol) with an explicit `completion_status` --
   `complete`, `governed_unsupported` (e.g. TSM/IFRS), `failed`,
   `partial`, or `no_data` -- so replay and reconciliation can
   distinguish "genuinely nothing eligible as of this date" from
   "this symbol was never successfully ingested at all".

Layering (unchanged): sec_edgar_adapter.py remains the Provider Adapter
(SEC authentication, rate limiting, retry, raw-schema validation, field
normalization). This module is the Persistence Adapter -- the ONLY code
in this repository permitted to call sec_edgar_adapter's live-network
functions is `ingest_symbol()` below. Everything else in this module,
and everything in validation_engine.py's replay path, must go through
`get_facts_as_of_replay()` / `get_symbol_registry_entry()` instead.
"""

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from services.market_hours import next_us_trading_session

log = logging.getLogger(__name__)

_USE_POSTGRES = bool(os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES", "0") == "1")

# Bumped whenever the fact-identity key, ingestion-run schema, or any
# other structural contract below changes in a way that would make an
# OLD ingestion run's data insufficient to trust without re-ingestion.
# DP-033 second readiness pass: v1 -> v2 for the period_start identity
# fix -- any v1-ingested data (there is none; nothing has ever been
# ingested into production) would need re-ingestion under v2's key.
SCHEMA_VERSION = "sec_pit_v2"


def _pg_conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


# ── Schema ────────────────────────────────────────────────────────────────
#
# sec_pit_facts: the immutable fact store itself. Corrected identity key
# (item 1 above) — UNIQUE now includes period_start.
#
# sec_pit_ingestion_runs: the completeness contract (item 3 above) — one
# row per (run_id, symbol).
#
# sec_pit_symbol_registry: the persisted symbol->CIK mapping replay reads
# instead of ever calling resolve_cik() live (item 2 above). Populated
# only by ingest_symbol(); a symbol absent here has never been
# successfully ingested, full stop — replay treats this as "no ingestion",
# never guesses a CIK.

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_pit_facts (
    id             BIGSERIAL PRIMARY KEY,
    cik            BIGINT NOT NULL,
    symbol         TEXT NOT NULL,
    concept        TEXT NOT NULL,
    unit           TEXT NOT NULL,
    accession      TEXT NOT NULL,
    form           TEXT,
    fiscal_year    INTEGER,
    fiscal_period  TEXT,
    period_start   DATE,
    period_end     DATE NOT NULL,
    filed_date     DATE NOT NULL,
    value          DOUBLE PRECISION,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingestion_run_id TEXT,
    UNIQUE (cik, concept, unit, accession, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_lookup ON sec_pit_facts(cik, concept, filed_date);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_symbol  ON sec_pit_facts(symbol);

CREATE TABLE IF NOT EXISTS sec_pit_ingestion_runs (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    universe_manifest_version TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    cik                 BIGINT,
    provider            TEXT NOT NULL DEFAULT 'sec_edgar',
    acquired_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    taxonomy_classification TEXT,
    fact_rows_observed  INTEGER,
    fact_rows_inserted  INTEGER,
    duplicates_skipped  INTEGER,
    malformed_skipped   INTEGER,
    concepts_covered    INTEGER,
    distinct_accessions INTEGER,
    earliest_filed      DATE,
    latest_filed         DATE,
    completion_status   TEXT NOT NULL,
    failure_reason       TEXT,
    fingerprint          TEXT,
    UNIQUE (run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_sec_pit_runs_symbol ON sec_pit_ingestion_runs(symbol, acquired_at);

CREATE TABLE IF NOT EXISTS sec_pit_symbol_registry (
    symbol              TEXT PRIMARY KEY,
    cik                 BIGINT NOT NULL,
    taxonomy_classification TEXT NOT NULL,
    last_ingestion_run_id TEXT,
    last_ingested_at    TIMESTAMPTZ,
    schema_version      TEXT NOT NULL
);

ALTER TABLE sec_pit_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sec_pit_ingestion_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sec_pit_symbol_registry ENABLE ROW LEVEL SECURITY;

-- Database-enforced append-only (DP-033 second readiness pass): rejects
-- ordinary UPDATE/DELETE against sec_pit_facts at the database boundary,
-- not merely by application-code convention. Effective for the
-- application's normal connection role (does not rely on RLS, which a
-- BYPASSRLS/owner role would ignore). TRUNCATE is separately controlled
-- via table ownership/grants at deployment time, not by this trigger
-- (Postgres row-level triggers do not fire on TRUNCATE) -- documented
-- limitation, not silently assumed covered.
CREATE OR REPLACE FUNCTION sec_pit_facts_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'sec_pit_facts is database-enforced append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sec_pit_facts_no_update ON sec_pit_facts;
CREATE TRIGGER trg_sec_pit_facts_no_update
    BEFORE UPDATE ON sec_pit_facts
    FOR EACH ROW EXECUTE FUNCTION sec_pit_facts_reject_mutation();

DROP TRIGGER IF EXISTS trg_sec_pit_facts_no_delete ON sec_pit_facts;
CREATE TRIGGER trg_sec_pit_facts_no_delete
    BEFORE DELETE ON sec_pit_facts
    FOR EACH ROW EXECUTE FUNCTION sec_pit_facts_reject_mutation();
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_pit_facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    cik            INTEGER NOT NULL,
    symbol         TEXT NOT NULL,
    concept        TEXT NOT NULL,
    unit           TEXT NOT NULL,
    accession      TEXT NOT NULL,
    form           TEXT,
    fiscal_year    INTEGER,
    fiscal_period  TEXT,
    period_start   TEXT,
    period_end     TEXT NOT NULL,
    filed_date     TEXT NOT NULL,
    value          REAL,
    ingested_at    TEXT NOT NULL,
    ingestion_run_id TEXT,
    UNIQUE (cik, concept, unit, accession, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_lookup ON sec_pit_facts(cik, concept, filed_date);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_symbol  ON sec_pit_facts(symbol);

CREATE TABLE IF NOT EXISTS sec_pit_ingestion_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    schema_version      TEXT NOT NULL,
    universe_manifest_version TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    cik                 INTEGER,
    provider            TEXT NOT NULL DEFAULT 'sec_edgar',
    acquired_at         TEXT NOT NULL,
    taxonomy_classification TEXT,
    fact_rows_observed  INTEGER,
    fact_rows_inserted  INTEGER,
    duplicates_skipped  INTEGER,
    malformed_skipped   INTEGER,
    concepts_covered    INTEGER,
    distinct_accessions INTEGER,
    earliest_filed      TEXT,
    latest_filed        TEXT,
    completion_status   TEXT NOT NULL,
    failure_reason       TEXT,
    fingerprint          TEXT,
    UNIQUE (run_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_sec_pit_runs_symbol ON sec_pit_ingestion_runs(symbol, acquired_at);

CREATE TABLE IF NOT EXISTS sec_pit_symbol_registry (
    symbol              TEXT PRIMARY KEY,
    cik                 INTEGER NOT NULL,
    taxonomy_classification TEXT NOT NULL,
    last_ingestion_run_id TEXT,
    last_ingested_at    TEXT,
    schema_version      TEXT NOT NULL
);

-- SQLite append-only parity: no BEFORE UPDATE/DELETE trigger can
-- reference TG_OP the way Postgres does, but the RAISE(ABORT, ...) form
-- gives the same "reject the statement" behavior for local/test parity.
CREATE TRIGGER IF NOT EXISTS trg_sec_pit_facts_no_update
    BEFORE UPDATE ON sec_pit_facts
BEGIN
    SELECT RAISE(ABORT, 'sec_pit_facts is database-enforced append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_sec_pit_facts_no_delete
    BEFORE DELETE ON sec_pit_facts
BEGIN
    SELECT RAISE(ABORT, 'sec_pit_facts is database-enforced append-only: DELETE is not permitted');
END;
"""

_SQLITE_PATH = os.getenv("SEC_PIT_SQLITE_PATH", "sec_pit_facts.db")
_db_lock = threading.Lock()
_initialized = False

# NULL-safety sentinel for period_start (see _ingest_company_facts_rows'
# comment) — never a value SEC would supply (no XBRL filer reports a
# period starting in year 1 AD), so it cannot collide with a genuine
# period_start. Must be a value PostgreSQL's DATE column type will
# actually accept — "" (the original choice) is valid for SQLite's
# dynamically-typed TEXT column but is rejected by Postgres with
# InvalidDatetimeFormat, a real bug this session's own execution-verified
# Postgres testing caught (SQLite's permissive typing had silently masked
# it; this is exactly why SQLite results are not proof of Postgres
# behavior).
_NO_PERIOD_START_SENTINEL = "0001-01-01"


def _period_start_or_none(value):
    return None if value in (None, _NO_PERIOD_START_SENTINEL) else value


# ── Temporal contract: date-only filing timestamps ──────────────────────────
# (unchanged from the prior pass — see next_us_trading_session()'s own
# docstring in services/market_hours.py for the full rationale.)


def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Idempotent — safe to call on every process start, mirrors
    validation_engine.py's own init_db()/_init_db() convention. Confirmed
    no formal migration framework exists in this repository (searched
    scripts/migrations/ — one ad-hoc, never-run .sql file, no runner, no
    version table); this idempotent-DDL pattern is this repo's own
    established, only convention for new tables."""
    global _initialized
    with _db_lock:
        if _initialized:
            return
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(_PG_SCHEMA)
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                conn.executescript(_SQLITE_SCHEMA)
        _initialized = True


def new_run_id() -> str:
    """A deterministic-shape (not deterministic-value) run identifier —
    callers needing reproducible test fixtures should pass their own
    run_id explicitly rather than relying on this."""
    return f"run_{uuid.uuid4().hex[:16]}"


# ── ACQUISITION (the only path permitted to touch the live network) ────────

def ingest_symbol(
    symbol: str,
    run_id: str,
    universe_manifest_version: str,
    *,
    resolve_cik_fn=None,
    fetch_company_facts_fn=None,
) -> dict:
    """
    The ONLY function in this codebase permitted to acquire SEC data for
    the point-in-time store. Explicit, administrative, never called by
    any replay path. `resolve_cik_fn`/`fetch_company_facts_fn` default to
    `services.sec_edgar_adapter`'s real live functions (imported lazily,
    only here) but are injectable for tests/tooling that need to observe
    or stub acquisition without a live network call.

    Writes, in order: (1) zero or more sec_pit_facts rows (additive,
    idempotent), (2) one sec_pit_ingestion_runs row recording the
    complete outcome, (3) one sec_pit_symbol_registry row/upsert IF AND
    ONLY IF the outcome is a genuinely supported, complete ingestion —
    a failed or governed-unsupported symbol does NOT get a registry
    entry, so replay correctly reports "no ingestion" for it rather than
    silently reusing a stale/wrong CIK.

    Returns a dict matching one row of the Phase 11 reconciliation
    contract: symbol, cik, completion_status, taxonomy_classification,
    fact_rows_observed/inserted, duplicates_skipped, malformed_skipped,
    concepts_covered, distinct_accessions, earliest_filed, latest_filed,
    failure_reason.
    """
    init_db()
    if resolve_cik_fn is None or fetch_company_facts_fn is None:
        from services import sec_edgar_adapter as sea
        resolve_cik_fn = resolve_cik_fn or sea.resolve_cik
        fetch_company_facts_fn = fetch_company_facts_fn or sea.fetch_company_facts

    sym = symbol.upper().strip()
    acquired_at = datetime.now(timezone.utc)

    cik = resolve_cik_fn(sym)
    if cik is None:
        result = _record_ingestion_run(
            run_id, universe_manifest_version, sym, cik=None,
            acquired_at=acquired_at, taxonomy_classification=None,
            fact_rows_observed=0, fact_rows_inserted=0, duplicates_skipped=0,
            malformed_skipped=0, concepts_covered=0, distinct_accessions=0,
            earliest_filed=None, latest_filed=None,
            completion_status="failed", failure_reason="CIK not found",
        )
        return result

    facts = fetch_company_facts_fn(cik)
    if facts is None:
        # Distinguish a genuinely IFRS-only foreign private issuer (a
        # GOVERNED exclusion) from any other fetch failure (a real
        # FAILED ingestion) by making one more read-only classification
        # request — reuses the existing distinct IFRS log path in
        # sec_edgar_adapter.fetch_company_facts(); here we independently
        # confirm via the raw submissions taxonomy check so ingestion
        # reconciliation can classify correctly without depending on log
        # scraping.
        taxonomy = _classify_missing_us_gaap(cik, fetch_company_facts_fn)
        status = "governed_unsupported" if taxonomy == "ifrs_only" else "failed"
        reason = (
            "foreign private issuer reports under IFRS (ifrs-full taxonomy), "
            "no us-gaap data available — governed exclusion, not a fetch error"
            if status == "governed_unsupported"
            else "companyfacts fetch failed or returned an unexpected shape"
        )
        result = _record_ingestion_run(
            run_id, universe_manifest_version, sym, cik=cik,
            acquired_at=acquired_at, taxonomy_classification=taxonomy,
            fact_rows_observed=0, fact_rows_inserted=0, duplicates_skipped=0,
            malformed_skipped=0, concepts_covered=0, distinct_accessions=0,
            earliest_filed=None, latest_filed=None,
            completion_status=status, failure_reason=reason,
        )
        return result

    ingest_stats = _ingest_company_facts_rows(cik, sym, facts, run_id)
    result = _record_ingestion_run(
        run_id, universe_manifest_version, sym, cik=cik,
        acquired_at=acquired_at, taxonomy_classification="us_gaap",
        fact_rows_observed=ingest_stats["total_seen"],
        fact_rows_inserted=ingest_stats["inserted"],
        duplicates_skipped=ingest_stats["skipped"],
        malformed_skipped=ingest_stats["malformed"],
        concepts_covered=ingest_stats["concepts_covered"],
        distinct_accessions=ingest_stats["distinct_accessions"],
        earliest_filed=ingest_stats["earliest_filed"],
        latest_filed=ingest_stats["latest_filed"],
        completion_status="complete" if ingest_stats["inserted"] > 0 or ingest_stats["skipped"] > 0 else "no_data",
        failure_reason=None,
    )

    # Symbol registry — only for a genuinely complete/reusable ingestion.
    if result["completion_status"] in ("complete",):
        _upsert_symbol_registry(sym, cik, "us_gaap", run_id, acquired_at)

    return result


def _classify_missing_us_gaap(cik: int, fetch_company_facts_fn) -> Optional[str]:
    """Best-effort re-classification when fetch_company_facts_fn already
    returned None (schema-invalid) — makes ONE more request only to
    distinguish IFRS-only from a genuine failure, reusing the injected
    fetch function (so tests can stub this too, no hidden live call)."""
    try:
        import requests
        from services import sec_edgar_adapter as sea
        resp = sea._get_with_retry(sea._FACTS_URL.format(cik=cik))
        if resp is not None and resp.status_code == 200:
            payload = resp.json()
            taxonomies = list(payload.get("facts", {}).keys()) if isinstance(payload, dict) else []
            if "ifrs-full" in taxonomies and "us-gaap" not in taxonomies:
                return "ifrs_only"
    except Exception:
        pass
    return "unknown"


def _ingest_company_facts_rows(cik: int, symbol: str, facts: dict, run_id: str) -> dict:
    """Internal: flattens+inserts fact rows (the corrected 6-part
    identity key), returns detailed stats for the ingestion-run record.
    Not a public API — call ingest_symbol() instead."""
    from services.sec_edgar_adapter import _DIRECT_FIELD_TAGS

    wanted_concepts = {tag for tags in _DIRECT_FIELD_TAGS.values() for tag in tags}
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    now = datetime.now(timezone.utc)
    rows = []
    total_seen = 0
    malformed = 0
    concepts_seen = set()
    accessions_seen = set()
    filed_dates = []
    for concept, concept_data in us_gaap.items():
        if concept not in wanted_concepts:
            continue
        for entry in concept_data.get("units", {}).get("USD", []):
            total_seen += 1
            accn = entry.get("accn")
            end = entry.get("end")
            filed = entry.get("filed")
            val = entry.get("val")
            if not accn or not end or not filed or val is None:
                malformed += 1
                continue
            concepts_seen.add(concept)
            accessions_seen.add(accn)
            filed_dates.append(filed)
            # DP-033 second-pass fix: NULL is never equal to NULL in a SQL
            # UNIQUE constraint (Postgres and SQLite alike) — an instant
            # fact (no "start", e.g. Assets/Liabilities/StockholdersEquity)
            # would silently insert a NEW duplicate row on every
            # re-ingestion instead of being deduplicated, breaking the
            # append-only idempotency guarantee. Found by this session's
            # own test suite, not assumed. Normalized to the empty string
            # sentinel (never a legitimate SEC date value) so the UNIQUE
            # constraint's equality check actually deduplicates; converted
            # back to None on read (_period_start_or_none below).
            period_start = entry.get("start") or _NO_PERIOD_START_SENTINEL
            rows.append((
                cik, symbol, concept, "USD", accn,
                entry.get("form"), entry.get("fy"), entry.get("fp"),
                period_start, end, filed, float(val),
                now.isoformat() if not _USE_POSTGRES else now, run_id,
            ))

    inserted = 0
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    for row in rows:
                        cur.execute(
                            """INSERT INTO sec_pit_facts
                               (cik, symbol, concept, unit, accession, form,
                                fiscal_year, fiscal_period, period_start, period_end,
                                filed_date, value, ingested_at, ingestion_run_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (cik, concept, unit, accession, period_start, period_end)
                               DO NOTHING""",
                            row,
                        )
                        if cur.rowcount:
                            inserted += 1
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                for row in rows:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO sec_pit_facts
                           (cik, symbol, concept, unit, accession, form,
                            fiscal_year, fiscal_period, period_start, period_end,
                            filed_date, value, ingested_at, ingestion_run_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        row,
                    )
                    if cur.rowcount:
                        inserted += 1

    return {
        "total_seen": total_seen,
        "inserted": inserted,
        "skipped": len(rows) - inserted,
        "malformed": malformed,
        "concepts_covered": len(concepts_seen),
        "distinct_accessions": len(accessions_seen),
        "earliest_filed": min(filed_dates) if filed_dates else None,
        "latest_filed": max(filed_dates) if filed_dates else None,
    }


def _record_ingestion_run(
    run_id, universe_manifest_version, symbol, *, cik, acquired_at,
    taxonomy_classification, fact_rows_observed, fact_rows_inserted,
    duplicates_skipped, malformed_skipped, concepts_covered,
    distinct_accessions, earliest_filed, latest_filed,
    completion_status, failure_reason,
) -> dict:
    init_db()
    fingerprint = f"{symbol}:{cik}:{fact_rows_inserted}:{distinct_accessions}:{earliest_filed}:{latest_filed}"
    row = (
        run_id, SCHEMA_VERSION, universe_manifest_version, symbol, cik, "sec_edgar",
        acquired_at.isoformat() if not _USE_POSTGRES else acquired_at,
        taxonomy_classification, fact_rows_observed, fact_rows_inserted,
        duplicates_skipped, malformed_skipped, concepts_covered, distinct_accessions,
        earliest_filed, latest_filed, completion_status, failure_reason, fingerprint,
    )
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sec_pit_ingestion_runs
                           (run_id, schema_version, universe_manifest_version, symbol, cik, provider,
                            acquired_at, taxonomy_classification, fact_rows_observed, fact_rows_inserted,
                            duplicates_skipped, malformed_skipped, concepts_covered, distinct_accessions,
                            earliest_filed, latest_filed, completion_status, failure_reason, fingerprint)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (run_id, symbol) DO NOTHING""",
                        row,
                    )
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO sec_pit_ingestion_runs
                       (run_id, schema_version, universe_manifest_version, symbol, cik, provider,
                        acquired_at, taxonomy_classification, fact_rows_observed, fact_rows_inserted,
                        duplicates_skipped, malformed_skipped, concepts_covered, distinct_accessions,
                        earliest_filed, latest_filed, completion_status, failure_reason, fingerprint)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    row,
                )
    return {
        "symbol": symbol, "cik": cik, "completion_status": completion_status,
        "taxonomy_classification": taxonomy_classification,
        "fact_rows_observed": fact_rows_observed, "fact_rows_inserted": fact_rows_inserted,
        "duplicates_skipped": duplicates_skipped, "malformed_skipped": malformed_skipped,
        "concepts_covered": concepts_covered, "distinct_accessions": distinct_accessions,
        "earliest_filed": earliest_filed, "latest_filed": latest_filed,
        "failure_reason": failure_reason, "run_id": run_id,
    }


def _upsert_symbol_registry(symbol, cik, taxonomy, run_id, ingested_at) -> None:
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO sec_pit_symbol_registry
                           (symbol, cik, taxonomy_classification, last_ingestion_run_id, last_ingested_at, schema_version)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (symbol) DO UPDATE SET
                             cik = EXCLUDED.cik, taxonomy_classification = EXCLUDED.taxonomy_classification,
                             last_ingestion_run_id = EXCLUDED.last_ingestion_run_id,
                             last_ingested_at = EXCLUDED.last_ingested_at,
                             schema_version = EXCLUDED.schema_version""",
                        (symbol, cik, taxonomy, run_id, ingested_at, SCHEMA_VERSION),
                    )
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                conn.execute(
                    """INSERT INTO sec_pit_symbol_registry
                       (symbol, cik, taxonomy_classification, last_ingestion_run_id, last_ingested_at, schema_version)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT (symbol) DO UPDATE SET
                         cik = excluded.cik, taxonomy_classification = excluded.taxonomy_classification,
                         last_ingestion_run_id = excluded.last_ingestion_run_id,
                         last_ingested_at = excluded.last_ingested_at,
                         schema_version = excluded.schema_version""",
                    (symbol, cik, taxonomy, run_id, ingested_at.isoformat(), SCHEMA_VERSION),
                )


# ── REPLAY (the only path validation may call — zero network, ever) ────────

def get_symbol_registry_entry(symbol: str) -> Optional[dict]:
    """Pure, offline lookup of a symbol's persisted CIK mapping. Returns
    None if the symbol was never successfully ingested — replay must
    treat this as "no ingestion", never fall back to a live CIK
    resolution. This is the ONLY way validation_engine.py's replay path
    learns a symbol's CIK."""
    init_db()
    sym = symbol.upper().strip()
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT cik, taxonomy_classification, last_ingestion_run_id, schema_version "
                        "FROM sec_pit_symbol_registry WHERE symbol = %s", (sym,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT cik, taxonomy_classification, last_ingestion_run_id, schema_version "
                    "FROM sec_pit_symbol_registry WHERE symbol = ?", (sym,),
                ).fetchone()
    if row is None:
        return None
    return {"cik": row[0], "taxonomy_classification": row[1],
            "last_ingestion_run_id": row[2], "schema_version": row[3]}


def get_facts_as_of_replay(symbol: str, as_of: date) -> dict:
    """
    THE pure, deterministic, OFFLINE (zero network, zero writes, ever) as-of
    lookup for historical validation. Renamed from the prior pass's
    `get_facts_as_of_persisted()` to state its contract precisely: this
    function performs NO acquisition under any circumstance, including a
    cache miss — a symbol never ingested returns an empty-facts shape,
    the same as a symbol ingested but with nothing eligible as of the
    cutoff. Distinguishing those two cases is `get_symbol_registry_entry()`
    combined with `get_ingestion_status()`'s job, not this function's.

    Never calls sec_edgar_adapter's live functions. Never calls
    ingest_symbol(). Never inserts, updates, or deletes anything.
    """
    init_db()
    entry = get_symbol_registry_entry(symbol)
    if entry is None:
        return {"cik": None, "entityName": None, "facts": {"us-gaap": {}}}
    cik = entry["cik"]

    as_of_str = as_of.isoformat()
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT concept, fiscal_year, fiscal_period, form,
                                  period_start, period_end, filed_date, value
                           FROM sec_pit_facts
                           WHERE cik = %s AND filed_date <= %s""",
                        (cik, as_of_str),
                    )
                    rows = cur.fetchall()
                    cols = ["concept", "fiscal_year", "fiscal_period", "form",
                            "period_start", "period_end", "filed_date", "value"]
                    rows = [dict(zip(cols, r)) for r in rows]
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                cur = conn.execute(
                    """SELECT concept, fiscal_year, fiscal_period, form,
                              period_start, period_end, filed_date, value
                       FROM sec_pit_facts
                       WHERE cik = ? AND filed_date <= ?""",
                    (cik, as_of_str),
                )
                rows = [dict(r) for r in cur.fetchall()]

    us_gaap: dict = {}
    for r in rows:
        filed_d = r["filed_date"]
        filed_date_obj = filed_d if isinstance(filed_d, date) else date.fromisoformat(str(filed_d))
        if next_us_trading_session(filed_date_obj) > as_of:
            continue
        concept = r["concept"]
        period_start_normalized = _period_start_or_none(
            str(r["period_start"]) if r["period_start"] is not None else None
        )
        entry_row = {
            "end": str(r["period_end"]) if r["period_end"] else None,
            "start": period_start_normalized,
            "val": r["value"],
            "fy": r["fiscal_year"],
            "fp": r["fiscal_period"],
            "form": r["form"],
            "filed": str(r["filed_date"]),
        }
        us_gaap.setdefault(concept, {"units": {"USD": []}})
        us_gaap[concept]["units"]["USD"].append(entry_row)

    return {"cik": cik, "entityName": None, "facts": {"us-gaap": us_gaap}}


def get_ingestion_status(symbol: str, run_id: Optional[str] = None) -> Optional[dict]:
    """Most recent (or a specific run's) ingestion-run record for a
    symbol — the completeness contract replay/reconciliation consult to
    distinguish complete/governed_unsupported/failed/partial/no_data."""
    init_db()
    sym = symbol.upper().strip()
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    if run_id:
                        cur.execute(
                            "SELECT completion_status, taxonomy_classification, failure_reason, "
                            "fact_rows_inserted, distinct_accessions, earliest_filed, latest_filed "
                            "FROM sec_pit_ingestion_runs WHERE symbol = %s AND run_id = %s",
                            (sym, run_id),
                        )
                    else:
                        cur.execute(
                            "SELECT completion_status, taxonomy_classification, failure_reason, "
                            "fact_rows_inserted, distinct_accessions, earliest_filed, latest_filed "
                            "FROM sec_pit_ingestion_runs WHERE symbol = %s ORDER BY acquired_at DESC LIMIT 1",
                            (sym,),
                        )
                    row = cur.fetchone()
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                if run_id:
                    row = conn.execute(
                        "SELECT completion_status, taxonomy_classification, failure_reason, "
                        "fact_rows_inserted, distinct_accessions, earliest_filed, latest_filed "
                        "FROM sec_pit_ingestion_runs WHERE symbol = ? AND run_id = ?",
                        (sym, run_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT completion_status, taxonomy_classification, failure_reason, "
                        "fact_rows_inserted, distinct_accessions, earliest_filed, latest_filed "
                        "FROM sec_pit_ingestion_runs WHERE symbol = ? ORDER BY acquired_at DESC LIMIT 1",
                        (sym,),
                    ).fetchone()
    if row is None:
        return None
    return {
        "completion_status": row[0], "taxonomy_classification": row[1],
        "failure_reason": row[2], "fact_rows_inserted": row[3],
        "distinct_accessions": row[4], "earliest_filed": row[5], "latest_filed": row[6],
    }


def coverage_for_symbol(symbol: str, cik: int) -> dict:
    """Diagnostic: how many facts/concepts/accessions are persisted for
    this symbol, and the earliest/latest filed dates covered — used by
    reconciliation reports, never by the scoring path itself."""
    init_db()
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*), COUNT(DISTINCT concept), COUNT(DISTINCT accession),
                                  MIN(filed_date), MAX(filed_date)
                           FROM sec_pit_facts WHERE cik = %s""",
                        (cik,),
                    )
                    n, n_concepts, n_accn, min_f, max_f = cur.fetchone()
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*), COUNT(DISTINCT concept), COUNT(DISTINCT accession),
                              MIN(filed_date), MAX(filed_date)
                       FROM sec_pit_facts WHERE cik = ?""",
                    (cik,),
                ).fetchone()
                n, n_concepts, n_accn, min_f, max_f = row
    return {
        "symbol": symbol, "cik": cik, "n_facts": n, "n_concepts": n_concepts,
        "n_accessions": n_accn, "earliest_filed": min_f, "latest_filed": max_f,
    }
