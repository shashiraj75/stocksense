"""
Versioned, immutable price-path evidence persistence — Trade Postmortem
Sprint 3A, Stage 3/C.

Mirrors services/postmortem/report_store.py's exact pattern: "current
version identity" is the (paper_trade_id, evidence_bundle_version,
source_id, source_version) quadruple, enforced by a UNIQUE index.
`persist_evidence` uses INSERT ... ON CONFLICT DO NOTHING against that
index, so two concurrent acquisition attempts for the identical trade
and identical source/version can never both insert a row — the loser
reads back the winner's already-persisted row instead. A persisted
evidence row is never UPDATEd by this module — a genuinely new source
version (or a retry that produced different bars, which changes
evidence_hash but NOT the version-triple identity) is stored as-is
under ON CONFLICT DO NOTHING semantics: identical version-triple = same
row, no matter how many times acquisition retries.

The full bar list is stored alongside the row (JSONB), never discarded
in favor of only the derived MFE/MAE — Stage 3's own explicit
requirement.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime

from services.postmortem.price_path_evidence import PricePathBar, PricePathEvidenceBundle


@dataclass(frozen=True)
class PersistedPricePathEvidence:
    id: int
    paper_trade_id: int
    user_id: str
    symbol: str
    market: str
    evidence_bundle_version: str
    source_id: str
    source_type: str
    source_version: str
    provider_symbol: str
    price_adjustment_basis: str
    bar_interval: str
    market_timezone: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_bar_policy: str
    exit_bar_policy: str
    requested_window_start: date
    requested_window_end: date
    observed_window_start: date | None
    observed_window_end: date | None
    bars_expected: int | None
    bars_observed: int
    missing_bar_count: int | None
    data_completeness: str
    freshness_basis: str
    acquisition_timestamp: datetime
    source_manifest: dict
    limitations: list
    bars: list
    evidence_hash: str


_COLUMNS = (
    "id, paper_trade_id, user_id, symbol, market, evidence_bundle_version, "
    "source_id, source_type, source_version, provider_symbol, price_adjustment_basis, "
    "bar_interval, market_timezone, entry_timestamp, exit_timestamp, "
    "entry_bar_policy, exit_bar_policy, requested_window_start, requested_window_end, "
    "observed_window_start, observed_window_end, bars_expected, bars_observed, "
    "missing_bar_count, data_completeness, freshness_basis, acquisition_timestamp, "
    "source_manifest, limitations, bars, evidence_hash"
)


def _row_to_evidence(row) -> PersistedPricePathEvidence:
    return PersistedPricePathEvidence(*row)


def _bars_to_json(bars: tuple[PricePathBar, ...]) -> str:
    return json.dumps([
        {
            "timestamp": b.timestamp.isoformat(), "interval": b.interval,
            "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": b.volume, "session_date": b.session_date.isoformat(),
            "source_id": b.source_id, "adjustment_basis": b.adjustment_basis,
            "verification_level": b.verification_level,
        }
        for b in bars
    ])


def persist_evidence(conn, bundle: PricePathEvidenceBundle) -> tuple[PersistedPricePathEvidence, bool]:
    """Returns (evidence, created). `created=False` means a row for this
    exact (paper_trade_id, evidence_bundle_version, source_id,
    source_version) already existed (a concurrent acquisition won the
    race, or this is a harmless retry) and the existing row is returned
    unchanged — this function never overwrites a persisted bundle."""
    row = conn.execute(
        f"""INSERT INTO paper_trade_price_path_evidence (
                paper_trade_id, user_id, symbol, market, evidence_bundle_version,
                source_id, source_type, source_version, provider_symbol, price_adjustment_basis,
                bar_interval, market_timezone, entry_timestamp, exit_timestamp,
                entry_bar_policy, exit_bar_policy, requested_window_start, requested_window_end,
                observed_window_start, observed_window_end, bars_expected, bars_observed,
                missing_bar_count, data_completeness, freshness_basis, acquisition_timestamp,
                source_manifest, limitations, bars, evidence_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (paper_trade_id, evidence_bundle_version, source_id, source_version)
            DO NOTHING
            RETURNING {_COLUMNS}""",
        (
            bundle.paper_trade_id, bundle.user_id, bundle.symbol, bundle.market, bundle.evidence_bundle_version,
            bundle.source_id, bundle.source_type, bundle.source_version, bundle.provider_symbol, bundle.price_adjustment_basis,
            bundle.bar_interval, bundle.market_timezone, bundle.entry_timestamp, bundle.exit_timestamp,
            bundle.entry_bar_policy, bundle.exit_bar_policy, bundle.requested_window_start, bundle.requested_window_end,
            bundle.observed_window_start, bundle.observed_window_end, bundle.bars_expected, bundle.bars_observed,
            bundle.missing_bar_count, bundle.data_completeness, bundle.freshness_basis, bundle.acquisition_timestamp,
            json.dumps(bundle.source_manifest), json.dumps(bundle.limitations), _bars_to_json(bundle.bars), bundle.evidence_hash,
        ),
    ).fetchone()
    if row is not None:
        return _row_to_evidence(row), True

    existing = conn.execute(
        f"""SELECT {_COLUMNS} FROM paper_trade_price_path_evidence
            WHERE paper_trade_id = %s AND evidence_bundle_version = %s
              AND source_id = %s AND source_version = %s""",
        (bundle.paper_trade_id, bundle.evidence_bundle_version, bundle.source_id, bundle.source_version),
    ).fetchone()
    if existing is None:
        raise RuntimeError(
            f"persist_evidence: INSERT conflicted but no existing row found for trade "
            f"{bundle.paper_trade_id} (version {bundle.evidence_bundle_version}/{bundle.source_id}/{bundle.source_version})"
        )
    return _row_to_evidence(existing), False


def get_current_evidence(
    conn, *, paper_trade_id: int, user_id: str, evidence_bundle_version: str, source_id: str, source_version: str,
) -> PersistedPricePathEvidence | None:
    """User-scoped lookup — evidence belonging to another user is never
    returned, matching the report/exit-snapshot tables' own strict
    ownership discipline."""
    row = conn.execute(
        f"""SELECT {_COLUMNS} FROM paper_trade_price_path_evidence
            WHERE paper_trade_id = %s AND user_id = %s
              AND evidence_bundle_version = %s AND source_id = %s AND source_version = %s""",
        (paper_trade_id, user_id, evidence_bundle_version, source_id, source_version),
    ).fetchone()
    return _row_to_evidence(row) if row else None
