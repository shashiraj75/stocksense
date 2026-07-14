"""
Phase 1A.6 Steps 6-10 — dry-run-only historical market-contamination repair
planner.

Why this exists: the Phase 1A.5 forensic investigation found a population
of legacy predictions stored with market='IN' whose symbol is a
definitively US-only ticker (a defect fixed at commit 536fd3d — see
services/market_integrity.py's module docstring). This script classifies
every one of those legacy rows into a repair category and freezes the
classification into a versioned, checksummed JSON plan for human review.

This script NEVER writes to any table and has no --execute/--apply/--write
mode of any kind — planning (and, separately, offline verification of an
already-saved plan file) only. Actually repairing the legacy rows is
explicitly out of scope for this phase and is a separate, future,
human-approved action.

Connection: read-only audit role only (AUDIT_DATABASE_URL, sourced from
~/.stocksense360_audit_env) — the same reader used for the Phase 1A.5
investigation (default_transaction_read_only=on at the role level; this
script also explicitly sets conn.read_only = True as defense-in-depth).
Never touches DATABASE_URL / the writer connection. --verify mode makes
NO database connection of any kind — it only re-validates an already
saved JSON file's own internal structure and checksum.

Classification categories, in this precedence order (see
classify_candidate()) — evaluated top to bottom, first match wins:
  1. UNCLASSIFIABLE               Insufficient/unclassifiable evidence:
                                   missing price/pred_date, or the symbol's
                                   current canonical classification is no
                                   longer definitively US_ONLY.
  2. QUARANTINE_RECOMMENDED       A material downstream dependency exists
                                   (see downstream_dependencies) — checked
                                   BEFORE duplicate matching, so a row with
                                   a user-facing or downstream dependency
                                   is never presented as automatically safe
                                   merely because a duplicate match also
                                   exists.
  3. EXACT_CROSS_MARKET_DUPLICATE A correctly-labelled market='US'
                                   prediction exists for the same
                                   (symbol, pred_date, price), and no
                                   dependency blocked classification above.
  4. NEAR_DUPLICATE_REVIEW_REQUIRED A market='US' prediction exists for the
                                   same (symbol, pred_date) at a different
                                   price, and no dependency blocked
                                   classification above.
  5. SAFE_RELABEL_TO_US           No duplicate, no dependency — an orphaned
                                   mislabel, safe to relabel market IN->US.

Downstream dependency evidence (see classify_candidate) carries an
explicit confidence tier per domain — DIRECT (tied to this exact row's own
key), STRONG_LOGICAL (a real but not row-level match, e.g. symbol+market),
or WEAK_LOGICAL (a symbol-only match where the underlying table lacks a
market column, e.g. score_snapshots) — so a report reader can distinguish
strong, row-level evidence from a conservative, weaker signal.

Residual risk, explicitly not resolved by this script: an earlier
forensic pass (Phase 1A.5) reported 194 exact-price and 132 near-price
duplicate matches using a methodology not fully retained in this script's
context. This script's own duplicate-matching methodology is described
above and produces its own counts when run — the two are NOT claimed to
reconcile in this phase, and reconciling them requires a separately
authorized production analysis. See RESIDUAL_RISK_NOTES below, which is
embedded verbatim in every generated plan's JSON.

Output: a byte-deterministic, checksummed plan written to the path given
by --out (default /tmp/stocksense_market_repair_plan.json). No wall-clock
timestamp, random ID, temporary path, or other environment-dependent value
is ever written into the file — identical underlying data always produces
byte-identical output. A human-readable generation timestamp is printed to
the console only, never persisted into the file. Refuses to overwrite an
existing file unless --overwrite is passed.
"""

import argparse
import json
import os
import sys
from collections import Counter
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alpha_engine.manifest_backfill import canonical_json, compute_checksum  # noqa: E402
from services.market_integrity import SymbolMarketClass, classify_symbol  # noqa: E402

PLAN_SCHEMA_VERSION = "1"
PLANNER_VERSION = "1"
SUPPORTED_PLAN_SCHEMA_VERSIONS = {"1"}
DEFAULT_OUT_PATH = "/tmp/stocksense_market_repair_plan.json"

CATEGORY_UNCLASSIFIABLE = "UNCLASSIFIABLE"
CATEGORY_QUARANTINE = "QUARANTINE_RECOMMENDED"
CATEGORY_EXACT_DUP = "EXACT_CROSS_MARKET_DUPLICATE"
CATEGORY_NEAR_DUP = "NEAR_DUPLICATE_REVIEW_REQUIRED"
CATEGORY_SAFE_RELABEL = "SAFE_RELABEL_TO_US"

