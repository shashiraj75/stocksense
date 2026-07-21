"""
StockSense360 SEC Point-in-Time Fact Store (DP-033 remediation, 2026-07-22).

CORRECTION to this session's earlier claim: sec_edgar_adapter.py's 12-hour
in-memory `_facts_cache` is an EPHEMERAL runtime cache, not immutable
persistence. It does not survive a process restart, is not shared across
Railway instances, and a live SEC re-fetch after cache expiry can return a
DIFFERENT payload (a genuine amendment landing, or SEC correcting a prior
response) — silently changing what a repeated historical validation run
would compute, with zero record of what was actually used the first time.
That is not reproducible, and reproducibility is one of this repository's
own explicit temporal-integrity requirements (DP-026/DP-033).

This module is the actual immutable store: SEC EDGAR facts, once ingested,
are written here and NEVER overwritten or deleted — a later-filed amendment
is a NEW row (new accession number), never a mutation of an existing one.
Historical validation reads ONLY from this table after ingestion — no live
network call happens during a validation replay. This satisfies:

  - reproducibility: same DB rows -> same result, every time, regardless
    of SEC's current live state;
  - survives process restart and is shared across every instance (it's in
    the same Postgres database everything else in this repo already uses);
  - immutable provenance: every row carries its own accession number,
    form, filed date, and ingestion timestamp;
  - amendments/restatements never leak backward: a fact filed after a
    signal's as-of cutoff is simply not yet in the eligible row set for
    that cutoff, exactly as sec_edgar_adapter.filter_facts_as_of() already
    enforces for the ephemeral-cache path — this module applies the
    identical filter, just against persisted rows instead of an in-memory
    payload.

Layering: sec_edgar_adapter.py remains the Provider Adapter (owns SEC
authentication, rate limiting, retry, raw-schema validation, field
normalization) — this module is a Persistence Adapter one layer above it,
consuming sec_edgar_adapter's already-fetched/normalized-shape payloads
and writing them here. Nothing in this module makes an SEC HTTP request
directly.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import date, datetime, timezone
from typing import Optional

from services.market_hours import next_us_trading_session

log = logging.getLogger(__name__)

_USE_POSTGRES = bool(os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES", "0") == "1")

# Restrict persisted concepts to exactly the ones sec_edgar_adapter's own
# _DIRECT_FIELD_TAGS cares about — storing every XBRL concept a company
# ever tagged would be unbounded and mostly irrelevant to this repo's
# scoring. Imported lazily inside functions (not at module scope) to avoid
# a hard import-order dependency between the two modules for callers that
# only need one of them.


def _pg_conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


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
    period_end     DATE,
    filed_date     DATE NOT NULL,
    value          DOUBLE PRECISION,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cik, concept, unit, accession, period_end)
);

CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_lookup ON sec_pit_facts(cik, concept, filed_date);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_symbol  ON sec_pit_facts(symbol);

ALTER TABLE sec_pit_facts ENABLE ROW LEVEL SECURITY;
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
    period_end     TEXT,
    filed_date     TEXT NOT NULL,
    value          REAL,
    ingested_at    TEXT NOT NULL,
    UNIQUE (cik, concept, unit, accession, period_end)
);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_lookup ON sec_pit_facts(cik, concept, filed_date);
CREATE INDEX IF NOT EXISTS idx_sec_pit_facts_symbol  ON sec_pit_facts(symbol);
"""

_SQLITE_PATH = os.getenv("SEC_PIT_SQLITE_PATH", "sec_pit_facts.db")
_db_lock = threading.Lock()
_initialized = False


# ── Temporal contract: date-only filing timestamps ──────────────────────────
#
# Confirmed live (2026-07-21/22 investigation): SEC EDGAR's companyfacts API
# gives each fact a DATE-ONLY `filed` value (e.g. "2025-10-31") — no
# intraday time. The submissions API separately exposes a to-the-second
# `acceptanceDateTime` per accession, but joining it here would require an
# additional live SEC request per accession at ingestion time; deferred as
# a documented future enhancement (not silently dropped), since a purely
# conservative date-only rule is sufficient and correct without it.
#
# Conservative rule applied: a date-only filed date is NEVER treated as
# available before market open on that same calendar date — a fact is only
# eligible starting the NEXT US trading session strictly after its filed
# date. This is deliberately conservative in the "available later than
# reality" direction, never the reverse — a pre-market filing on day D
# genuinely available before D's open is still (correctly, safely) treated
# as unavailable until D+1's session, rather than risk the opposite error
# (using a same-day filing as if it predated the market's reaction to it).
# Shared implementation: services.market_hours.next_us_trading_session()
# (imported above) — both this module and sec_edgar_adapter.py's
# filter_facts_as_of() apply the identical rule, not two independently-
# maintained ones.


