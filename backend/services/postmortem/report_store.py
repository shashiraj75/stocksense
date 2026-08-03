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

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def canonicalize_report_json(value):
    """The ONE authoritative canonicalization path for every persisted
    postmortem report JSON value (structured_report, evidence_items,
    claims, source_manifest, evidence_gaps, warnings) — a recursive,
    eager walk (not a json.dumps `default=` callback, so the exact same
    canonical Python objects can be reused for both evidence_hash
    computation and persistence, per Package A's requirement that the
    hash and the stored row are computed from identical canonical
    content, never two independently-converted copies).

    Real defect found by real-PostgreSQL CI (fixed at its source in
    current_report_generation.build_current_report_payload): a governed
    price-path EvidenceItem/PostmortemClaim dataclass instance reaching
    the OLD json.dumps(..., default=str) call would have been silently
    stringified into an opaque repr instead of a governed JSON object.
    This function never falls back to str() — an object it cannot
    represent as governed JSON raises TypeError."""
    if isinstance(value, _JSON_SCALAR_TYPES):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        canonical = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"report JSON dictionary keys must be strings — got an unsupported "
                    f"{type(k).__name__} key, refusing to silently coerce it"
                )
            canonical[k] = canonicalize_report_json(v)
        return canonical
    if isinstance(value, (list, tuple)):
        return [canonicalize_report_json(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return canonicalize_report_json(dataclasses.asdict(value))
    raise TypeError(
        f"report JSON must be built from plain JSON-safe scalars, dicts, lists, "
        f"dataclasses, Enums or date/datetime values — got an unsupported "
        f"{type(value).__name__}, refusing to silently stringify it"
    )


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
    supersedes_report_id: int | None = None
    generated_at: datetime | None = None


def compute_evidence_hash(evidence_items: list, claims: list) -> str:
    """Deterministic for identical evidence — sorted-key JSON serialization
    of exactly the two lists that make up a report's evidence content,
    hashed with SHA-256. Used for debugging/dedup visibility only; it is
    NOT the uniqueness boundary (the version-triple UNIQUE index is).

    Canonicalizes evidence_items/claims through the SAME
    canonicalize_report_json() path persist_report uses for the
    persisted row — never a second, independently-converted copy — so
    an equivalent dataclass and dictionary input produce the identical
    hash, and the stored evidence_hash always matches a recomputation
    over the exact persisted content."""
    canonical_evidence_items = canonicalize_report_json(evidence_items)
    canonical_claims = canonicalize_report_json(claims)
    canonical = json.dumps(
        {"evidence_items": canonical_evidence_items, "claims": canonical_claims}, sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_REPORT_COLUMNS = (
    "id, paper_trade_id, user_id, market, report_trading_date, market_timezone, "
    "report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version, "
    "evidence_hash, status, structured_report, evidence_items, claims, source_manifest, "
    "evidence_gaps, warnings, generated_at, supersedes_report_id"
)


def _row_to_report(row) -> PersistedReport:
    (rid, trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
     ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, generated_at, supersedes_id) = row
    return PersistedReport(
        id=rid, paper_trade_id=trade_id, user_id=user_id, market=market,
        report_trading_date=trading_date, market_timezone=tz,
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version=bundle_v, evidence_hash=ev_hash, status=status,
        structured_report=structured, evidence_items=ev_items, claims=claims,
        source_manifest=manifest, evidence_gaps=gaps, warnings=warnings,
        generated_at=generated_at, supersedes_report_id=supersedes_id,
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
    supersedes_report_id: int | None = None,
) -> tuple[PersistedReport, bool]:
    """Returns (report, created). `created=False` means a report for this
    exact version triple already existed (a concurrent generator won the
    race) and the existing row is returned unchanged — this function
    never overwrites a completed report.

    `supersedes_report_id` (Sprint 3A, Stage I) links a price-path-
    enhanced report to the prior, less-complete report for the same
    trade it supersedes — set only by callers building on top of an
    existing report (services.postmortem.price_path_generation); plain
    Sprint 1/2 callers never pass it and it stays NULL, so existing
    Sprint 2 report rows are entirely unaffected by this addition.

    Package A — every authoritative JSON field is canonicalized ONCE,
    through the single canonicalize_report_json() path, and the SAME
    canonical objects are used both to compute evidence_hash and to
    build the INSERT payload below — never two independently-converted
    copies. A serialization failure (an unsupported object anywhere in
    any of the six fields) raises before any INSERT is attempted, so no
    partial report row can ever be persisted."""
    canonical_structured_report = canonicalize_report_json(structured_report)
    canonical_evidence_items = canonicalize_report_json(evidence_items)
    canonical_claims = canonicalize_report_json(claims)
    canonical_source_manifest = canonicalize_report_json(source_manifest)
    canonical_evidence_gaps = canonicalize_report_json(evidence_gaps)
    canonical_warnings = canonicalize_report_json(warnings)

    evidence_hash = compute_evidence_hash(canonical_evidence_items, canonical_claims)
    row = conn.execute(
        f"""INSERT INTO paper_trade_postmortem_report (
                paper_trade_id, user_id, market, report_trading_date, market_timezone,
                report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version,
                evidence_hash, status, structured_report, evidence_items, claims, source_manifest,
                evidence_gaps, warnings, supersedes_report_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (paper_trade_id, report_schema_version, calculation_version, attribution_rules_version)
            DO NOTHING
            RETURNING {_REPORT_COLUMNS}""",
        (
            paper_trade_id, user_id, market, report_trading_date, market_timezone,
            report_schema_version, calculation_version, attribution_rules_version, evidence_bundle_version,
            evidence_hash, status,
            json.dumps(canonical_structured_report),
            json.dumps(canonical_evidence_items),
            json.dumps(canonical_claims),
            json.dumps(canonical_source_manifest), json.dumps(canonical_evidence_gaps), json.dumps(canonical_warnings),
            supersedes_report_id,
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


def get_report_by_id(conn, *, report_id: int, user_id: str) -> PersistedReport | None:
    """User-scoped lookup by primary key — used to read back a prior
    (e.g. Sprint 2) report before building a price-path-enhanced report
    that supersedes it (Sprint 3A, Stage I). A report belonging to
    another user is never returned."""
    row = conn.execute(
        f"""SELECT {_REPORT_COLUMNS} FROM paper_trade_postmortem_report
            WHERE id = %s AND user_id = %s""",
        (report_id, user_id),
    ).fetchone()
    return _row_to_report(row) if row else None


def get_latest_report_for_trade(conn, *, paper_trade_id: int, user_id: str) -> PersistedReport | None:
    """User-scoped — the most recently generated report for a trade
    across ALL schema versions, preferring the highest
    report_schema_version and, within that, the most recent
    generated_at. This is the deterministic 'latest version' selection
    Stage I requires: a price-path report (schema 1.1.0) is always
    preferred over a plain Sprint 2 report (schema 1.0.0) for the same
    trade once one exists, without needing the caller to already know
    which schema version to ask for."""
    row = conn.execute(
        f"""SELECT {_REPORT_COLUMNS} FROM paper_trade_postmortem_report
            WHERE paper_trade_id = %s AND user_id = %s
            ORDER BY report_schema_version DESC, generated_at DESC LIMIT 1""",
        (paper_trade_id, user_id),
    ).fetchone()
    return _row_to_report(row) if row else None


def get_latest_predecessor_report(conn, *, paper_trade_id: int, user_id: str, exclude_report_schema_version: str) -> PersistedReport | None:
    """Wave B, Stage J4E — the deterministic supersession-predecessor
    lookup: the most recent report for this trade whose report_schema_
    version is NOT the caller's own target version (so a report can
    never supersede a same-version sibling, and a retried/idempotent
    call for the SAME version never picks up a stale predecessor from
    an earlier attempt at that same version). Preferring the highest
    remaining schema_version, then most recent generated_at, exactly
    mirrors get_latest_report_for_trade's own ordering."""
    row = conn.execute(
        f"""SELECT {_REPORT_COLUMNS} FROM paper_trade_postmortem_report
            WHERE paper_trade_id = %s AND user_id = %s AND report_schema_version != %s
            ORDER BY report_schema_version DESC, generated_at DESC LIMIT 1""",
        (paper_trade_id, user_id, exclude_report_schema_version),
    ).fetchone()
    return _row_to_report(row) if row else None