# Declared in precedence order — evaluated top to bottom in classify_candidate.
ALL_CATEGORIES = (
    CATEGORY_UNCLASSIFIABLE,
    CATEGORY_QUARANTINE,
    CATEGORY_EXACT_DUP,
    CATEGORY_NEAR_DUP,
    CATEGORY_SAFE_RELABEL,
)

CONFIDENCE_DIRECT = "DIRECT"
CONFIDENCE_STRONG_LOGICAL = "STRONG_LOGICAL"
CONFIDENCE_WEAK_LOGICAL = "WEAK_LOGICAL"

# A forbidden-field guard for validate_repair_plan — any of these names
# anywhere in the plan (at any nesting depth — see _check_forbidden_fields_
# recursive) would imply an execution/mutation capability this script must
# never have.
_FORBIDDEN_ACTION_FIELDS = {"execute", "apply", "write", "repair_action", "confirm", "mutate"}

# Bounded recursion ceiling for _check_forbidden_fields_recursive — well
# above any real plan's actual nesting (plan -> candidates -> downstream_
# dependencies is 3 levels), but finite, so a pathological or malformed
# input can never cause unbounded recursion.
_MAX_VALIDATION_DEPTH = 12

RESIDUAL_RISK_NOTES = (
    "The Phase 1A.5 forensic investigation reported 194 exact-price and 132 "
    "near-price duplicate matches using a methodology not fully retained in "
    "this script's available context. This planner's own duplicate-matching "
    "methodology (documented in this file's module docstring) produces its "
    "own counts when run against live data — the two are NOT claimed to "
    "reconcile in this phase. Reconciling them requires a separately "
    "authorized production analysis before any repair execution planning.",
    "No historical repair, relabeling, deletion, or quarantine action has "
    "been executed on any row referenced by this plan. This plan is "
    "advisory only.",
    "The Phase 1A.4 manifest is invalid (a known prior defect) and must "
    "never be reused for any purpose.",
)


class RepairPlanError(Exception):
    """Refuses to produce a plan file — never silently write a partial or
    malformed plan."""


class RepairPlanValidationError(Exception):
    """A saved repair-plan JSON file fails structural, schema, or checksum
    validation — raised by validate_repair_plan, used by --verify. Never
    raised as a result of any database access."""


def _redact_database_identity(database_url: str) -> dict:
    """host/dbname only — never the credential-bearing user:password portion,
    never the full connection string."""
    parts = urlsplit(database_url)
    return {"host": parts.hostname, "port": parts.port, "dbname": (parts.path or "").lstrip("/")}


def _connect_read_only():
    import psycopg

    audit_url = os.getenv("AUDIT_DATABASE_URL", "")
    if not audit_url:
        raise RepairPlanError(
            "AUDIT_DATABASE_URL is not set — this script only ever connects via the "
            "read-only audit role, never DATABASE_URL. Source ~/.stocksense360_audit_env first."
        )
    conn = psycopg.connect(audit_url, autocommit=False)
    conn.read_only = True  # defense-in-depth; the audit role is already read-only at the DB level
    return conn, audit_url


def _load_contaminated_candidates(conn) -> list[dict]:
    """The confirmed-contaminated population: market='IN' rows whose symbol
    is definitively US_ONLY per the single shared classifier (services.
    market_integrity) — never a SQL heuristic on the symbol text. This is
    deliberately NOT "every market='IN' row": the overwhelming majority of
    IN-labelled predictions are genuine Indian-market predictions (IN_ONLY
    or BOTH symbols) and must never be pulled into this planner at all.
    Symbols currently classified as BOTH or UNKNOWN are also excluded
    here: this planner's contaminated-row definition matches the Phase
    1A.5 forensic query exactly (market='IN' AND symbol is definitively
    US-only), so only US_ONLY rows ever enter classify_candidate()."""
    rows = conn.execute("""
        SELECT id, symbol, horizon, market, price, is_daily_pick,
               date(logged_at) AS pred_date
        FROM predictions
        WHERE market = 'IN'
        ORDER BY logged_at ASC, symbol ASC, id ASC
    """).fetchall()
    cols = ["prediction_id", "symbol", "horizon", "market", "price", "is_daily_pick", "pred_date"]
    all_in_rows = [dict(zip(cols, r)) for r in rows]
    return [row for row in all_in_rows if classify_symbol(row["symbol"]) == SymbolMarketClass.US_ONLY]


