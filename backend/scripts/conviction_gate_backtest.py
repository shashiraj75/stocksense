"""
Conviction-gate walk-forward backtest.

Validates the ACTUAL Daily Picks publication gate field — `confidence`
(persisted per-candidate on `alpha_observations.signal_confidence`,
`item.get("confidence")` in `services/daily_picks.py`) — against realized
forward returns. This is deliberately NOT `val_signals.composite_score`
(validation_engine.py's `_score_at` proxy, which structurally tops out
around 82 and can never reach the real 85.0 gate threshold) and NOT the
separate `confidence_model`/`_confidence_engine` 5-factor heuristic. See
Documentation/Engineering-Handbook/Daily-Picks/DAILY-PICKS-IMPLEMENTATION-REGISTER.md
DP-034/DP-035 for the gate's own semantics evidence.

Two layers, deliberately separated so the statistics are unit-testable
without a database or network connection:

1. Pure functions (`bucket_for_confidence`, `compute_bucket_stats`) — take a
   plain list of {"confidence": float, "realized_return_pct": float} dicts
   and produce win-rate / average-return / sample-size-adequacy per bucket.
   Covered by synthetic-data tests in
   backend/tests/unit/test_conviction_gate_backtest.py.

2. I/O functions (`fetch_observations`, `resolve_realized_return`,
   `run_backtest`) — read `alpha_observations` (Postgres) and compute a
   forward return via yfinance, mirroring the trading-day-window logic
   already used by `services/alpha_engine/outcome_logger.py` for the
   unrelated legacy `predictions`/`outcomes` tables. `alpha_observations`
   itself stores no realized outcome for any row — nothing resolves one —
   so this is computed live, read-only, on every invocation. No write path
   exists in this module; it never persists anything back to
   `alpha_observations` or any other table.

Audit extension (2026-08-22)
----------------------------
This script now also drives a reproducible CONVICTION AUDIT. The audit's
normative rules — market/horizon separation, the eight populations, the claim
levels, and the two return measures — live in
`backend/services/alpha_engine/audit_contract.py` and are summarised here only
to point at it. The key correction it encodes:

  * the original return measure enters at a close that PRECEDES the pick's own
    pre-market generation. That is a NON-EXECUTABLE PRIOR-SESSION RESEARCH
    PRICE (not look-ahead bias, and not investor P&L). It is retained
    unchanged, but is now explicitly labelled RESEARCH_PRIOR_CLOSE, and
  * a second measure, EXECUTABLE_NEXT_OPEN, enters at the open of the first
    regular session strictly after `run_generated_at`, using the real NYSE and
    NSE exchange calendars (`audit_contract`'s companion `audit_calendar`).

Both measures are GROSS of all transaction costs. No cost model is
implemented here and neither measure may be described as net or as investor
P&L.

`--audit-out DIR` writes a full evidence bundle (run_manifest.json,
row_decisions.jsonl, aggregate_summary.json, statistical_results.json,
data_integrity_results.json) to a caller-supplied directory. That directory
MUST be outside the repository — no generated data file is ever committed.
Row counts must reconcile exactly (fetched == included + excluded) at every
stage; a failure raises `ReconciliationError` and exits non-zero rather than
reporting a denominator the audit cannot account for.

Usage (manual, not wired into any scheduled job):
    python backend/scripts/conviction_gate_backtest.py --market IN --horizon short
    python backend/scripts/conviction_gate_backtest.py --market US --horizon medium --limit 200
    python backend/scripts/conviction_gate_backtest.py --market US --horizon short \
        --audit-out /tmp/conviction-audit
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Mirrors services/thresholds.py DailyPicksPublicationThresholds without
# importing it, so this script has no import-time dependency on the
# production package layout (kept import-light for standalone invocation
# and for unit tests that never touch backend/services at all).
MIN_CONVICTION_TO_PUBLISH = 85.0

# Bucket edges chosen around the real gate: below the existing
# `_passes_quality_gate` hard floor (25), the pre-existing short-term
# "priority" cutoff (80), the publication gate itself (85), and a
# saturation bucket (95+) since confidence is observed to pile up at 100.
BUCKET_EDGES: list[tuple[float, float, str]] = [
    (0.0, 25.0, "0-24 (below quality gate)"),
    (25.0, 50.0, "25-49"),
    (50.0, 70.0, "50-69"),
    (70.0, 80.0, "70-79"),
    (80.0, 85.0, "80-84 (below publication gate)"),
    (85.0, 95.0, "85-94 (publication gate)"),
    (95.0, 100.0 + 1e-9, "95-100 (publication gate)"),
]

# A bucket's win-rate/avg-return is reported but flagged inadequate below
# this sample size — never presented as a meaningful result on its own.
MIN_SAMPLE_SIZE = 30

# Mirrors outcome_logger.HORIZON_CONFIG's trading-day windows and minimum
# calendar-day wait, applied here to alpha_observations' reference_price
# instead of the legacy predictions table.
HORIZON_TRADING_DAYS = {"short": 5, "medium": 20, "long": 60}
HORIZON_MIN_CALENDAR_DAYS = {"short": 3, "medium": 30, "long": 90}

MARKETS = ("IN", "US")
HORIZONS = ("short", "medium", "long")
_TICKER_SUFFIX = {"IN": ".NS", "US": ""}


# --------------------------------------------------------------------------
# Pure statistics layer — no I/O, fully unit-testable on synthetic data.
# --------------------------------------------------------------------------


def bucket_for_confidence(confidence: float) -> str | None:
    """Return the bucket label for a confidence value, or None if it's not
    a finite value in [0, 100] (fails closed — never silently mis-buckets
    an invalid value into a real bucket)."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return None
    if c != c or c in (float("inf"), float("-inf")):  # NaN/±Infinity
        return None
    if c < 0.0 or c > 100.0:
        return None
    for lo, hi, label in BUCKET_EDGES:
        if lo <= c < hi:
            return label
    return None


@dataclass
class BucketStats:
    label: str
    n: int
    n_wins: int
    win_rate: float | None
    avg_return_pct: float | None
    adequate_sample: bool


def compute_bucket_stats(
    observations: list[dict],
    min_sample_size: int = MIN_SAMPLE_SIZE,
) -> dict[str, BucketStats]:
    """
    Bucket `observations` (each a dict with numeric "confidence" and
    "realized_return_pct" keys) by confidence and compute win rate (share
    of rows with realized_return_pct > 0) and average realized return per
    bucket.

    A "win" is a strictly positive realized return — a flat 0.0% return is
    not counted as a win (matches the surrounding codebase's convention of
    never treating a missing/neutral outcome as a fabricated success, see
    the Validation Benchmark Evidence Integrity work in Current-Release-
    Status.md).

    Rows with a non-bucketable confidence (see `bucket_for_confidence`) or
    a non-finite realized_return_pct are silently excluded from every
    bucket's statistics — never counted as either a win or a loss.

    A bucket with fewer than `min_sample_size` observations still reports
    its (possibly noisy) win_rate/avg_return_pct, but `adequate_sample` is
    False — callers must not present an inadequate bucket's numbers as a
    meaningful finding.
    """
    grouped: dict[str, list[float]] = {label: [] for _, _, label in BUCKET_EDGES}
    for obs in observations:
        label = bucket_for_confidence(obs.get("confidence"))
        if label is None:
            continue
        ret = obs.get("realized_return_pct")
        try:
            ret_f = float(ret)
        except (TypeError, ValueError):
            continue
        if ret_f != ret_f or ret_f in (float("inf"), float("-inf")):
            continue
        grouped[label].append(ret_f)

    results: dict[str, BucketStats] = {}
    for _, _, label in BUCKET_EDGES:
        returns = grouped[label]
        n = len(returns)
        if n == 0:
            results[label] = BucketStats(label, 0, 0, None, None, False)
            continue
        n_wins = sum(1 for r in returns if r > 0.0)
        results[label] = BucketStats(
            label=label,
            n=n,
            n_wins=n_wins,
            win_rate=n_wins / n,
            avg_return_pct=sum(returns) / n,
            adequate_sample=n >= min_sample_size,
        )
    return results


def format_report(stats_by_horizon: dict[str, dict[str, BucketStats]]) -> str:
    """Render a plain-text summary table, honestly labeling inadequate
    sample sizes rather than omitting or silently including them."""
    lines: list[str] = []
    for horizon, stats in stats_by_horizon.items():
        lines.append(f"\n=== {horizon.upper()} horizon ===")
        lines.append(f"{'bucket':<32}{'n':>6}{'win_rate':>10}{'avg_ret%':>10}  adequate")
        for _, _, label in BUCKET_EDGES:
            s = stats.get(label)
            if s is None or s.n == 0:
                lines.append(f"{label:<32}{0:>6}{'n/a':>10}{'n/a':>10}  NO (n=0)")
                continue
            wr = f"{s.win_rate * 100:.1f}%"
            ar = f"{s.avg_return_pct:.2f}"
            flag = "yes" if s.adequate_sample else f"NO (n<{MIN_SAMPLE_SIZE})"
            lines.append(f"{label:<32}{s.n:>6}{wr:>10}{ar:>10}  {flag}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O layer — Postgres read + yfinance forward-return lookup. Deliberately
# thin; every decision that needs to be unit-testable lives above this line.
# --------------------------------------------------------------------------


def _to_date(value) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))

# --------------------------------------------------------------------------
# AUDIT LAYER — populations, policy eras, the two return measures, the
# missing analyses, and the reproducible evidence bundle. Every rule applied
# here is stated normatively in services/alpha_engine/audit_contract.py.
# --------------------------------------------------------------------------


def _contract():
    from services.alpha_engine import audit_contract

    return audit_contract


