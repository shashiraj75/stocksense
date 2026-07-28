"""
Versioned, persisted structured report store — Trade Postmortem Engine,
Sprint 2.

`structured_report`/`evidence_items`/`claims` are the authoritative JSON
representation of a trade's postmortem — never only an unstructured
paragraph. "Current version identity" for a trade is the
(paper_trade_id, report_schema_version, calculation_version,
attribution_rules_version) quadruple, enforced by a UNIQUE index at the
database level (see services/postgres_store.py) — this module's
`persist_report` uses `INSERT ... ON CONFLICT DO NOTHING` against that
index, so two concurrent generation attempts for the identical trade and
identical versions can never both insert a row; the loser reads back the
winner's already-persisted row instead. A completed report row is never
UPDATEd by this module — a genuinely new rules or calculation version
simply inserts a new row under its own key.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PersistedReport:
    id: int
    paper_trade_id: int
    user_id: str
    market: str
    report_trading_date: date
    market_timezone: str
    report_schema_version: str
    calculation_version: str
    attribution_rules_version: str
    evidence_bundle_version: str
    evidence_hash: str
    status: str
    structured_report: dict
    evidence_items: list
    claims: list
    source_manifest: dict
    evidence_gaps: list
    warnings: list


def compute_evidence_hash(evidence_items: list, claims: list) -> str:
    """Deterministic for identical evidence — sorted-key JSON serialization
    of exactly the two lists that make up a report's evidence content,
    hashed with SHA-256. Used for debugging/dedup visibility only; it is
    NOT the uniqueness boundary (the version-triple UNIQUE index is)."""
    canonical = json.dumps({"evidence_items": evidence_items, "claims": claims}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_REPORT_COLUMNS = (
    "id, paper_trade_id, user_id, market, report_trading_date, market_timezone, "
    "report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version, "
    "evidence_hash, status, structured_report, evidence_items, claims, source_manifest, "
    "evidence_gaps, warnings"
)


def _row_to_report(row) -> PersistedReport:
    (rid, trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
     ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings) = row
    return PersistedReport(
        id=rid, paper_trade_id=trade_id, user_id=user_id, market=market,
        report_trading_date=trading_date, market_timezone=tz,
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version=bundle_v, evidence_hash=ev_hash, status=status,
        structured_report=structured, evidence_items=ev_items, claims=claims,
        source_manifest=manifest, evidence_gaps=gaps, warnings=warnings,
    )


def persist_report(
    conn,
    *,
    paper_trade_id: int,
    user_id: str,
    market: str,
    report_trading_date: date,
    market_timezone: str,
    report_schema_version: str,
    calculation_version: str,
    attribution_rules_version: str,
    evidence_bundle_version: str,
    status: str,
    structured_report: dict,
    evidence_items: list,
    claims: list,
    source_manifest: dict,
    evidence_gaps: list,
    warnings: list,
) -> tuple[PersistedReport, bool]:
    """Returns (report, created). `created=False` means a report for this
    exact version triple already existed (a concurrent generator won the
    race) and the existing row is returned unchanged — this function
    never overwrites a completed report."""
    evidence_hash = compute_evidence_hash(evidence_items, claims)
    row = conn.execute(
        f"""INSERT INTO paper_trade_postmortem_report (
                paper_trade_id, user_id, market, report_trading_date, market_timezone,
                report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version,
                evidence_hash, status, structured_report, evidence_items, claims, source_manifest,
                evidence_gaps, warnings
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (paper_trade_id, report_schema_version, calculation_version, attribution_rules_version)
            DO NOTHING
            RETURNING {_REPORT_COLUMNS}""",
        (
            paper_trade_id, user_id, market, report_trading_date, market_timezone,
            report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version,
            evidence_hash, status,
            json.dumps(structured_report, default=str), json.dumps(evidence_items, default=str), json.dumps(claims, default=str),
            json.dumps(source_manifest, default=str), json.dumps(evidence_gaps, default=str), json.dumps(warnings, default=str),
        ),
    ).fetchone()
    if row is not None:
        return _row_to_report(row), True

    existing = conn.execute(
        f"""SELECT {_REPORT_COLUMNS} FROM paper_trade_postmortem_report
            WHERE paper_trade_id = %s AND report_schema_version = %s
              AND calculation_version = %s AND attribution_rules_version = %s""",
        (paper_trade_id, report_schema_version, calculation_version, attribution_rules_version),
    ).fetchone()
    if existing is None:
        # Should be unreachable: the INSERT only DO-NOTHINGs when a
        # conflicting row already exists, so it must be findable
        # immediately afterward within the same transaction.
        raise RuntimeError(
            f"persist_report: INSERT conflicted but no existing row found for trade {paper_trade_id} "
            f"(versions {report_schema_version}/{calculation_version}/{attribution_rules_version})"
        )
    return _row_to_report(existing), False


def get_current_report(conn, *, paper_trade_id: int, user_id: str,
                        report_schema_version: str, calculation_version: str, attribution_rules_version: str
                        ) -> PersistedReport | None:
    """User-scoped lookup — a report belonging to another user is never
    returned, matching the existing single-trade postmortem endpoint's
    own strict ownership check."""
    row = conn.execute(
        f"""SELECT {_REPORT_COLUMNS} FROM paper_trade_postmortem_report
            WHERE paper_trade_id = %s AND user_id = %s
              AND report_schema_version = %s AND calculation_version = %s AND attribution_rules_version = %s""",
        (paper_trade_id, user_id, report_schema_version, calculation_version, attribution_rules_version),
    ).fetchone()
    return _row_to_report(row) if row else None