def _load_us_predictions_by_symbol_date(conn) -> dict:
    """symbol -> pred_date -> list of (prediction_id, price) for every
    correctly-labelled market='US' row — used to find exact/near duplicates
    of a contaminated candidate without one query per candidate."""
    rows = conn.execute("""
        SELECT symbol, date(logged_at) AS pred_date, id, price
        FROM predictions
        WHERE market = 'US'
    """).fetchall()
    index = {}
    for symbol, pred_date, pid, price in rows:
        index.setdefault(symbol, {}).setdefault(pred_date, []).append((pid, price))
    return index


def _load_outcome_keys(conn) -> set:
    """(symbol, horizon, market, pred_date) keys with an existing outcomes
    row — used to detect a contaminated candidate that already has a
    resolved (or partially resolved) outcome logged against it. DIRECT
    confidence: this is the row's own exact natural key."""
    rows = conn.execute("SELECT symbol, horizon, market, pred_date FROM outcomes").fetchall()
    return {(sym, hz, mkt, pdate) for sym, hz, mkt, pdate in rows}


def _load_paper_trade_symbol_counts(conn, market: str) -> dict:
    """symbol -> count of paper_trades rows under the given market.
    paper_trades has a market column but no foreign key back to a specific
    predictions.id, so this is a STRONG_LOGICAL (symbol+market, not
    row-level) dependency signal — deliberately conservative: any count > 0
    blocks automatic relabeling for that symbol. Never reads session_id/
    user_id/account fields."""
    rows = conn.execute(
        "SELECT symbol, COUNT(*) FROM paper_trades WHERE market = %s GROUP BY symbol", (market,)
    ).fetchall()
    return dict(rows)


def _load_score_snapshot_key_counts(conn) -> dict:
    """(symbol, horizon, snapshot_date) -> count of score_snapshots rows.
    Note: score_snapshots has no market column at all (a pre-existing
    schema gap, unrelated to this defect) so this signal cannot distinguish
    an IN-scoped snapshot from a US-scoped one — WEAK_LOGICAL confidence,
    treated conservatively (blocks automatic relabel) but clearly labeled
    as the weakest evidence tier."""
    rows = conn.execute(
        "SELECT symbol, horizon, snapshot_date, COUNT(*) FROM score_snapshots "
        "GROUP BY symbol, horizon, snapshot_date"
    ).fetchall()
    return {(sym, hz, sdate): cnt for sym, hz, sdate, cnt in rows}