def _finite(x) -> bool:
    """True only for a real, finite float — NaN and +/-Infinity are rejected."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def canonical_key(market: str, horizon: str, run_id, symbol: str) -> str:
    """
    The row's identity.

    (run_id, market, horizon, symbol) was VERIFIED unique against the live
    table (zero duplicate groups across all 45,296 rows on 2026-08-22), so it
    is a sound primary identity and does not depend on carrying a UUID
    through the evidence bundle.
    """
    return f"{market}/{horizon}/{run_id}/{symbol}"


# --------------------------------------------------------------------------
# POPULATION SOURCES
# --------------------------------------------------------------------------
# Two interchangeable read-only sources produce IDENTICALLY-SHAPED rows:
#
#   postgres          — a direct psycopg connection via DATABASE_URL.
#   extract:<path>    — an immutable, checksummed extract taken read-only from
#                       the production database and frozen to a file.
#
# The extract path exists because production Postgres is not reachable by
# direct connection string from every governed environment, while read-only
# SQL access is. Freezing the population first also makes the audit exactly
# reproducible: the same extract always yields the same numbers, and the
# extract's checksum is recorded in the manifest.


class PopulationSourceError(RuntimeError):
    """Raised when a population source cannot be read or fails its checksum."""


def fetch_observations(conn, market: str, horizon: str, limit: int | None = None) -> list[dict]:
    """Read-only fetch of every (market, horizon) alpha_observations row
    with a numeric signal_confidence, oldest first."""
    sql = """
        SELECT observation_id, run_id, symbol, run_generated_at,
               run_session_date, reference_session_date,
               reference_price, signal_confidence, signal,
               ranking_alpha, is_daily_pick, pick_rank
        FROM alpha_observations
        WHERE market = %s AND horizon = %s AND signal_confidence IS NOT NULL
        ORDER BY reference_session_date ASC
    """
    if limit:
        sql += " LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, (market, horizon, limit) if limit else (market, horizon))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


_SIGNAL_CODE = {"B": "BUY", "H": "HOLD", "S": "SELL"}


def parse_extract(payload: dict, market: str, horizon: str) -> list[dict]:
    """
    Decode a frozen production extract into observation rows.

    Packed row grammar (one row per ';', fields per '~'), chosen so the
    extract is compact enough to transport intact yet loses nothing the audit
    needs:

        symbol ~ signal-initial ~ signal_confidence ~ ranking_alpha
               ~ reference_price ~ is_daily_pick(0|1) ~ pick_rank
               ~ (reference_session_date - run_session_date) in days

    Per-run constants (run_id, run_generated_at, run_session_date) are carried
    once on the run header — VERIFIED constant within a run against live data.
    `reference_session_date` is NOT constant within a run (22 of 51 short-
    horizon runs carry more than one), so it is stored per row as a day offset.
    """
    rows: list[dict] = []
    for run in payload.get("runs", []):
        if run["market"] != market or run.get("horizon", horizon) != horizon:
            continue
        session_date = _to_date(run["run_session_date"])
        generated = run["run_generated_at"]
        if isinstance(generated, str):
            generated = _dt.datetime.fromisoformat(
                generated.replace(" ", "T").replace("+00", "+00:00"))
        packed = run.get("packed") or ""
        parts = [p for p in packed.split(";") if p]
        if len(parts) != int(run["n"]):
            raise PopulationSourceError(
                f"run {run['run_id']}: extract declares n={run['n']} but "
                f"decodes {len(parts)} rows — refusing a truncated population")
        for item in parts:
            f = item.split("~")
            if len(f) != 8:
                raise PopulationSourceError(
                    f"run {run['run_id']}: malformed packed row {item!r}")
            sym, sig, conf, ralpha, refp, dp, prank, refoff = f
            rows.append({
                "run_id": run["run_id"],
                "symbol": sym,
                "run_generated_at": generated,
                "run_session_date": session_date,
                "reference_session_date": session_date + _dt.timedelta(days=int(refoff)),
                "reference_price": float(refp),
                "signal_confidence": float(conf),
                "signal": _SIGNAL_CODE.get(sig, sig),
                "ranking_alpha": float(ralpha),
                "is_daily_pick": dp == "1",
                "pick_rank": int(prank) if prank else None,
            })
    return rows


def load_extract(path) -> dict:
    """Read a frozen extract and verify its per-run checksums."""
    import hashlib
    import json
    import pathlib

    p = pathlib.Path(path).expanduser().resolve()
    raw = p.read_text(encoding="utf-8")
    payload = json.loads(raw)
    bad = []
    for run in payload.get("runs", []):
        expected = run.get("md5")
        if not expected:
            continue
        actual = hashlib.md5(
            (run.get("packed") or "").encode("utf-8")).hexdigest()
        if actual != expected:
            bad.append(run["run_id"])
    if bad:
        raise PopulationSourceError(
            f"extract checksum mismatch for run(s) {bad} — the transported "
            f"population does not match what the database emitted; refusing "
            f"to audit possibly-corrupted rows")
    payload.setdefault("meta", {})["extract_sha256"] = hashlib.sha256(
        raw.encode("utf-8")).hexdigest()
    payload["meta"]["extract_path"] = str(p)
    payload["meta"]["runs_checksum_verified"] = sum(
        1 for r in payload.get("runs", []) if r.get("md5"))
    return payload


def load_population(source: str, market: str, horizon: str, *,
                    limit: int | None = None) -> tuple[list[dict], dict]:
    """Return `(rows, source_metadata)` from whichever read-only source."""
    if source.startswith("extract:"):
        payload = load_extract(source.split(":", 1)[1])
        rows = parse_extract(payload, market, horizon)
        if limit:
            rows = rows[:limit]
        return rows, dict(payload.get("meta", {}))
    if source == "postgres":
        import os

        import psycopg

        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            raise PopulationSourceError(
                "DATABASE_URL is not set — cannot run against Postgres")
        with psycopg.connect(database_url) as conn:
            rows = fetch_observations(conn, market, horizon, limit=limit)
        # Never record the credential itself — only a non-secret identifier.
        host = database_url.split("@")[-1].split("/")[0] if "@" in database_url else "unknown"
        return rows, {"source": f"postgres:{host}", "table": "alpha_observations"}
    raise PopulationSourceError(f"unknown population source {source!r}")


# --------------------------------------------------------------------------
# RETURN MEASURES — both read the SAME immutable price snapshot
# --------------------------------------------------------------------------


def resolve_research_return(row: dict, market: str, horizon: str, snapshot,
                            *, today: _dt.date | None = None) -> tuple:
    """
    MEASURE A — "PRIOR-CLOSE-TO-FUTURE-CLOSE RESEARCH RETURN — NON-EXECUTABLE".

    Entry: the CLOSE of the first session on/after `reference_session_date`.
    Exit: the CLOSE of the session exactly HORIZON_TRADING_DAYS later on the
    exchange calendar. Gross of all costs.

    The entry close precedes the pick's own pre-market generation, so no
    investor could have transacted at it. It is a research reference price —
    NOT look-ahead bias and NOT investor P&L. It is retained purely for
    comparability with the superseded analysis.

    Returns `(return_pct, exclusion_reason, provenance)`. The provenance
    records the ACTUAL entry/exit session dates and prices, and whether the
    stored `reference_price` reconciled against the provider's own close —
    so a reader can tell which number the audit actually used.
    """
    from services.alpha_engine import audit_calendar

    prov: dict = {"measure": "RESEARCH_PRIOR_CLOSE", "price_source": "snapshot",
                  "calendar": audit_calendar._MARKET_CALENDAR.get(market)}
    trading_days = HORIZON_TRADING_DAYS.get(horizon)
    if trading_days is None:
        return None, "unknown_horizon", prov
    ref_date = _to_date(row["reference_session_date"])
    prov["reference_session_date"] = ref_date.isoformat()

    entry_iso = snapshot.first_session_on_or_after(market, row["symbol"], ref_date)
    if entry_iso is None:
        return None, "no_price_data_on_or_after_reference_date", prov
    prov["entry_session_date"] = entry_iso
    entry_price = snapshot.get_close(market, row["symbol"], entry_iso)
    if entry_price is None:
        return None, "entry_close_missing", prov
    if entry_price <= 0:
        return None, "non_positive_entry_price", prov
    prov["entry_price"] = entry_price
    prov["entry_price_field"] = "close"

    # Did the stored reference_price agree with the provider's own close?
    stored = row.get("reference_price")
    if _finite(stored) and float(stored) > 0:
        rel = abs(float(stored) - entry_price) / entry_price
        prov["stored_reference_price"] = float(stored)
        prov["stored_vs_provider_rel_diff"] = rel
        prov["reference_price_used"] = False
        prov["reference_price_reconciled"] = rel <= 0.02
        prov["reference_price_note"] = (
            "The audit uses the PROVIDER close, never the stored "
            "reference_price; the stored value is reconciled against it and "
            "reported so any divergence is visible.")
    else:
        prov["stored_reference_price"] = None
        prov["reference_price_used"] = False
        prov["reference_price_reconciled"] = None

    exit_date = audit_calendar.session_offset(market, _to_date(entry_iso), trading_days)
    if exit_date is None:
        return None, "horizon_window_not_yet_complete", prov
    prov["exit_session_date"] = exit_date.isoformat()
    today = today or _dt.date.today()
    if exit_date >= today:
        return None, "horizon_window_not_yet_complete", prov
    exit_price = snapshot.get_close(market, row["symbol"], exit_date)
    if exit_price is None:
        return None, "exit_close_missing", prov
    prov["exit_price"] = exit_price
    prov["exit_price_field"] = "close"
    return round((exit_price - entry_price) / entry_price * 100, 6), None, prov


def resolve_executable_return(row: dict, market: str, horizon: str, snapshot,
                              *, today: _dt.date | None = None) -> tuple:
    """
    MEASURE B — "NEXT-TRADABLE-OPEN-TO-HORIZON-CLOSE GROSS BENCHMARK RETURN".

    Entry: the OPEN of the first regular session strictly after
    `run_generated_at`, on the real exchange calendar (weekends, holidays and
    DST handled by `audit_calendar`, never by a hardcoded offset). Exit: the
    CLOSE of the session exactly HORIZON_TRADING_DAYS sessions later.

    Gross of all costs — no commission, spread, slippage, tax or market-impact
    model exists. This is an executable BENCHMARK, not a realised fill, and
    may never be called net return or investor P&L.

    A window that has not fully elapsed is EXCLUDED, never truncated.
    """
    from services.alpha_engine import audit_calendar

    prov: dict = {"measure": "EXECUTABLE_NEXT_OPEN", "price_source": "snapshot",
                  "calendar": audit_calendar._MARKET_CALENDAR.get(market)}
    trading_days = HORIZON_TRADING_DAYS.get(horizon)
    if trading_days is None:
        return None, "unknown_horizon", prov
    generated = row.get("run_generated_at")
    if generated is None:
        return None, "missing_run_generated_at", prov
    prov["run_generated_at"] = str(generated)

    entry = audit_calendar.next_tradable_open(market, generated)
    if entry is None:
        return None, "no_tradable_session_after_generation", prov
    entry_date, entry_open_utc = entry
    prov["entry_session_date"] = entry_date.isoformat()
    prov["entry_session_open_utc"] = entry_open_utc.isoformat()

    exit_date = audit_calendar.session_offset(market, entry_date, trading_days)
    if exit_date is None:
        return None, "horizon_window_not_yet_complete", prov
    prov["exit_session_date"] = exit_date.isoformat()
    today = today or _dt.date.today()
    if exit_date >= today:
        return None, "horizon_window_not_yet_complete", prov

    entry_price = snapshot.get_open(market, row["symbol"], entry_date)
    if entry_price is None:
        return None, "entry_open_missing", prov
    if entry_price <= 0:
        return None, "non_positive_entry_price", prov
    exit_price = snapshot.get_close(market, row["symbol"], exit_date)
    if exit_price is None:
        return None, "exit_close_missing", prov
    prov["entry_price"] = entry_price
    prov["entry_price_field"] = "open"
    prov["exit_price"] = exit_price
    prov["exit_price_field"] = "close"
    return round((exit_price - entry_price) / entry_price * 100, 6), None, prov


# --------------------------------------------------------------------------
# POPULATIONS AND RANKING
# --------------------------------------------------------------------------


def assign_populations(row: dict) -> list[str]:
    """
    Label one row with every contract population it belongs to.

    Membership is deliberately non-exclusive (a row is simultaneously
    ALL_ELIGIBLE, BUY and BUY_HIGH_CONV) so each analysis selects its own pair
    without re-deriving definitions — the drift that let the superseded report
    compare BUY_HIGH_CONV against all of NON_BUY.
    """
    c = _contract()
    pops = [c.P_ALL_ELIGIBLE]
    is_buy = str(row.get("signal") or "").upper() == "BUY"
    conf = row.get("signal_confidence")
    if is_buy:
        pops.append(c.P_BUY)
        if _finite(conf) and float(conf) >= c.MIN_CONVICTION_TO_PUBLISH:
            pops.append(c.P_BUY_HIGH_CONV)
        else:
            pops.append(c.P_BUY_LOW_CONV)
        if not row.get("is_daily_pick"):
            pops.append(c.P_UNPUBLISHED_BUY)
    else:
        pops.append(c.P_NON_BUY)
    if row.get("is_daily_pick"):
        pops.append(c.P_PUBLISHED)
    return pops


# Empirically PROVEN direction, not an assumption. Production selects picks
# with `sorted(..., key=ranking_alpha, reverse=True)` (daily_picks.py), and
# that ordering was verified against live production rows: among published
# short-horizon picks, corr(pick_rank, ranking_alpha) = -0.63 (IN) and -0.65
# (US), with 110/132 (IN) and 124/126 (US) published rows matching the
# tie-aware DESCENDING ranking_alpha order versus 8 and 6 matching ASCENDING.
# Higher ranking_alpha therefore means BETTER rank (lower pick_rank).
RANKING_ALPHA_HIGHER_IS_BETTER = True


def within_run_rank_percentile(rows: list[dict], n_quantiles: int = 4) -> None:
    """
    Attach TIE-AWARE `rank_percentile`, `rank_quantile` and `rank_tied_with`
    to each BUY row, computed WITHIN its own run.

    Raw `ranking_alpha` is not comparable across runs (each run z-scores
    against its own cross-section), so a pooled sort would compare
    incomparable scores. Percentiles are computed inside a single run_id, over
    BUY rows only.

    TIE HANDLING — AVERAGE RANKS. Rows sharing a `ranking_alpha` receive the
    MEAN of the ranks they jointly occupy, so tied rows always receive an
    identical percentile. The previous implementation used positional index
    after a plain sort, which handed tied rows different percentiles decided
    by list order — non-deterministic in principle and simply wrong in fact.

    Percentile orientation follows RANKING_ALPHA_HIGHER_IS_BETTER: 1.0 is the
    BEST-ranked row in the run. Runs with fewer than two BUY rows get None —
    a single-element percentile carries no information and must never be
    silently coded as 1.0.
    """
    by_run: dict[object, list[dict]] = {}
    for r in rows:
        if str(r.get("signal") or "").upper() == "BUY" and _finite(r.get("ranking_alpha")):
            by_run.setdefault(r.get("run_id"), []).append(r)
    for _run, group in by_run.items():
        if len(group) < 2:
            for r in group:
                r["rank_percentile"] = None
                r["rank_quantile"] = None
                r["rank_tie_handling"] = "average_ranks"
                r["rank_tied_with"] = 0
            continue
        # Ascending by ranking_alpha, so index 0 is the WORST row.
        ordered = sorted(group, key=lambda r: float(r["ranking_alpha"]))
        n = len(ordered)
        i = 0
        while i < n:
            j = i
            while j + 1 < n and float(ordered[j + 1]["ranking_alpha"]) == \
                    float(ordered[i]["ranking_alpha"]):
                j += 1
            # Average of the 0-based positions i..j shared by this tie group.
            avg_pos = (i + j) / 2.0
            pct = avg_pos / (n - 1)
            for k in range(i, j + 1):
                r = ordered[k]
                r["rank_percentile"] = pct
                r["rank_quantile"] = min(int(pct * n_quantiles), n_quantiles - 1) + 1
                r["rank_tie_handling"] = "average_ranks"
                r["rank_tied_with"] = j - i
            i = j + 1


# --------------------------------------------------------------------------
# COMPARISONS
# --------------------------------------------------------------------------


def _win_rows(rows: list[dict], pop_a: str, pop_b: str, measure_key: str,
              *, restrict: set | None = None) -> list[dict]:
    """
    Build the flat row list the statistics layer consumes for A-vs-B.

    `restrict`, when given, is a set of canonical keys the comparison is
    limited to (used for run-matched, era-restricted comparisons).
    """
    out = []
    for r in rows:
        if restrict is not None and r["canonical_key"] not in restrict:
            continue
        ret = r.get(measure_key)
        if not _finite(ret):
            continue
        if pop_a in r["populations"]:
            group = "A"
        elif pop_b in r["populations"]:
            group = "B"
        else:
            continue
        out.append({
            "group": group,
            "is_win": float(ret) > 0.0,
            "cluster_date": r["run_session_date_iso"],
            "symbol": r["symbol"],
            "canonical_key": r["canonical_key"],
        })
    return out


def compare_populations(label, rows, pop_a, pop_b, measure_key, *, seed,
                        restrict: set | None = None, n_runs=None, min_runs=None,
                        extra_reasons=None, permutation_draws: int = 2000,
                        bootstrap_draws: int | None = None):
    """
    Run one A-vs-B comparison through the full governed pipeline and classify
    it. Identifiability and the symbol jackknife are computed INSIDE
    `audit_stats.analyse_comparison`, before classification, and are inputs to
    `classify_claim` — never checked afterwards.

    `bootstrap_draws` is the date-blocked bootstrap draw count. It is threaded
    all the way from the CLI to `analyse_comparison`; an earlier version
    accepted `--bootstrap-draws`, recorded it in the manifest, and then never
    passed it down, so the manifest documented a draw count the run had not
    actually used. The value is echoed back on the result so the manifest can
    be reconciled against what was really executed.
    """
    from services.alpha_engine import audit_stats

    if bootstrap_draws is None:
        bootstrap_draws = audit_stats.DEFAULT_BOOTSTRAP_DRAWS
    flat = _win_rows(rows, pop_a, pop_b, measure_key, restrict=restrict)
    res = audit_stats.analyse_comparison(
        label, flat, seed=seed, n_runs=n_runs, min_runs=min_runs,
        draws=bootstrap_draws,
        permutation_draws=permutation_draws,
        extra_identifiability_reasons=extra_reasons)
    result = res.to_dict()
    result["population_a"] = pop_a
    result["population_b"] = pop_b
    result["measure"] = measure_key
    result["bootstrap_draws_used"] = bootstrap_draws
    result["permutation_draws_used"] = permutation_draws
    result["claim_level"] = classify_claim(res)
    return result


def classify_claim(result) -> str:
    """
    Map a statistical result onto a contract claim level.

    ORDER MATTERS AND IS DELIBERATE:

      1. NOT_IDENTIFIABLE first. If the comparison could not be estimated,
         that is the answer. It is NOT downgraded to PRELIMINARY (which
         asserts suggestive evidence) and NOT reported as NOT_PROVEN (which
         asserts a real test returned null).
      2. SIGN-UNSTABLE JACKKNIFE IS A HARD VETO ON PROVEN. A difference that
         flips sign when a single ticker is deleted is not a robust finding,
         however small its p-value.
      3. THE METHOD CEILING IS A HARD CAP ON PROVEN. Every p-value this audit
         produces is the MAXIMUM of two SEPARATE one-way stratified
         permutation tests — a dual sensitivity check, not joint two-way
         clustered inference (see
         `audit_stats.dual_one_way_stratified_permutation_sensitivity`).
         While that is so, PROVEN is unreachable BY CONSTRUCTION and the
         strongest attainable level is PRELIMINARY. This is applied here, in
         code, so the ceiling cannot be lost in prose.
      4. SIGN-UNSTABLE JACKKNIFE IS A HARD VETO ON PROVEN. A difference that
         flips sign when a single ticker is deleted is not a robust finding,
         however small its p-value.
      5. PROVEN would additionally require everything simultaneously: adequate
         clusters, a date-blocked interval excluding zero, a significant
         p-value, naive/dependence-aware agreement, AND jackknife sign
         stability. Holm survival across the complete family is applied by the
         caller and can only ever downgrade further.

    This function is the single place those caps are enforced. No caller may
    raise a level by hand.
    """
    from services.alpha_engine import audit_stats

    c = _contract()
    if result.identifiability == "NOT_IDENTIFIABLE":
        return c.NOT_IDENTIFIABLE
    if result.difference_pp is None:
        return c.NOT_IDENTIFIABLE
    jk = result.jackknife or {}
    sign_stable = jk.get("sign_stable")
    if not result.inference_permitted or result.block_ci_pp is None:
        # Estimable, but no inference could be made. Only genuinely
        # directionally-suggestive evidence (a stable sign) is PRELIMINARY.
        return c.PRELIMINARY if sign_stable else c.NOT_IDENTIFIABLE
    lo, hi = result.block_ci_pp
    excludes_zero = (lo > 0 and hi > 0) or (lo < 0 and hi < 0)
    p = result.permutation_p_dual_one_way_max
    significant = p is not None and p < 0.05
    if not excludes_zero or not significant:
        return c.NOT_PROVEN
    if result.methods_agree is False:
        return c.PRELIMINARY
    if sign_stable is not True:
        # HARD VETO — a sign-unstable estimate can never be PROVEN.
        return c.PRELIMINARY
    if not getattr(result, "joint_two_way_inference_available", False):
        # METHOD CEILING. Everything else passed, but the inference underneath
        # is a dual one-way sensitivity check rather than a joint two-way
        # clustered test, so PROVEN is not available on this evidence.
        return audit_stats.MAX_CLAIM_LEVEL_WITHOUT_JOINT_INFERENCE
    return c.PROVEN


def ranking_lift(rows: list[dict], measure_key: str, *, seed: int,
                 n_quantiles: int = 4, permutation_draws: int = 2000) -> dict:
    """
    (C) Does WITHIN-RUN rank order add information beyond the binary BUY call?

    Reports the tie-aware quantile win rates AND a dependence-aware trend
    test. Quartile monotonicity is DESCRIPTIVE ONLY and can never on its own
    establish ranking skill — four ordered win rates arise by chance far more
    readily than intuition suggests. The claim level is therefore driven by
    the two-way permutation trend test, with monotonicity reported alongside.
    """
    from services.alpha_engine import audit_stats

    c = _contract()
    eligible = [r for r in rows
                if c.P_BUY in r["populations"]
                and r.get("rank_percentile") is not None
                and _finite(r.get(measure_key))]
    out: dict = {
        "n": len(eligible),
        "tie_handling": "average_ranks",
        "direction": ("ranking_alpha HIGHER IS BETTER — verified against "
                      "production sorting behaviour, not assumed"),
        "direction_verified": RANKING_ALPHA_HIGHER_IS_BETTER,
        "note": ("Within-run percentiles of ranking_alpha (raw scores are not "
                 "comparable across runs). Monotonicity is descriptive; only "
                 "the dependence-aware trend test can support a claim."),
    }
    if len(eligible) < max(n_quantiles * 2, 20):
        out["claim_level"] = c.NOT_IDENTIFIABLE
        out["reason"] = "too few ranked BUY rows with a resolved return"
        return out

    buckets: list[list[dict]] = [[] for _ in range(n_quantiles)]
    for r in eligible:
        buckets[int(r["rank_quantile"]) - 1].append(r)

    quantiles = []
    for i, b in enumerate(buckets):
        wr = (sum(1 for r in b if float(r[measure_key]) > 0) / len(b)) if b else None
        quantiles.append({
            "quantile": i + 1, "n": len(b), "win_rate": wr,
            "avg_return_pct": (sum(float(r[measure_key]) for r in b) / len(b))
                              if b else None})
    rates = [q["win_rate"] for q in quantiles if q["win_rate"] is not None]
    monotone = (len(rates) == n_quantiles
                and (all(x <= y for x, y in zip(rates, rates[1:]))
                     or all(x >= y for x, y in zip(rates, rates[1:]))))

    flat = [{"rank_percentile": r["rank_percentile"],
             "is_win": float(r[measure_key]) > 0.0,
             "cluster_date": r["run_session_date_iso"],
             "symbol": r["symbol"]} for r in eligible]
    trend = audit_stats.dual_one_way_trend_sensitivity(
        flat, draws=permutation_draws, seed=seed)
    jk = audit_stats.symbol_cluster_jackknife(
        [dict(f, group="A" if f["rank_percentile"] >= 0.5 else "B") for f in flat])

    out.update({
        "quantiles": quantiles,
        "monotone": monotone,
        "monotone_note": ("DESCRIPTIVE ONLY — monotonicity alone cannot "
                          "establish ranking skill and never sets the claim "
                          "level on its own."),
        "top_minus_bottom_pp": ((rates[-1] - rates[0]) * 100.0)
                               if len(rates) == n_quantiles else None,
        "trend_test": trend,
        "jackknife": jk,
    })
    p = trend.get("p_dual_one_way_max")
    n_dates = len({f["cluster_date"] for f in flat})
    n_syms = len({f["symbol"] for f in flat})
    if n_dates < audit_stats.MIN_CLUSTERS_FOR_INFERENCE or \
            n_syms < audit_stats.MIN_CLUSTERS_FOR_INFERENCE or p is None:
        out["claim_level"] = c.NOT_IDENTIFIABLE
        out["reason"] = (f"inadequate clusters for a trend test "
                         f"(dates={n_dates}, symbols={n_syms})")
    elif p < 0.05 and jk.get("sign_stable") is True:
        # Capped at PRELIMINARY for the same reason every other comparison is:
        # the underlying statistic is a dual one-way sensitivity check, not
        # joint two-way clustered inference.
        out["claim_level"] = audit_stats.MAX_CLAIM_LEVEL_WITHOUT_JOINT_INFERENCE
        out["reason"] = ("trend is significant under BOTH one-way permutation "
                         "nulls; capped at PRELIMINARY because no joint "
                         "two-way inference exists, and pending Holm correction")
    else:
        out["claim_level"] = c.NOT_PROVEN
        out["reason"] = ("no significant rank->outcome trend under either "
                         "one-way permutation null")
    out["joint_two_way_inference_available"] = False
    out["max_claim_level"] = audit_stats.MAX_CLAIM_LEVEL_WITHOUT_JOINT_INFERENCE
    return out


def concentration_report(rows: list[dict], measure_key: str) -> dict:
    """
    Concentration and stability of whatever sample a claim rests on.

    A "10,000 observation" sample built from 400 symbols over 20 dates has
    nothing like 10,000 independent pieces of information. The first/second-
    half split is DATE-PURE and is compared only against THIS market's own
    baseline — never another market's, per contract rule 1.
    """
    c = _contract()
    resolved = [r for r in rows if _finite(r.get(measure_key))]
    n = len(resolved)
    if n == 0:
        return {"n_resolved": 0, "note": "no resolved rows"}

    dates = sorted({r["run_session_date_iso"] for r in resolved})
    sym_counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    for r in resolved:
        sym_counts[r["symbol"]] = sym_counts.get(r["symbol"], 0) + 1
        date_counts[r["run_session_date_iso"]] = (
            date_counts.get(r["run_session_date_iso"], 0) + 1)

    def win_rate(subset):
        return (sum(1 for r in subset if float(r[measure_key]) > 0) / len(subset)
                if subset else None)

    half = len(dates) // 2
    first_dates, second_dates = set(dates[:half]), set(dates[half:])
    first = [r for r in resolved if r["run_session_date_iso"] in first_dates]
    second = [r for r in resolved if r["run_session_date_iso"] in second_dates]

    return {
        "n_resolved": n,
        "distinct_symbols": len(sym_counts),
        "distinct_dates": len(dates),
        "max_single_symbol_share": max(sym_counts.values()) / n,
        "max_single_date_share": max(date_counts.values()) / n,
        "baseline_win_rate": win_rate(resolved),
        "first_half": {"n": len(first), "dates": len(first_dates),
                       "win_rate": win_rate(first)},
        "second_half": {"n": len(second), "dates": len(second_dates),
                        "win_rate": win_rate(second)},
        "half_split_note": (
            "Date-pure split on this market's own ordered distinct dates, "
            "compared ONLY against this market's own baseline_win_rate. No "
            "cross-market comparison is made (contract rule 1)."),
        "sector_breadth": {
            "claim_level": c.NOT_REPRODUCIBLE,
            "note": ("No sector column exists in alpha_observations or any "
                     "joined table. Sector breadth cannot be computed and "
                     "must not be claimed."),
        },
    }


# --------------------------------------------------------------------------
# PUBLISHED vs UNPUBLISHED — run-matched, era-pure
# --------------------------------------------------------------------------


def match_published_unpublished(rows: list[dict], measure_key: str) -> dict:
    """
    Build the run-matched, era-pure comparison sets for PUBLISHED vs
    UNPUBLISHED_BUY.

    THE MATCH IS ON run_id + market + horizon, exactly. A published row is
    only ever compared against unpublished BUY rows from THE SAME RUN, because
    those are the candidates the selector actually chose between. Comparing
    across runs compares different days' market conditions, which is precisely
    the confound this analysis exists to remove.

    A run is USABLE only if, after resolution, it contains at least one
    resolved PUBLISHED row AND at least one resolved UNPUBLISHED_BUY row.
    Every run that is not usable is EXCLUDED EXPLICITLY, with its identity and
    its reason recorded, so the exclusions reconcile against the population.

    Results are keyed BY POLICY ERA and never pooled across eras.
    """
    c = _contract()
    by_run: dict[object, dict] = {}
    for r in rows:
        slot = by_run.setdefault(r["run_id"], {
            "run_id": r["run_id"], "market": r["market"],
            "horizon": r["horizon"], "era": r["policy_era"],
            "run_session_date": r["run_session_date_iso"],
            "published": [], "unpublished_buy": [], "other": 0})
        resolved = _finite(r.get(measure_key))
        if c.P_PUBLISHED in r["populations"]:
            slot["published"].append((r["canonical_key"], resolved))
        elif c.P_UNPUBLISHED_BUY in r["populations"]:
            slot["unpublished_buy"].append((r["canonical_key"], resolved))
        else:
            slot["other"] += 1

    matched_keys: set = set()
    matched_runs, excluded_runs = [], []
    for slot in sorted(by_run.values(), key=lambda s: (s["run_session_date"], str(s["run_id"]))):
        pub_ok = [k for k, ok in slot["published"] if ok]
        unpub_ok = [k for k, ok in slot["unpublished_buy"] if ok]
        summary = {
            "run_id": slot["run_id"], "market": slot["market"],
            "horizon": slot["horizon"], "policy_era": slot["era"],
            "run_session_date": slot["run_session_date"],
            "n_published": len(slot["published"]),
            "n_published_resolved": len(pub_ok),
            "n_unpublished_buy": len(slot["unpublished_buy"]),
            "n_unpublished_buy_resolved": len(unpub_ok),
        }
        if not pub_ok and not unpub_ok:
            summary["exclusion_reason"] = "no_resolved_rows_in_either_group"
        elif not pub_ok:
            summary["exclusion_reason"] = "no_resolved_published_row_in_this_run"
        elif not unpub_ok:
            summary["exclusion_reason"] = "no_resolved_unpublished_buy_row_in_this_run"
        else:
            summary["exclusion_reason"] = None
        if summary["exclusion_reason"]:
            excluded_runs.append(summary)
        else:
            matched_runs.append(summary)
            matched_keys.update(pub_ok)
            matched_keys.update(unpub_ok)

    by_era: dict[str, dict] = {}
    for era in c.POLICY_ERAS:
        era_runs = [m for m in matched_runs if m["policy_era"] == era]
        keys: set = set()
        for slot in by_run.values():
            if slot["era"] != era:
                continue
            if any(m["run_id"] == slot["run_id"] for m in era_runs):
                keys.update(k for k, ok in slot["published"] if ok)
                keys.update(k for k, ok in slot["unpublished_buy"] if ok)
        by_era[era] = {
            "policy_era": era,
            "n_matched_runs": len(era_runs),
            "n_excluded_runs": len([e for e in excluded_runs if e["policy_era"] == era]),
            "matched_keys": keys,
            "n_matched_rows": len(keys),
        }

    return {
        "matched_runs": matched_runs,
        "excluded_runs": excluded_runs,
        "n_runs_total": len(by_run),
        "n_runs_matched": len(matched_runs),
        "n_runs_excluded": len(excluded_runs),
        "matched_keys": matched_keys,
        "by_era": by_era,
        "matching_rule": ("exact match on run_id + market + horizon; a run "
                          "contributes only if it has at least one RESOLVED "
                          "row in BOTH groups"),
        "pooling_rule": ("policy eras are NEVER pooled; each era is estimated "
                         "separately or reported NOT_IDENTIFIABLE"),
    }


def published_vs_unpublished(rows, measure_key, *, seed, market, horizon,
                             permutation_draws: int = 2000,
                             bootstrap_draws: int | None = None) -> dict:
    """
    (D) What users saw, against the BUY candidates from the SAME RUN that were
    not published — estimated separately within each publication-policy era.

    There is no headline number pooling eras, by construction. If the current
    era is underpowered it is NOT_IDENTIFIABLE — never NOT_PROVEN (which would
    falsely imply a real test found nothing) and never PRELIMINARY (which
    would falsely imply suggestive evidence).
    """
    c = _contract()
    matching = match_published_unpublished(rows, measure_key)
    per_era = {}
    for era, info in matching["by_era"].items():
        label = f"{market}/{horizon}/{era}/PUBLISHED-vs-UNPUBLISHED_BUY"
        reasons = []
        if info["n_matched_runs"] == 0:
            reasons.append(
                f"policy era {era!r} has no run with resolved rows in both "
                f"groups — nothing can be compared within it")
        res = compare_populations(
            label, rows, c.P_PUBLISHED, c.P_UNPUBLISHED_BUY, measure_key,
            seed=seed, restrict=info["matched_keys"],
            n_runs=info["n_matched_runs"],
            min_runs=c.MIN_RUNS_PER_ERA_FOR_ESTIMATE,
            extra_reasons=reasons, permutation_draws=permutation_draws,
            bootstrap_draws=bootstrap_draws)
        res["policy_era"] = era
        res["n_matched_runs"] = info["n_matched_runs"]
        res["n_matched_rows"] = info["n_matched_rows"]
        res["n_excluded_runs"] = info["n_excluded_runs"]
        per_era[era] = res
    return {
        "by_policy_era": per_era,
        "matching": {k: v for k, v in matching.items()
                     if k not in ("matched_keys", "by_era")},
        "era_row_counts": {e: {"n_matched_runs": i["n_matched_runs"],
                               "n_matched_rows": i["n_matched_rows"],
                               "n_excluded_runs": i["n_excluded_runs"]}
                           for e, i in matching["by_era"].items()},
        "headline": None,
        "headline_note": (
            "There is deliberately NO pooled headline. Pooling pre-policy and "
            "current-policy rows would compare populations produced by "
            "different selection rules."),
    }


# --------------------------------------------------------------------------
# DATA-INTEGRITY CHECKS — actually executed, never a placeholder
# --------------------------------------------------------------------------


def run_data_integrity_checks(rows_by_cell: dict, source_meta: dict) -> dict:
    """
    Reproducible read-only data-integrity checks, EXECUTED over the audited
    population and returned as structured results.

    Each check carries its own definition alongside its result so the evidence
    bundle is self-describing and a reviewer never has to hunt for what a
    number meant. Checks that require server-side state the extract cannot
    carry (e.g. `factor_ic_history` row count) are taken from the extract's
    source metadata, which was populated by the same read-only queries at
    extraction time; those are flagged `source: extract_metadata`.
    """
    c = _contract()
    checks: dict[str, dict] = {}

    def add(name, definition, result, passed, source="audited_population"):
        checks[name] = {"definition": definition, "result": result,
                        "passed": passed, "source": source}

    all_rows = [r for rows in rows_by_cell.values() for r in rows]

    # 1. Total rows by market/horizon.
    counts = {}
    for r in all_rows:
        k = f'{r["market"]}/{r["horizon"]}'
        counts[k] = counts.get(k, 0) + 1
    add("row_counts_by_market_horizon",
        "COUNT(*) grouped by market and horizon over the audited population.",
        counts, all(v > 0 for v in counts.values()))

    # 2. Null / non-finite critical fields.
    critical = ("signal_confidence", "ranking_alpha", "reference_price")
    bad = {f: sum(1 for r in all_rows if not _finite(r.get(f))) for f in critical}
    bad["signal"] = sum(1 for r in all_rows
                        if str(r.get("signal") or "").upper()
                        not in ("BUY", "HOLD", "SELL"))
    bad["run_generated_at"] = sum(1 for r in all_rows if r.get("run_generated_at") is None)
    add("null_or_non_finite_critical_fields",
        "Count of rows whose critical fields are NULL, NaN, +/-Inf, or (for "
        "`signal`) outside {BUY, HOLD, SELL}.",
        bad, all(v == 0 for v in bad.values()))

    # 3. Duplicate canonical keys.
    seen: dict[str, int] = {}
    for r in all_rows:
        seen[r["canonical_key"]] = seen.get(r["canonical_key"], 0) + 1
    dups = {k: v for k, v in seen.items() if v > 1}
    add("duplicate_canonical_keys",
        "Canonical key is (market, horizon, run_id, symbol). Any key "
        "appearing more than once is a duplicate.",
        {"n_duplicate_keys": len(dups), "examples": sorted(dups)[:10]},
        len(dups) == 0)

    # 4. Market-label routing.
    routing = {
        "IN_rows_with_dot_suffix": sum(
            1 for r in all_rows if r["market"] == "IN" and "." in r["symbol"]),
        "US_rows_with_NS_suffix": sum(
            1 for r in all_rows if r["market"] == "US" and r["symbol"].endswith(".NS")),
        "unknown_markets": sorted({r["market"] for r in all_rows} - {"IN", "US"}),
    }
    add("market_label_routing",
        "India symbols must be stored unsuffixed (the .NS suffix is applied "
        "only at provider-call time); US symbols must never carry .NS; no "
        "market label outside {IN, US} may appear.",
        routing,
        routing["IN_rows_with_dot_suffix"] == 0
        and routing["US_rows_with_NS_suffix"] == 0
        and not routing["unknown_markets"])

    # 5. Score / z-score ranges.
    confs = [float(r["signal_confidence"]) for r in all_rows if _finite(r.get("signal_confidence"))]
    alphas = [float(r["ranking_alpha"]) for r in all_rows if _finite(r.get("ranking_alpha"))]
    ranges = {
        "signal_confidence": {"min": min(confs) if confs else None,
                              "max": max(confs) if confs else None,
                              "out_of_0_100": sum(1 for v in confs if v < 0 or v > 100)},
        "ranking_alpha": {"min": min(alphas) if alphas else None,
                          "max": max(alphas) if alphas else None,
                          "abs_gt_10": sum(1 for v in alphas if abs(v) > 10)},
    }
    add("score_and_zscore_ranges",
        "signal_confidence must lie in [0, 100]; ranking_alpha is a z-scored "
        "quantity and any |value| > 10 is implausible.",
        ranges,
        ranges["signal_confidence"]["out_of_0_100"] == 0
        and ranges["ranking_alpha"]["abs_gt_10"] == 0)

    # 6. Reference-price validity.
    refp = {"n_non_finite": sum(1 for r in all_rows if not _finite(r.get("reference_price"))),
            "n_non_positive": sum(1 for r in all_rows
                                  if _finite(r.get("reference_price"))
                                  and float(r["reference_price"]) <= 0)}
    add("reference_price_validity",
        "reference_price must be finite and strictly positive on every row.",
        refp, refp["n_non_finite"] == 0 and refp["n_non_positive"] == 0)

    # 7. Schema-version distribution (from extract metadata).
    sv = source_meta.get("schema_version_distribution")
    add("schema_version_distribution",
        "Distribution of (feature_schema_version, regime_schema_version). A "
        "single version across the window means no mid-window schema change "
        "silently split the population.",
        sv, bool(sv) and len(sv) == 1, source="extract_metadata")

    # 8. Publication gate/cap compliance BY POLICY ERA.
    era_compliance: dict[str, dict] = {}
    per_run: dict[tuple, dict] = {}
    for r in all_rows:
        key = (r["market"], r["horizon"], r["run_id"])
        slot = per_run.setdefault(key, {"era": r["policy_era"], "published": 0,
                                        "below_gate": 0})
        if r.get("is_daily_pick"):
            slot["published"] += 1
            if float(r["signal_confidence"]) < c.MIN_CONVICTION_TO_PUBLISH:
                slot["below_gate"] += 1
    for (market, _h, _rid), slot in per_run.items():
        k = f'{market}/{slot["era"]}'
        acc = era_compliance.setdefault(
            k, {"runs": 0, "runs_violating_gate": 0, "runs_violating_cap": 0,
                "max_published_in_a_run": 0})
        acc["runs"] += 1
        acc["max_published_in_a_run"] = max(acc["max_published_in_a_run"],
                                            slot["published"])
        if slot["below_gate"] > 0:
            acc["runs_violating_gate"] += 1
        if slot["published"] > c.MAX_PUBLISHED_PER_RUN:
            acc["runs_violating_cap"] += 1
    # Expectation per era: legacy is expected to violate both (that is what
    # makes it a separate era); gate_only must satisfy the gate but may exceed
    # the cap; gate_plus_cap must satisfy both.
    ok = True
    for k, acc in era_compliance.items():
        era = k.split("/", 1)[1]
        if era == c.ERA_GATE_ONLY and acc["runs_violating_gate"]:
            ok = False
        if era == c.ERA_GATE_PLUS_CAP and (acc["runs_violating_gate"]
                                           or acc["runs_violating_cap"]):
            ok = False
    add("publication_gate_and_cap_by_policy_era",
        f"Within each (market, policy era): number of runs publishing a row "
        f"below the >= {c.MIN_CONVICTION_TO_PUBLISH} conviction gate, and "
        f"number publishing more than {c.MAX_PUBLISHED_PER_RUN} rows. This "
        f"check is what DEFINES the era boundaries: gate_only must have zero "
        f"gate violations, gate_plus_cap zero of either. Legacy is expected "
        f"to violate both — that is why it is a separate era.",
        era_compliance, ok)

    # 9. factor_ic_history population (from extract metadata).
    fic = source_meta.get("factor_ic_history_rows")
    add("factor_ic_history_population",
        "Row count of factor_ic_history, read read-only from production. A "
        "count of 0 means the factor-IC learning feedback loop has never been "
        "populated — the accurate containment rationale.",
        {"rows": fic},
        fic is not None, source="extract_metadata")

    # 10. Outcome-column absence.
    oc = source_meta.get("outcome_columns_present")
    add("outcome_column_absence",
        "alpha_observations must contain NO realised-outcome column "
        "(realized_return_pct / outcome / realized_return / actual_return). "
        "Its absence is why this audit must resolve returns externally and "
        "why alpha_observations cannot resolve anything on its own.",
        {"outcome_columns_present": oc}, oc == 0, source="extract_metadata")

    # 11. Production ranking does not read alpha_observations.
    checks["production_ranking_does_not_read_alpha_observations"] = \
        _check_ranking_isolation()

    checks["_summary"] = {
        "n_checks": len([k for k in checks if not k.startswith("_")]),
        "n_passed": len([v for k, v in checks.items()
                         if not k.startswith("_") and v.get("passed")]),
        "n_failed": sorted(k for k, v in checks.items()
                           if not k.startswith("_") and not v.get("passed")),
    }
    return checks


def _check_ranking_isolation() -> dict:
    """
    Static proof that the PRODUCTION ranking path never reads
    `alpha_observations`.

    This matters because if production ranking read the same table this audit
    analyses, the audit would be measuring a feedback loop rather than an
    independent record. The check greps the production service modules for a
    READ of the table; the only permitted reference is the write/append path.
    """
    import pathlib
    import re

    services = pathlib.Path(__file__).resolve().parents[1] / "services"

    # The ONE known, reviewed reader: a coverage/observability summary
    # (row counts, distinct runs, earliest/latest run timestamp) served to the
    # status endpoint. It reads no per-symbol field, feeds no score and feeds
    # no ordering, so it cannot create a feedback loop.
    ALLOWED_READERS = {
        "services/postgres_store.py": {
            "function": "get_alpha_observations_coverage",
            "why_permitted": (
                "Aggregate observability only: COUNT(*), COUNT(DISTINCT "
                "run_id), MIN/MAX(run_generated_at) grouped by market and "
                "horizon. It returns no per-symbol value and nothing it "
                "returns reaches ranking, scoring or selection."),
        },
    }
    # The production ranking/selection path itself. Zero reads permitted here.
    RANKING_PATH = ("services/daily_picks.py",)

    # Only a real SQL read counts: FROM / JOIN against the table. A prose
    # mention of the table name in a comment or an identifier is not a read,
    # and matching those was producing false positives.
    read_pattern = re.compile(
        r"\b(FROM|JOIN)\s+alpha_observations\b", re.IGNORECASE)

    scanned = 0
    readers: list[dict] = []
    for path in sorted(services.rglob("*.py")):
        if "audit_" in path.name:
            continue  # the audit itself is allowed to read it
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits = read_pattern.findall(text)
        if hits:
            rel = str(path.relative_to(services.parent))
            readers.append({"file": rel, "n_reads": len(hits),
                            "allowed": rel in ALLOWED_READERS,
                            "justification": ALLOWED_READERS.get(rel)})

    unexpected = [r for r in readers if not r["allowed"]]
    ranking_readers = [r for r in readers if r["file"] in RANKING_PATH]
    return {
        "definition": (
            "Static scan of backend/services/**.py (excluding the audit's own "
            "audit_* modules) for a SQL READ (FROM/JOIN) of "
            "alpha_observations. Production ranking must only ever APPEND to "
            "this table, never read from it — otherwise this audit would be "
            "measuring a feedback loop rather than an independent record. "
            "Exactly one reader is allow-listed and justified below; the "
            "ranking path itself (daily_picks.py) must have zero reads."),
        "result": {
            "files_scanned": scanned,
            "readers_found": readers,
            "unexpected_readers": [r["file"] for r in unexpected],
            "ranking_path_readers": [r["file"] for r in ranking_readers],
            "allow_listed": ALLOWED_READERS,
        },
        "passed": not unexpected and not ranking_readers,
        "source": "static_code_scan",
    }


def run_backtest(market: str, horizon: str, *, limit: int | None = None,
                 source: str = "postgres") -> dict:
    """
    Legacy confidence-bucket report.

    Rewritten to use the SAME batched price panel as the audit: it no longer
    makes one provider request per observation. Retained because the bucket
    view is a useful human-readable sanity check, but it is NOT the audit and
    produces no claim levels.
    """
    from services.alpha_engine import audit_prices

    c = _contract()
    raw, _meta = load_population(source, market, horizon, limit=limit)
    rows = prepare_rows(raw, market, horizon)
    symbols = sorted({r["symbol"] for r in rows})
    earliest = min(_to_date(r["reference_session_date"]) for r in rows)
    snapshot = audit_prices.fetch_panel(
        {market: symbols}, earliest - _dt.timedelta(days=7), _dt.date.today())
    resolve_returns(rows, market, horizon, snapshot)

    observations = [
        {"confidence": r["signal_confidence"],
         "realized_return_pct": r[c.RESEARCH_PRIOR_CLOSE]}
        for r in rows if _finite(r.get(c.RESEARCH_PRIOR_CLOSE))
    ]
    log.info("[conviction_gate_backtest] %s/%s: fetched=%d resolved=%d "
             "unique_symbols=%d provider_requests=%d",
             market, horizon, len(rows), len(observations), len(symbols),
             snapshot.meta["requests_made"])
    return compute_bucket_stats(observations)


# --------------------------------------------------------------------------
# AUDIT ASSEMBLY
# --------------------------------------------------------------------------


def prepare_rows(raw: list[dict], market: str, horizon: str) -> list[dict]:
    """Attach identity, policy era and derived date strings to every row."""
    c = _contract()
    rows = []
    for r in raw:
        rec = dict(r)
        rec["market"] = market
        rec["horizon"] = horizon
        rec["run_session_date_iso"] = _to_date(r["run_session_date"]).isoformat()
        rec["reference_session_date_iso"] = _to_date(r["reference_session_date"]).isoformat()
        rec["canonical_key"] = canonical_key(market, horizon, r["run_id"], r["symbol"])
        rec["policy_era"] = c.policy_era(market, r["run_session_date"])
        rec["populations"] = assign_populations(r)
        rows.append(rec)
    within_run_rank_percentile(rows)
    return rows


def resolve_returns(rows: list[dict], market: str, horizon: str, snapshot,
                    *, today=None) -> None:
    """Resolve BOTH measures for every row from the SAME frozen snapshot."""
    c = _contract()
    for r in rows:
        research, r_reason, r_prov = resolve_research_return(
            r, market, horizon, snapshot, today=today)
        exe, e_reason, e_prov = resolve_executable_return(
            r, market, horizon, snapshot, today=today)
        r[c.RESEARCH_PRIOR_CLOSE] = research
        r[c.EXECUTABLE_NEXT_OPEN] = exe
        r["research_excluded_reason"] = r_reason
        r["executable_excluded_reason"] = e_reason
        r["research_provenance"] = r_prov
        r["executable_provenance"] = e_prov


def build_decisions(rows: list[dict]) -> list[dict]:
    """
    One decision record per row, carrying EVERY field needed to reconstruct
    every aggregate the audit reports — population membership, policy era,
    matching inputs, ranking fields, entry/exit provenance, the return, the
    win/loss flag, and each analysis's inclusion/exclusion reason.

    If an aggregate cannot be rebuilt from this file, the audit has failed its
    own reproducibility requirement and `verify_reconstruction` says so.
    """
    c = _contract()
    out = []
    for r in rows:
        research = r.get(c.RESEARCH_PRIOR_CLOSE)
        exe = r.get(c.EXECUTABLE_NEXT_OPEN)
        out.append({
            "canonical_key": r["canonical_key"],
            "run_id": r["run_id"],
            "market": r["market"],
            "horizon": r["horizon"],
            "symbol": r["symbol"],
            "run_generated_at": str(r.get("run_generated_at")),
            "run_session_date": r["run_session_date_iso"],
            "reference_session_date": r["reference_session_date_iso"],
            "policy_era": r["policy_era"],
            "signal": r.get("signal"),
            "signal_confidence": r.get("signal_confidence"),
            "is_daily_pick": bool(r.get("is_daily_pick")),
            "pick_rank": r.get("pick_rank"),
            "populations": r["populations"],
            "reference_price": r.get("reference_price"),
            # --- ranking fields (contract requirement) -------------------
            "ranking_alpha": r.get("ranking_alpha"),
            "rank_percentile": r.get("rank_percentile"),
            "rank_quantile": r.get("rank_quantile"),
            "rank_tie_handling": r.get("rank_tie_handling"),
            "rank_tied_with": r.get("rank_tied_with"),
            "ranking_alpha_higher_is_better": RANKING_ALPHA_HIGHER_IS_BETTER,
            # --- measure A ------------------------------------------------
            "research_return_pct": research,
            "research_is_win": (float(research) > 0.0) if _finite(research) else None,
            "research_excluded_reason": r.get("research_excluded_reason"),
            "research_provenance": r.get("research_provenance"),
            # --- measure B ------------------------------------------------
            "executable_return_pct": exe,
            "executable_is_win": (float(exe) > 0.0) if _finite(exe) else None,
            "executable_excluded_reason": r.get("executable_excluded_reason"),
            "executable_provenance": r.get("executable_provenance"),
            # --- published/unpublished matching inputs --------------------
            "pub_unpub_group": (
                c.P_PUBLISHED if c.P_PUBLISHED in r["populations"]
                else c.P_UNPUBLISHED_BUY if c.P_UNPUBLISHED_BUY in r["populations"]
                else None),
            "gross_of_transaction_costs": True,
        })
    return out


def build_audit(market: str, horizon: str, rows: list[dict], *, seed: int,
                permutation_draws: int = 2000,
                bootstrap_draws: int | None = None) -> dict:
    """
    Full audit for one market x horizon, with an exactly-reconciling waterfall.

    Every row produces exactly one decision record. `included` and `excluded`
    are counted independently and then checked against the fetched total via
    `audit_contract.assert_reconciles`, which RAISES on a mismatch — the audit
    refuses to report a denominator it cannot account for.
    """
    from services.alpha_engine import audit_prices

    c = _contract()
    decisions = build_decisions(rows)

    waterfall = {}
    missingness = {}
    for measure, reason_key in ((c.RESEARCH_PRIOR_CLOSE, "research_excluded_reason"),
                                (c.EXECUTABLE_NEXT_OPEN, "executable_excluded_reason")):
        included = sum(1 for r in rows if _finite(r.get(measure)))
        excluded = len(rows) - included
        c.assert_reconciles(f"{market}/{horizon}/{measure}", len(rows), included, excluded)
        reasons: dict[str, int] = {}
        for r in rows:
            reason = r.get(reason_key)
            if not _finite(r.get(measure)):
                reasons[reason or "unknown"] = reasons.get(reason or "unknown", 0) + 1
        waterfall[measure] = {
            "fetched": len(rows), "included": included, "excluded": excluded,
            "exclusion_reasons": dict(sorted(reasons.items())),
            "by_policy_era": _era_waterfall(rows, measure),
        }
        flat = [{
            "market": r["market"], "horizon": r["horizon"],
            "reference_session_date": r["run_session_date_iso"],
            "comparison_group": _comparison_group(r),
            "resolved": _finite(r.get(measure)),
        } for r in rows]
        report = audit_prices.missingness_report(flat, resolved_key="resolved")
        # Recorded, not raised here: the CLI applies the guard across the
        # whole run so a single cell cannot silently pass a run-level breach.
        missingness[measure] = audit_prices.enforce_missingness(
            report, raise_on_breach=False)

    stats: dict = {}
    n_dates = len({r["run_session_date_iso"] for r in rows})
    for measure in (c.RESEARCH_PRIOR_CLOSE, c.EXECUTABLE_NEXT_OPEN):
        stats[measure] = {
            # (A) headline: BUY against the ELIGIBLE NON-BUY population,
            # never against "chance".
            "buy_vs_non_buy": compare_populations(
                f"{market}/{horizon}/BUY-vs-NON_BUY", rows,
                c.P_BUY, c.P_NON_BUY, measure, seed=seed, n_runs=n_dates,
                permutation_draws=permutation_draws,
                bootstrap_draws=bootstrap_draws),
            # (B) the conviction gate, tested WITHIN BUY only.
            "conviction_within_buy": compare_populations(
                f"{market}/{horizon}/BUY>=85-vs-BUY<85", rows,
                c.P_BUY_HIGH_CONV, c.P_BUY_LOW_CONV, measure, seed=seed,
                n_runs=n_dates, permutation_draws=permutation_draws,
                bootstrap_draws=bootstrap_draws),
            # (D) run-matched, era-pure published vs unpublished.
            "published_vs_unpublished_buy": published_vs_unpublished(
                rows, measure, seed=seed, market=market, horizon=horizon,
                permutation_draws=permutation_draws,
                bootstrap_draws=bootstrap_draws),
            # (C) does ranking add information beyond the binary BUY call?
            "ranking_lift": ranking_lift(rows, measure, seed=seed,
                                         permutation_draws=permutation_draws),
            # (E) how concentrated is the sample the claims rest on?
            "concentration": concentration_report(rows, measure),
        }

    return {"market": market, "horizon": horizon, "rows": rows,
            "decisions": decisions, "waterfall": waterfall,
            "missingness": missingness, "statistics": stats}


def _comparison_group(row) -> str:
    c = _contract()
    if c.P_PUBLISHED in row["populations"]:
        return "PUBLISHED"
    if c.P_BUY_HIGH_CONV in row["populations"]:
        return "BUY_HIGH_CONV"
    if c.P_BUY_LOW_CONV in row["populations"]:
        return "BUY_LOW_CONV"
    return "NON_BUY"


def _era_waterfall(rows, measure) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        slot = out.setdefault(r["policy_era"], {"fetched": 0, "included": 0})
        slot["fetched"] += 1
        slot["included"] += bool(_finite(r.get(measure)))
    for slot in out.values():
        slot["excluded"] = slot["fetched"] - slot["included"]
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# PRE-REGISTERED FAMILY AND HOLM CORRECTION
# --------------------------------------------------------------------------

PRIMARY_ANALYSES = ("buy_vs_non_buy", "conviction_within_buy")


def collect_primary_family(audits: list[dict]) -> dict[str, float | None]:
    """
    The COMPLETE pre-registered primary family for the WHOLE audit run.

    Every market x horizon x measure x primary comparison that the run
    produced enters the family — including each policy era's published-vs-
    unpublished test — so the Holm correction reflects everything actually
    tested, not merely whatever one CLI invocation happened to compute. This
    is why the CLI runs all requested cells before correcting anything.

    A comparison with no p-value (NOT_IDENTIFIABLE, or inference not
    permitted) is carried through untested and never consumes family budget.
    """
    family: dict[str, float | None] = {}
    for a in audits:
        cell = f'{a["market"]}/{a["horizon"]}'
        for measure, block in a["statistics"].items():
            for name in PRIMARY_ANALYSES:
                family[f"{cell}/{measure}/{name}"] = block[name].get("block_p_value")
            for era, res in block["published_vs_unpublished_buy"]["by_policy_era"].items():
                family[f"{cell}/{measure}/published_vs_unpublished_buy/{era}"] = \
                    res.get("block_p_value")
            # The ranking-lift trend test is a PRE-REGISTERED primary question
            # ("does the ranking add information beyond the binary BUY
            # call?"), so it consumes family budget like any other. Leaving it
            # out would let it clear an uncorrected bar the others must clear
            # corrected.
            family[f"{cell}/{measure}/ranking_lift_trend"] = \
                (block["ranking_lift"].get("trend_test") or {}).get("p_dual_one_way_max")
    return family


def apply_holm(audits: list[dict], family: dict) -> dict:
    """Apply Holm across the complete family; a failure can only DOWNGRADE."""
    from services.alpha_engine import audit_stats

    c = _contract()
    corrected = audit_stats.holm_correction(family)

    def attach(res, key):
        adj = corrected.get(key, {})
        res["holm"] = adj
        if res.get("claim_level") == c.PROVEN and not adj.get("reject"):
            res["claim_level"] = c.PRELIMINARY
            res.setdefault("notes", []).append(
                "Did not survive Holm correction across the complete "
                "pre-registered family — downgraded from PROVEN to PRELIMINARY.")

    for a in audits:
        cell = f'{a["market"]}/{a["horizon"]}'
        for measure, block in a["statistics"].items():
            for name in PRIMARY_ANALYSES:
                attach(block[name], f"{cell}/{measure}/{name}")
            for era, res in block["published_vs_unpublished_buy"]["by_policy_era"].items():
                attach(res, f"{cell}/{measure}/published_vs_unpublished_buy/{era}")
            rl = block["ranking_lift"]
            adj = corrected.get(f"{cell}/{measure}/ranking_lift_trend", {})
            rl["holm"] = adj
            if rl.get("claim_level") in (c.PROVEN, c.PRELIMINARY) and \
                    adj.get("raw_p") is not None and not adj.get("reject"):
                rl["claim_level"] = c.NOT_PROVEN
                rl["reason"] = (
                    "Rank->outcome trend did not survive Holm correction "
                    "across the complete pre-registered family.")
    return corrected


# --------------------------------------------------------------------------
# RECONSTRUCTION VERIFICATION
# --------------------------------------------------------------------------


class ReconstructionError(RuntimeError):
    """Raised when a reported aggregate cannot be rebuilt from the row file."""


def verify_reconstruction(decisions: list[dict], audits: list[dict],
                          *, tolerance: float = 1e-9) -> dict:
    """
    Independently rebuild every headline aggregate FROM THE ROW FILE ALONE and
    compare it against what the audit reported.

    This is the reproducibility proof: it uses only fields present in
    `row_decisions.jsonl`, deliberately re-deriving them rather than reusing
    any in-memory value. A mismatch RAISES — a bundle whose aggregates cannot
    be reconstructed is not evidence.
    """
    c = _contract()
    by_cell: dict[str, list[dict]] = {}
    for d in decisions:
        by_cell.setdefault(f'{d["market"]}/{d["horizon"]}', []).append(d)

    checks, failures = [], []
    for a in audits:
        cell = f'{a["market"]}/{a["horizon"]}'
        rows = by_cell.get(cell, [])
        for measure, ret_key, win_key, excl_key in (
            (c.RESEARCH_PRIOR_CLOSE, "research_return_pct", "research_is_win",
             "research_excluded_reason"),
            (c.EXECUTABLE_NEXT_OPEN, "executable_return_pct", "executable_is_win",
             "executable_excluded_reason"),
        ):
            wf = a["waterfall"][measure]
            rebuilt_included = sum(1 for r in rows if r[ret_key] is not None)
            rebuilt_excluded = sum(1 for r in rows if r[ret_key] is None)
            for label, rebuilt, reported in (
                ("fetched", len(rows), wf["fetched"]),
                ("included", rebuilt_included, wf["included"]),
                ("excluded", rebuilt_excluded, wf["excluded"]),
            ):
                ok = rebuilt == reported
                checks.append({"cell": cell, "measure": measure,
                               "aggregate": f"waterfall.{label}",
                               "rebuilt": rebuilt, "reported": reported, "ok": ok})
                if not ok:
                    failures.append(checks[-1])

            # Exclusion reasons must also rebuild exactly.
            reasons: dict[str, int] = {}
            for r in rows:
                if r[ret_key] is None:
                    k = r[excl_key] or "unknown"
                    reasons[k] = reasons.get(k, 0) + 1
            ok = reasons == wf["exclusion_reasons"]
            checks.append({"cell": cell, "measure": measure,
                           "aggregate": "waterfall.exclusion_reasons",
                           "rebuilt": reasons, "reported": wf["exclusion_reasons"],
                           "ok": ok})
            if not ok:
                failures.append(checks[-1])

            # Win rates and n for each primary comparison.
            block = a["statistics"][measure]
            for name, pop_a, pop_b in (
                ("buy_vs_non_buy", c.P_BUY, c.P_NON_BUY),
                ("conviction_within_buy", c.P_BUY_HIGH_CONV, c.P_BUY_LOW_CONV),
            ):
                res = block[name]
                sel = [r for r in rows if r[ret_key] is not None]
                n_a = sum(1 for r in sel if pop_a in r["populations"])
                n_b = sum(1 for r in sel if pop_b in r["populations"]
                          and pop_a not in r["populations"])
                w_a = sum(1 for r in sel if pop_a in r["populations"] and r[win_key])
                w_b = sum(1 for r in sel if pop_b in r["populations"]
                          and pop_a not in r["populations"] and r[win_key])
                rate_a = (w_a / n_a) if n_a else None
                rate_b = (w_b / n_b) if n_b else None
                for label, rebuilt, reported in (
                    ("n_a", n_a, res["n_a"]), ("n_b", n_b, res["n_b"]),
                    ("rate_a", rate_a, res["rate_a"]),
                    ("rate_b", rate_b, res["rate_b"]),
                ):
                    ok = _close(rebuilt, reported, tolerance)
                    checks.append({"cell": cell, "measure": measure,
                                   "aggregate": f"{name}.{label}",
                                   "rebuilt": rebuilt, "reported": reported, "ok": ok})
                    if not ok:
                        failures.append(checks[-1])

            # Ranking-lift quantile counts and win rates.
            rl = block["ranking_lift"]
            if "quantiles" in rl:
                sel = [r for r in rows if r[ret_key] is not None
                       and r["rank_quantile"] is not None
                       and c.P_BUY in r["populations"]]
                for q in rl["quantiles"]:
                    bucket = [r for r in sel if r["rank_quantile"] == q["quantile"]]
                    n = len(bucket)
                    wr = (sum(1 for r in bucket if r[win_key]) / n) if n else None
                    for label, rebuilt, reported in (
                        (f'q{q["quantile"]}.n', n, q["n"]),
                        (f'q{q["quantile"]}.win_rate', wr, q["win_rate"]),
                    ):
                        ok = _close(rebuilt, reported, tolerance)
                        checks.append({"cell": cell, "measure": measure,
                                       "aggregate": f"ranking_lift.{label}",
                                       "rebuilt": rebuilt, "reported": reported,
                                       "ok": ok})
                        if not ok:
                            failures.append(checks[-1])

    out = {"n_checks": len(checks), "n_failed": len(failures),
           "failures": failures[:50], "passed": not failures,
           "note": ("Every aggregate above was rebuilt using ONLY fields "
                    "present in row_decisions.jsonl.")}
    if failures:
        raise ReconstructionError(
            f"{len(failures)} reported aggregate(s) could not be reconstructed "
            f"from row_decisions.jsonl; first failure: {failures[0]}")
    return out


def _close(a, b, tol) -> bool:
    if a is None or b is None:
        return a is b or a == b
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


# --------------------------------------------------------------------------
# EVIDENCE BUNDLE
# --------------------------------------------------------------------------


class BundleDirectoryNotEmpty(RuntimeError):
    """Raised when an evidence bundle would be written into a non-empty directory."""


# Files a previous invocation would have left behind. Their presence means the
# directory already holds a bundle.
BUNDLE_FILES = (
    "aggregate_summary.json", "statistical_results.json", "row_decisions.jsonl",
    "run_manifest.json", "data_integrity_results.json",
    "reconstruction_verification.json", "multiple_testing_family.json",
)


def write_audit_bundle(out_dir, audits: list[dict], *, manifest: dict,
                       integrity: dict, reconstruction: dict, holm: dict):
    """
    Write the evidence bundle to a caller-supplied EMPTY directory OUTSIDE the
    repository. ONE BUNDLE == ONE INVOCATION, enforced here.

    WHY MERGING WAS REMOVED. An earlier version merged additively: per-cell
    aggregates and row decisions from previous invocations were retained, but
    the RUN-LEVEL artefacts — the manifest, the data-integrity results, the
    reconstruction verification, the price-snapshot identity and the Holm
    family correction — were OVERWRITTEN with only the latest invocation's
    data. The result was a bundle whose cells came from several runs while its
    provenance, its integrity evidence and its multiple-testing correction
    described just one of them. Holm in particular is meaningless that way:
    the correction would silently cover a strict subset of the tests the
    bundle actually contains, understating the family size and overstating
    significance.

    Two contracts could fix that: per-cell provenance, or one-invocation
    bundles. This audit takes the second, because it is the one that cannot be
    got subtly wrong — every closure bundle is generated from an EMPTY
    directory in ONE invocation covering every market and measure, so the
    manifest, integrity results and Holm family always describe exactly the
    cells present. A non-empty target directory is a HARD FAILURE, not a
    warning.
    """
    import json
    import pathlib

    c = _contract()
    out = pathlib.Path(out_dir).expanduser().resolve()
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if out == repo_root or repo_root in out.parents:
        raise ValueError(
            f"refusing to write the audit bundle inside the repository "
            f"({out}); pass --audit-out pointing somewhere outside {repo_root}")
    if out.exists():
        existing = sorted(p.name for p in out.iterdir())
        clash = [n for n in existing if n in BUNDLE_FILES]
        if clash:
            raise BundleDirectoryNotEmpty(
                f"{out} already contains bundle artefact(s) {clash}. A closure "
                f"bundle must be produced from an EMPTY directory in ONE "
                f"invocation covering every market and measure, so that the "
                f"manifest, the data-integrity results and the Holm family "
                f"correction describe exactly the cells present. Merging a "
                f"second invocation in would keep the earlier cells but "
                f"overwrite run-level provenance and under-count the Holm "
                f"family. Point --audit-out at a fresh directory.")
    out.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (out / name).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    cells = [f'{a["market"]}/{a["horizon"]}' for a in audits]

    # --- aggregate summary + statistics: additive per cell ----------------
    agg: dict = {}
    stat: dict = {}
    for a in audits:
        key = f'{a["market"]}/{a["horizon"]}'
        agg[key] = {"waterfall": a["waterfall"], "missingness": a["missingness"]}
        stat[key] = a["statistics"]
    dump("aggregate_summary.json", agg)
    dump("statistical_results.json", stat)

    # --- row decisions: this invocation's cells, and only those -----------
    rows_path = out / "row_decisions.jsonl"
    with rows_path.open("w", encoding="utf-8") as fh:
        for a in audits:
            for d in a["decisions"]:
                fh.write(json.dumps(d, default=str) + "\n")

    # --- manifest: this invocation's cells and provenance, nothing else ---
    manifest = dict(manifest)
    manifest["cells"] = cells
    manifest["cells_written_this_run"] = cells
    manifest["single_invocation_bundle"] = True
    manifest["bundle_contract"] = (
        "ONE BUNDLE == ONE INVOCATION. This directory was empty when the run "
        "started, so the manifest, data-integrity results, price-snapshot "
        "identity and Holm family correction all describe exactly the cells "
        "listed here. Multi-invocation bundles are prohibited.")
    manifest["return_measures"] = c.RETURN_MEASURES
    manifest["populations"] = list(c.POPULATIONS)
    manifest["claim_levels"] = list(c.CLAIM_LEVELS)
    manifest["policy_era_boundaries"] = {
        m: {e: d.isoformat() for e, d in b.items()}
        for m, b in c.POLICY_ERA_BOUNDARIES.items()}
    manifest["gross_of_costs"] = True
    manifest["cost_model_implemented"] = False
    manifest["transaction_costs_and_taxes"] = "EXPLICITLY DEFERRED — not implemented"
    manifest["permanently_not_reproducible"] = list(c.PERMANENTLY_NOT_REPRODUCIBLE)
    manifest["read_only"] = True
    dump("run_manifest.json", manifest)

    dump("data_integrity_results.json", integrity)
    dump("reconstruction_verification.json", reconstruction)
    dump("multiple_testing_family.json", holm)
    return out


def build_manifest(*, seed, source_meta, snapshot_info, cells, row_counts,
                   cutoff, permutation_draws, draws) -> dict:
    """
    Every field a reader needs to reproduce this exact run.

    Deliberately exhaustive: commit SHA, calculation version, seed, the exact
    query and cutoff, the run timestamp, a NON-SECRET source identifier, the
    installed package versions, the calendar version and the pinned provider
    parameters, the price-snapshot checksum, and row counts.
    """
    import hashlib
    import platform
    import subprocess

    from services.alpha_engine import audit_calendar, audit_prices

    c = _contract()

    def version(mod):
        try:
            m = __import__(mod)
            return getattr(m, "__version__", "unknown")
        except Exception:  # noqa: BLE001
            return "not-installed"

    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        sha = "unknown"

    return {
        "commit_sha": sha,
        "calculation_version": c.CALCULATION_VERSION,
        "random_seed": seed,
        "bootstrap_draws": draws,
        "permutation_draws": permutation_draws,
        "run_timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "population_query": (
            "SELECT observation_id, run_id, symbol, run_generated_at, "
            "run_session_date, reference_session_date, reference_price, "
            "signal_confidence, signal, ranking_alpha, is_daily_pick, pick_rank "
            "FROM alpha_observations WHERE market = %s AND horizon = %s "
            "AND signal_confidence IS NOT NULL ORDER BY reference_session_date ASC"),
        "population_cutoff_utc": cutoff,
        "source": source_meta,
        "cells": cells,
        "row_counts": row_counts,
        "row_identity": "(market, horizon, run_id, symbol)",
        "price_snapshot": snapshot_info,
        "price_provider": audit_prices.PROVIDER,
        "price_provider_params": audit_prices.PROVIDER_PARAMS,
        "calendar_library": "pandas_market_calendars",
        "calendar_version": version("pandas_market_calendars"),
        "calendars_used": dict(audit_calendar._MARKET_CALENDAR),
        "package_versions": {
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "scipy": version("scipy"),
            "pandas": version("pandas"),
            "yfinance": version("yfinance"),
            "pandas_market_calendars": version("pandas_market_calendars"),
        },
        "horizon_trading_days": HORIZON_TRADING_DAYS,
        "manifest_schema": "conviction-audit-manifest-2",
        "_hash_of_query": hashlib.sha256(b"alpha_observations").hexdigest()[:12],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _walk_comparisons(audits: list[dict]):
    """Yield every per-comparison result dict in a run, from every cell."""
    for a in audits:
        for block in a["statistics"].values():
            for name in PRIMARY_ANALYSES:
                yield block[name]
            yield from block["published_vs_unpublished_buy"]["by_policy_era"].values()


def verify_draws_propagated(audits: list[dict], *, bootstrap_draws: int,
                            permutation_draws: int) -> dict:
    """
    Prove the CLI's draw counts actually reached the implementations.

    Reads back `bootstrap_draws_executed` / `permutation_draws_executed`,
    which the bootstrap and the permutation record AT THE POINT OF USE, and
    fails the run if any executed comparison used a different count from the
    one the manifest is about to declare. A comparison that never ran (an
    empty population, or NOT_IDENTIFIABLE before any draw) records None and is
    counted separately rather than treated as a mismatch.
    """
    boot_seen, perm_seen, checked, skipped = set(), set(), 0, 0
    for res in _walk_comparisons(audits):
        b = res.get("bootstrap_draws_executed")
        p = res.get("permutation_draws_executed")
        if b is None and p is None:
            skipped += 1
            continue
        checked += 1
        if b is not None:
            boot_seen.add(b)
        if p is not None:
            perm_seen.add(p)
    bad = []
    if boot_seen - {bootstrap_draws}:
        bad.append(f"bootstrap draws executed {sorted(boot_seen)} != declared "
                   f"{bootstrap_draws}")
    if perm_seen - {permutation_draws}:
        bad.append(f"permutation draws executed {sorted(perm_seen)} != declared "
                   f"{permutation_draws}")
    if bad:
        raise RuntimeError(
            "manifest would misreport the draw counts actually used: "
            + "; ".join(bad))
    return {
        "declared_bootstrap_draws": bootstrap_draws,
        "declared_permutation_draws": permutation_draws,
        "bootstrap_draws_observed": sorted(boot_seen),
        "permutation_draws_observed": sorted(perm_seen),
        "comparisons_checked": checked,
        "comparisons_with_no_draws_executed": skipped,
        "verified": True,
    }


def run_full_audit(markets, horizons, *, source, out_dir, seed,
                   permutation_draws=2000, draws=10000, today=None,
                   snapshot_path=None, snapshot_in=None, limit=None,
                   enforce_missingness=True):
    """
    Run every requested market x horizon cell in ONE invocation, correct
    across the COMPLETE family, and write one mergeable evidence bundle.
    """
    from services.alpha_engine import audit_prices

    cutoff = _dt.datetime.now(_dt.timezone.utc).isoformat()
    cells, source_meta = [], {}
    prepared: dict[str, list[dict]] = {}
    for market in markets:
        for horizon in horizons:
            raw, meta = load_population(source, market, horizon, limit=limit)
            source_meta = source_meta or meta
            prepared[f"{market}/{horizon}"] = prepare_rows(raw, market, horizon)
            cells.append(f"{market}/{horizon}")
            log.info("[conviction_audit] %s/%s: %d rows loaded",
                     market, horizon, len(prepared[f"{market}/{horizon}"]))

    # --- ONE price panel for every unique symbol, fetched once ------------
    if snapshot_in:
        snapshot = audit_prices.PriceSnapshot.load(snapshot_in)
        snapshot_info = {"path": str(snapshot_in),
                         "sha256": snapshot.meta.get("loaded_sha256"),
                         "reused": True}
    else:
        by_market: dict[str, set] = {}
        earliest, latest = None, None
        for key, rows in prepared.items():
            market = key.split("/")[0]
            for r in rows:
                by_market.setdefault(market, set()).add(r["symbol"])
                d = _to_date(r["reference_session_date"])
                earliest = d if earliest is None or d < earliest else earliest
                latest = d if latest is None or d > latest else latest
        start = earliest - _dt.timedelta(days=7)
        end = (today or _dt.date.today())
        log.info("[conviction_audit] fetching ONE price panel: %d unique symbols, %s..%s",
                 sum(len(v) for v in by_market.values()), start, end)
        snapshot = audit_prices.fetch_panel(
            {m: sorted(v) for m, v in by_market.items()}, start, end)
        snapshot_info = snapshot.save(snapshot_path) if snapshot_path else {
            "path": None, "sha256": None, "note": "snapshot not persisted"}
        snapshot_info["symbols_requested"] = snapshot.meta["symbols_requested"]
        snapshot_info["symbols_returned"] = snapshot.meta["symbols_returned"]
        snapshot_info["symbols_failed"] = snapshot.meta["symbols_failed"]
        snapshot_info["requests_made"] = snapshot.meta["requests_made"]

    audits = []
    for key, rows in prepared.items():
        market, horizon = key.split("/")
        resolve_returns(rows, market, horizon, snapshot, today=today)
        audits.append(build_audit(market, horizon, rows, seed=seed,
                                  permutation_draws=permutation_draws,
                                  bootstrap_draws=draws))

    # Run-level missingness guard: applied ACROSS the whole run.
    if enforce_missingness:
        for a in audits:
            for measure, rep in a["missingness"].items():
                if not rep["passed"]:
                    raise audit_prices.MissingnessAbort(
                        f'{a["market"]}/{a["horizon"]}/{measure}: '
                        + "; ".join(rep["breaches"]))

    family = collect_primary_family(audits)
    holm = apply_holm(audits, family)

    integrity = run_data_integrity_checks(
        {f'{a["market"]}/{a["horizon"]}': a["rows"] for a in audits}, source_meta)

    all_decisions = [d for a in audits for d in a["decisions"]]
    reconstruction = verify_reconstruction(all_decisions, audits)

    # The manifest may only state a draw count the run ACTUALLY executed.
    # An earlier version recorded --bootstrap-draws without ever passing it
    # down, so the manifest documented a value the bootstrap had not used.
    executed = verify_draws_propagated(audits, bootstrap_draws=draws,
                                       permutation_draws=permutation_draws)

    manifest = build_manifest(
        seed=seed, source_meta=source_meta, snapshot_info=snapshot_info,
        cells=cells, row_counts={k: len(v) for k, v in prepared.items()},
        cutoff=cutoff, permutation_draws=permutation_draws, draws=draws)
    manifest["draws_propagation_verified"] = executed

    dest = write_audit_bundle(out_dir, audits, manifest=manifest,
                              integrity=integrity, reconstruction=reconstruction,
                              holm={"family": family, "holm": holm})
    return {"dest": dest, "audits": audits, "integrity": integrity,
            "reconstruction": reconstruction, "manifest": manifest,
            "family": family, "holm": holm}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=MARKETS,
                        help="legacy single-cell bucket report")
    parser.add_argument("--horizon", choices=HORIZONS,
                        help="legacy single-cell bucket report")
    parser.add_argument("--markets", nargs="+", choices=MARKETS,
                        help="run these markets in ONE invocation (audit mode)")
    parser.add_argument("--horizons", nargs="+", choices=HORIZONS,
                        help="run these horizons in ONE invocation (audit mode)")
    parser.add_argument("--source", default="postgres",
                        help="'postgres' (DATABASE_URL) or 'extract:<path>' "
                             "for a frozen, checksummed read-only extract")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--audit-out", default=None,
                        help="write the audit bundle here (must be OUTSIDE the repo)")
    parser.add_argument("--price-snapshot-out", default=None,
                        help="persist the immutable price panel here (outside the repo)")
    parser.add_argument("--price-snapshot-in", default=None,
                        help="reuse a previously saved price panel")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--permutation-draws", type=int, default=2000)
    parser.add_argument("--bootstrap-draws", type=int, default=10000)
    parser.add_argument("--today", default=None,
                        help="ISO date used as 'today' for window completeness")
    parser.add_argument("--allow-missingness-breach", action="store_true",
                        help="record rather than abort on a missingness breach "
                             "(for diagnosis only; never for a closure run)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.audit_out:
        from services.alpha_engine import audit_stats

        seed = args.seed if args.seed is not None else audit_stats.DEFAULT_SEED
        markets = args.markets or ([args.market] if args.market else list(MARKETS))
        horizons = args.horizons or ([args.horizon] if args.horizon else ["short"])
        today = _dt.date.fromisoformat(args.today) if args.today else None
        result = run_full_audit(
            markets, horizons, source=args.source, out_dir=args.audit_out,
            seed=seed, permutation_draws=args.permutation_draws,
            draws=args.bootstrap_draws, today=today,
            snapshot_path=args.price_snapshot_out,
            snapshot_in=args.price_snapshot_in, limit=args.limit,
            enforce_missingness=not args.allow_missingness_breach)
        print(f"[conviction_audit] bundle written to {result['dest']}")
        print(f"[conviction_audit] integrity: "
              f"{result['integrity']['_summary']['n_passed']}/"
              f"{result['integrity']['_summary']['n_checks']} checks passed"
              + (f" FAILED={result['integrity']['_summary']['n_failed']}"
                 if result['integrity']['_summary']['n_failed'] else ""))
        print(f"[conviction_audit] reconstruction: "
              f"{result['reconstruction']['n_checks']} aggregates rebuilt from "
              f"row_decisions.jsonl, {result['reconstruction']['n_failed']} failed")
        for a in result["audits"]:
            for measure, wf in a["waterfall"].items():
                print(f"  {a['market']}/{a['horizon']}/{measure}: "
                      f"fetched={wf['fetched']} included={wf['included']} "
                      f"excluded={wf['excluded']}")
        return 0

    if not (args.market and args.horizon):
        parser.error("legacy report mode needs --market and --horizon")
    stats = run_backtest(args.market, args.horizon, limit=args.limit)
    print(format_report({args.horizon: stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