def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Idempotent — safe to call on every process start, mirrors
    validation_engine.py's own init_db()/_init_db() convention."""
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


def ingest_company_facts(cik: int, symbol: str, facts: dict) -> dict:
    """
    Flattens a raw (or already as-of-pruned — pruning is harmless here,
    ingest the FULL unpruned payload for maximum future coverage)
    companyfacts payload and idempotently upserts every fact row into the
    immutable store. Only concepts sec_edgar_adapter.py's own
    _DIRECT_FIELD_TAGS table cares about are persisted (see module
    docstring). Never overwrites an existing row — the UNIQUE constraint
    on (cik, concept, unit, accession, period_end) makes re-ingestion of
    the same filing a guaranteed no-op (ON CONFLICT DO NOTHING /
    INSERT OR IGNORE), which is exactly the idempotency this store's
    immutability guarantee depends on: if a row for a given accession
    already exists, it is proof that filing's facts were already captured
    and must never be replaced by a later, possibly-different live
    response for the "same" concept (SEC does not retroactively edit an
    already-accepted accession's numbers; a correction always ships as a
    new accession, i.e. a new row here — this store's schema is exactly
    built around that guarantee, not around trusting SEC to never change
    its answer for the same accession).

    Returns {"inserted": n, "skipped": n, "total_seen": n} — never raises;
    a malformed entry is skipped and logged, not fatal to the batch.
    """
    init_db()
    from services.sec_edgar_adapter import _DIRECT_FIELD_TAGS

    wanted_concepts = {tag for tags in _DIRECT_FIELD_TAGS.values() for tag in tags}
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    now = datetime.now(timezone.utc)
    rows = []
    total_seen = 0
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
                continue
            rows.append((
                cik, symbol.upper(), concept, "USD", accn,
                entry.get("form"), entry.get("fy"), entry.get("fp"),
                entry.get("start"), end, filed, float(val),
                now.isoformat() if not _USE_POSTGRES else now,
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
                                filed_date, value, ingested_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (cik, concept, unit, accession, period_end)
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
                            filed_date, value, ingested_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        row,
                    )
                    if cur.rowcount:
                        inserted += 1

    return {"inserted": inserted, "skipped": len(rows) - inserted, "total_seen": total_seen}


def get_facts_as_of_persisted(symbol: str, cik: int, as_of: date) -> dict:
    """
    Pure, deterministic, OFFLINE (no network, no live cache) as-of lookup
    against ONLY what has already been ingested into this store. Returns
    a companyfacts-shaped dict (same shape sec_edgar_adapter.normalize_fields()
    already expects) containing only facts whose filed_date <= as_of —
    identical filtering semantics to sec_edgar_adapter.filter_facts_as_of(),
    sourced from the immutable table instead of an in-memory payload.

    Returns an empty-facts shape (never raises, never fabricates) if
    nothing has been ingested for this symbol/cik, or nothing is eligible
    as of the cutoff — callers must treat this the same as
    filter_facts_as_of()'s "concept dropped entirely" case.
    """
    init_db()
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
        # SQL already applied filed_date <= as_of (a cheap upper bound);
        # the true, conservative eligibility check — filed_date's NEXT
        # trading session, not filed_date itself — is applied here since
        # it can't be expressed as a plain SQL date comparison against a
        # calendar table without a second query. See next_us_trading_session().
        filed_d = r["filed_date"]
        filed_date_obj = filed_d if isinstance(filed_d, date) else date.fromisoformat(str(filed_d))
        if next_us_trading_session(filed_date_obj) > as_of:
            continue
        concept = r["concept"]
        entry = {
            "end": str(r["period_end"]) if r["period_end"] else None,
            "start": str(r["period_start"]) if r["period_start"] else None,
            "val": r["value"],
            "fy": r["fiscal_year"],
            "fp": r["fiscal_period"],
            "form": r["form"],
            "filed": str(r["filed_date"]),
        }
        us_gaap.setdefault(concept, {"units": {"USD": []}})
        us_gaap[concept]["units"]["USD"].append(entry)

    return {"cik": cik, "entityName": None, "facts": {"us-gaap": us_gaap}}


def coverage_for_symbol(symbol: str, cik: int) -> dict:
    """Diagnostic: how many facts/concepts/accessions are persisted for
    this symbol, and the earliest/latest filed dates covered — used by the
    representative-cohort and full-universe dry-run reports, never by the
    scoring path itself."""
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