def classify_candidate(candidate: dict, us_index: dict, outcome_keys: set,
                        paper_trade_counts: dict, score_snapshot_counts: dict) -> dict:
    """Pure classification — no I/O. Returns
    {category, category_reason, matched_us_prediction_id, downstream_dependencies}.
    Precedence: UNCLASSIFIABLE -> QUARANTINE (any blocking dependency) ->
    EXACT_DUP -> NEAR_DUP -> SAFE_RELABEL. See module docstring."""
    symbol = candidate["symbol"]
    pred_date = candidate["pred_date"]
    price = candidate["price"]
    horizon = candidate["horizon"]
    prediction_id = candidate["prediction_id"]

    classification = classify_symbol(symbol)
    if classification != SymbolMarketClass.US_ONLY:
        return {
            "category": CATEGORY_UNCLASSIFIABLE,
            "category_reason": (
                f"symbol is no longer definitively US_ONLY under the current canonical "
                f"universe (currently classifies as {classification.value}) — cannot "
                f"confidently place in any repair category"
            ),
            "matched_us_prediction_id": None,
            "downstream_dependencies": [],
        }

    if price is None or pred_date is None:
        return {
            "category": CATEGORY_UNCLASSIFIABLE,
            "category_reason": "missing price and/or pred_date — cannot compare against "
                                "genuine US rows or evaluate dependencies",
            "matched_us_prediction_id": None,
            "downstream_dependencies": [],
        }

    has_outcome = (symbol, horizon, "IN", pred_date) in outcome_keys
    is_daily_pick = bool(candidate.get("is_daily_pick"))
    paper_trade_count = paper_trade_counts.get(symbol, 0)
    score_snapshot_count = score_snapshot_counts.get((symbol, horizon, pred_date), 0)

    downstream_dependencies = [
        {
            "dependency_domain": "outcomes",
            "match_type": "exact_key",
            "match_key": {"symbol": symbol, "horizon": horizon, "market": "IN", "pred_date": pred_date},
            "match_count": 1 if has_outcome else 0,
            "confidence": CONFIDENCE_DIRECT,
            "blocks_automatic_relabel": has_outcome,
            "reason_code": "outcome_resolved_against_row",
        },
        {
            "dependency_domain": "daily_picks",
            "match_type": "exact_key",
            "match_key": {"prediction_id": prediction_id},
            "match_count": 1 if is_daily_pick else 0,
            "confidence": CONFIDENCE_DIRECT,
            "blocks_automatic_relabel": is_daily_pick,
            "reason_code": "published_as_daily_pick",
        },
        {
            "dependency_domain": "paper_trades",
            "match_type": "symbol_market_match",
            "match_key": {"symbol": symbol, "market": "IN"},
            "match_count": paper_trade_count,
            "confidence": CONFIDENCE_STRONG_LOGICAL,
            "blocks_automatic_relabel": paper_trade_count > 0,
            "reason_code": "paper_trade_exists_for_symbol_under_market",
        },
        {
            "dependency_domain": "score_snapshots",
            "match_type": "symbol_only_no_market_column",
            "match_key": {"symbol": symbol, "horizon": horizon, "pred_date": pred_date},
            "match_count": score_snapshot_count,
            "confidence": CONFIDENCE_WEAK_LOGICAL,
            "blocks_automatic_relabel": score_snapshot_count > 0,
            "reason_code": "score_snapshot_matches_symbol_no_market_distinction",
        },
    ]
    blocking = [d for d in downstream_dependencies if d["blocks_automatic_relabel"]]

    if blocking:
        blocked_by = ", ".join(d["dependency_domain"] for d in blocking)
        return {
            "category": CATEGORY_QUARANTINE,
            "category_reason": (
                f"blocked by downstream dependency in: {blocked_by} — a contaminated row "
                f"with a user-facing or downstream dependency is never presented as "
                f"automatically safe, even if a duplicate match also exists"
            ),
            "matched_us_prediction_id": None,
            "downstream_dependencies": downstream_dependencies,
        }

    us_matches = us_index.get(symbol, {}).get(pred_date, [])
    exact = [m for m in us_matches if m[1] == price]
    near = [m for m in us_matches if m[1] != price]

    if exact:
        return {
            "category": CATEGORY_EXACT_DUP,
            "category_reason": f"genuine market='US' prediction {exact[0][0]} exists for the "
                                f"same symbol/pred_date/price ({len(exact)} exact match(es)) — "
                                f"relabeling would create a duplicate row",
            "matched_us_prediction_id": exact[0][0],
            "downstream_dependencies": downstream_dependencies,
        }
    if near:
        return {
            "category": CATEGORY_NEAR_DUP,
            "category_reason": f"genuine market='US' prediction {near[0][0]} exists for the "
                                f"same symbol/pred_date but at a different price "
                                f"({len(near)} near match(es)) — requires human review",
            "matched_us_prediction_id": near[0][0],
            "downstream_dependencies": downstream_dependencies,
        }
    return {
        "category": CATEGORY_SAFE_RELABEL,
        "category_reason": "no genuine US duplicate and no downstream dependency found — "
                            "orphaned mislabel, safe to relabel market IN->US",
        "matched_us_prediction_id": None,
        "downstream_dependencies": downstream_dependencies,
    }


def build_repair_plan() -> dict:
    conn, audit_url = _connect_read_only()
    try:
        raw_candidates = _load_contaminated_candidates(conn)
        us_index = _load_us_predictions_by_symbol_date(conn)
        outcome_keys = _load_outcome_keys(conn)
        paper_trade_counts = _load_paper_trade_symbol_counts(conn, "IN")
        score_snapshot_counts = _load_score_snapshot_key_counts(conn)
        database_identity = _redact_database_identity(audit_url)
    finally:
        conn.close()

    candidates_out = []
    classification_counts = {c: 0 for c in ALL_CATEGORIES}
    dependency_summary = Counter()
    for cand in raw_candidates:
        result = classify_candidate(cand, us_index, outcome_keys,
                                     paper_trade_counts, score_snapshot_counts)
        classification_counts[result["category"]] += 1
        for dep in result["downstream_dependencies"]:
            if dep["blocks_automatic_relabel"]:
                dependency_summary[dep["dependency_domain"]] += 1
        candidates_out.append({
            "prediction_id": cand["prediction_id"],
            "symbol": cand["symbol"],
            "horizon": cand["horizon"],
            "stored_market": cand["market"],
            "pred_date": cand["pred_date"],
            "price": cand["price"],
            "category": result["category"],
            "category_reason": result["category_reason"],
            "matched_us_prediction_id": result["matched_us_prediction_id"],
            "downstream_dependencies": result["downstream_dependencies"],
        })

    from services.alpha_engine.manifest_backfill import _get_source_commit

    plan = {
        "manifest_version": PLAN_SCHEMA_VERSION,
        "planner_version": PLANNER_VERSION,
        "source_commit": _get_source_commit(),
        "database_identity": database_identity,
        "total_contaminated_rows": len(raw_candidates),
        "classification_counts": classification_counts,
        "dependency_summary": dict(dependency_summary),
        "canonical_serialization": "sort_keys+compact_separators+no_nan",
        "residual_risk_notes": list(RESIDUAL_RISK_NOTES),
        "candidates": candidates_out,
    }
    plan["manifest_sha256"] = compute_checksum(plan)
    return plan


def _check_forbidden_fields_recursive(obj, path: str = "root", depth: int = 0) -> None:
    """
    Recursively scans a plan's full structure — top-level fields, every
    candidate, and every nested dict/list within a candidate (in
    particular each downstream_dependencies entry) — for any key in
    _FORBIDDEN_ACTION_FIELDS, at any nesting depth. Raises
    RepairPlanValidationError on the first one found, naming the exact
    path so a reviewer can locate it. Bounded by _MAX_VALIDATION_DEPTH so a
    pathological or malformed input can never cause unbounded recursion.
    """
    if depth > _MAX_VALIDATION_DEPTH:
        raise RepairPlanValidationError(
            f"plan structure exceeds maximum validation depth ({_MAX_VALIDATION_DEPTH}) "
            f"at {path!r} — refusing to validate"
        )
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _FORBIDDEN_ACTION_FIELDS:
                raise RepairPlanValidationError(
                    f"forbidden executable/mutation-implying field {key!r} found at "
                    f"{path!r} — a repair plan must never carry a field implying an "
                    f"execution capability, at any nesting depth"
                )
            _check_forbidden_fields_recursive(value, f"{path}.{key}", depth + 1)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _check_forbidden_fields_recursive(item, f"{path}[{i}]", depth + 1)
    # scalars (str/int/float/bool/None): nothing to check


def _validate_finite(plan: dict) -> None:
    """canonical_json(..., allow_nan=False) already rejects NaN/Infinity at
    serialization time; this just gives a clearer, earlier error message."""
    for c in plan.get("candidates", []):
        price = c.get("price")
        if price is not None and (price != price or price in (float("inf"), float("-inf"))):
            raise RepairPlanError(f"candidate {c['prediction_id']} has a non-finite price — refusing to emit plan")


def validate_repair_plan(plan: dict, expected_sha256: str | None = None) -> None:
    """
    Structural, schema, and checksum validation for a saved repair-plan
    JSON file. Makes NO database connection and reads no environment
    variable of any kind. Raises RepairPlanValidationError on the first
    problem found. Used by --verify.
    """
    version = plan.get("manifest_version")
    if version not in SUPPORTED_PLAN_SCHEMA_VERSIONS:
        raise RepairPlanValidationError(f"unsupported manifest_version: {version!r}")
    if not plan.get("planner_version"):
        raise RepairPlanValidationError("missing required field: planner_version")

    required_top_level = (
        "manifest_version", "planner_version", "source_commit", "database_identity",
        "total_contaminated_rows", "classification_counts", "dependency_summary",
        "canonical_serialization", "residual_risk_notes", "manifest_sha256", "candidates",
    )
    for field in required_top_level:
        if field not in plan:
            raise RepairPlanValidationError(f"missing required top-level field: {field!r}")

    # Recursively scans the ENTIRE plan — top-level fields, every candidate,
    # and every nested dict/list within a candidate (in particular each
    # downstream_dependencies entry) — for a forbidden field at any depth.
    # Supersedes (and is strictly stronger than) checking only the plan's
    # and each candidate's own top-level keys.
    _check_forbidden_fields_recursive(plan)

    candidates = plan["candidates"]
    required_candidate_fields = (
        "prediction_id", "symbol", "horizon", "stored_market", "pred_date", "price",
        "category", "category_reason", "matched_us_prediction_id", "downstream_dependencies",
    )
    seen_ids = set()
    for c in candidates:
        for field in required_candidate_fields:
            if field not in c:
                raise RepairPlanValidationError(
                    f"candidate {c.get('prediction_id')} missing required field: {field!r}"
                )
        if c["category"] not in ALL_CATEGORIES:
            raise RepairPlanValidationError(
                f"candidate {c['prediction_id']} has unknown category: {c['category']!r}"
            )
        pid = c["prediction_id"]
        if pid in seen_ids:
            raise RepairPlanValidationError(f"duplicate prediction_id {pid} in plan")
        seen_ids.add(pid)

    if plan.get("total_contaminated_rows") != len(candidates):
        raise RepairPlanValidationError(
            f"total_contaminated_rows={plan.get('total_contaminated_rows')} does not match "
            f"actual candidates array length {len(candidates)}"
        )

    actual_counts = Counter(c["category"] for c in candidates)
    for cat in ALL_CATEGORIES:
        recorded = plan["classification_counts"].get(cat, 0)
        if recorded != actual_counts.get(cat, 0):
            raise RepairPlanValidationError(
                f"classification_counts[{cat!r}]={recorded} does not match actual count "
                f"{actual_counts.get(cat, 0)}"
            )

    _validate_finite(plan)

    recorded_sha = plan.get("manifest_sha256")
    recomputed_sha = compute_checksum(plan)
    if recorded_sha != recomputed_sha:
        raise RepairPlanValidationError(
            f"plan checksum mismatch: file claims {recorded_sha!r} but content hashes to "
            f"{recomputed_sha!r} — the plan may have been tampered with"
        )
    if expected_sha256 is not None and expected_sha256 != recorded_sha:
        raise RepairPlanValidationError(
            f"--verify checksum {expected_sha256!r} does not match this plan's own "
            f"recorded checksum {recorded_sha!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help=f"output path (default: {DEFAULT_OUT_PATH})")
    parser.add_argument("--overwrite", action="store_true", help="allow overwriting an existing file")
    parser.add_argument("--verify", metavar="PATH", default=None,
                         help="verify a saved plan file's structure and checksum, then exit. "
                              "Makes NO database connection and reads no environment variable.")
    args = parser.parse_args()

    if args.verify:
        with open(args.verify) as f:
            plan = json.load(f)
        try:
            validate_repair_plan(plan)
        except RepairPlanValidationError as e:
            print(f"PLAN VALIDATION FAILED: {e}", file=sys.stderr)
            return 3
        print("Mode: VERIFY ONLY (no database connection, no environment variables read)")
        print(f"Plan valid. manifest_sha256={plan['manifest_sha256']}")
        print(f"total_contaminated_rows={plan['total_contaminated_rows']}")
        for cat in ALL_CATEGORIES:
            print(f"  {cat}: {plan['classification_counts'].get(cat, 0)}")
        return 0

    if os.path.exists(args.out) and not args.overwrite:
        print(f"{args.out} already exists — refusing to overwrite without --overwrite.", file=sys.stderr)
        return 2

    try:
        plan = build_repair_plan()
        _validate_finite(plan)
    except RepairPlanError as e:
        print(f"REPAIR PLAN GENERATION REFUSED: {e}", file=sys.stderr)
        return 3

    with open(args.out, "w") as f:
        f.write(canonical_json(plan))

    import datetime
    print("Mode: DRY-RUN REPAIR PLAN (read-only, zero writes, no execute mode exists)")
    print(f"Generated at (console only, not written to the plan file): "
          f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    print(f"Database: {plan['database_identity']}")
    print(f"Source commit: {plan['source_commit']}")
    print(f"Total contaminated rows scanned: {plan['total_contaminated_rows']}")
    for cat in ALL_CATEGORIES:
        print(f"  {cat}: {plan['classification_counts'][cat]}")
    print(f"Dependency summary (blocking matches by domain): {plan['dependency_summary']}")
    print(f"Plan written to: {args.out}")
    print(f"manifest_sha256: {plan['manifest_sha256']}")
    print("\nThis is a plan only. No repair has been executed and no execute/apply/write mode "
          "exists in this script. Verify a saved plan later with --verify <path>.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
