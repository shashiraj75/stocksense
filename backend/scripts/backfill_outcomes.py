"""
Operator tool — historical backfill for the outcome-resolution lifecycle bug
fixed in services/postgres_store.py and services/alpha_engine/store.py
(2026-07 forensic audit, Phase 1A remediation), with deterministic
manifest-locked selection and transactional execution (Phase 1A.3).

Prior bug (Phase 1A): once ANY outcome row existed for a prediction, the
resolver would never look at it again, even if only return_1d/return_5d had
resolved and return_20d/return_60d were still NULL.

Prior gap (fixed in 1A.3): get_unresolved_predictions had no ORDER BY and
didn't select predictions.id, so a --batch-size slice wasn't reproducible
across runs — a reviewed dry-run and a later --execute couldn't be proven to
operate on the same candidates. Writes were also individually autocommitted,
so a mid-batch failure could leave a partial batch applied.

This tool now has three modes:

  1. Plain preview (default, no --manifest/--manifest-out) — unchanged
     ad-hoc scanning behavior from Phase 1A: dry-run only, reports
     examined/resolved/skipped/ambiguous counts. Cannot write.

  2. Manifest generation (--manifest-out PATH) — deterministically selects
     candidates (oldest pred_date first, stable prediction_id tie-break),
     resolves their actual forward-return values now, and freezes the exact
     batch into a versioned, checksummed JSON file. Zero database writes.

  3. Manifest execution (--manifest PATH --manifest-sha256 HASH [--execute
     --confirm BACKFILL_OUTCOMES_I_UNDERSTAND]) — re-validates every manifest
     candidate against live state (aborting the whole batch before any write
     if anything has drifted), then, only with --execute --confirm, applies
     the exact manifest-recorded values as ONE database transaction — all
     writes commit together or none do.

--execute is REFUSED if no --manifest is given — there is no direct-write
mode anymore. This is deliberate: a live re-derived candidate set can never
be proven to match what was reviewed, so all writes must go through a
saved, checksummed manifest.

Usage:
    # 1. Plain preview scan (ad hoc, no manifest, always dry-run).
    python scripts/backfill_outcomes.py --market IN --horizon short

    # 2. Generate a manifest for review.
    python scripts/backfill_outcomes.py --market IN --horizon short \\
        --start-date 2026-06-17 --end-date 2026-06-22 --batch-size 200 \\
        --manifest-out /tmp/canary.json

    # 3a. Preflight-check a saved manifest without writing.
    python scripts/backfill_outcomes.py --manifest /tmp/canary.json \\
        --manifest-sha256 <hash from step 2>

    # 3b. Execute it for real (one transaction, all-or-nothing).
    python scripts/backfill_outcomes.py --manifest /tmp/canary.json \\
        --manifest-sha256 <hash from step 2> \\
        --execute --confirm BACKFILL_OUTCOMES_I_UNDERSTAND

Filters (modes 1 and 2):
    --market    IN | US | both        (default: both)
    --horizon   short | medium | long | all   (default: all)
    --start-date / --end-date   YYYY-MM-DD, inclusive, optional (unbounded if omitted)
    --batch-size   cap predictions processed per (market, horizon) pair (default: 500)

This script does not trigger Daily Picks, Multibagger, or any other job.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alpha_engine.outcome_logger import HORIZON_CONFIG, MARKETS, resolve_pair  # noqa: E402
from services.alpha_engine import manifest_backfill  # noqa: E402

CONFIRM_TOKEN = "BACKFILL_OUTCOMES_I_UNDERSTAND"


def _resolve_market_list(arg: str) -> list[str]:
    if arg == "both":
        return list(MARKETS)
    if arg not in MARKETS:
        raise SystemExit(f"--market must be one of {MARKETS + ('both',)}, got {arg!r}")
    return [arg]


def _resolve_horizon_configs(arg: str) -> list[tuple]:
    if arg == "all":
        return HORIZON_CONFIG
    matches = [cfg for cfg in HORIZON_CONFIG if cfg[0] == arg]
    if not matches:
        valid = [cfg[0] for cfg in HORIZON_CONFIG] + ["all"]
        raise SystemExit(f"--horizon must be one of {valid}, got {arg!r}")
    return matches


def _print_report(rows: list[dict], existing: dict[tuple, dict], ambiguous: dict[tuple, list],
                   batch_size: int) -> None:
    """Plain-preview mode report — always dry-run, never writes."""
    print("=" * 78)
    print("OUTCOME BACKFILL DRY RUN REPORT (plain preview — use --manifest-out to save a batch)")
    print("=" * 78)

    total_examined = total_resolved = total_skipped = total_ambiguous = 0
    for stats in rows:
        key = (stats["market"], stats["horizon"])
        ex = existing.get(key, {"total": 0, "missing_5d": 0, "missing_20d": 0, "missing_60d": 0})
        amb = ambiguous.get(key, [])
        total_examined += stats["examined"]
        total_resolved += stats["resolved"]
        total_skipped += stats["skipped"]
        total_ambiguous += len(amb)

        print(f"\n[{stats['market']}/{stats['horizon']}]")
        print(f"  predictions examined:            {stats['examined']}")
        print(f"  existing outcome rows in range:   {ex['total']}")
        print(f"    missing return_5d:              {ex['missing_5d']}")
        print(f"    missing return_20d:             {ex['missing_20d']}")
        print(f"    missing return_60d:             {ex['missing_60d']}")
        print(f"  would resolve:                    {stats['resolved']}")
        print(f"  skipped (no return computable — not enough")
        print(f"    trading days yet, or missing/suspended/delisted price): {stats['skipped']}")
        print(f"  ambiguous groups (excluded, fail-closed):  {len(amb)}")
        if amb:
            for a in amb[:5]:
                print(f"    - {a['symbol']} {a['pred_date']}: "
                      f"{a['n_predictions']} predictions, {a['n_distinct_prices']} distinct prices")
            if len(amb) > 5:
                print(f"    ... and {len(amb) - 5} more")
        if stats["pending"] > batch_size:
            print(f"  NOTE: {stats['pending']} eligible, batch-size capped this run to {batch_size} "
                  f"— re-run to continue.")

    print("\n" + "-" * 78)
    print(f"TOTAL predictions examined: {total_examined}")
    print(f"TOTAL would resolve: {total_resolved}")
    print(f"TOTAL skipped:              {total_skipped}")
    print(f"TOTAL ambiguous (excluded): {total_ambiguous}")
    print("-" * 78)
    print("\nThis was a DRY RUN — zero rows were written.")
    print("Plain preview mode can never write. Use --manifest-out to save a reviewable, "
          "checksummed batch, then --manifest ... --execute --confirm to apply it.")


def _run_plain_preview(args) -> int:
    markets = _resolve_market_list(args.market)
    horizon_configs = _resolve_horizon_configs(args.horizon)

    print("Mode: PLAIN PREVIEW (dry-run only, no manifest)")
    print(f"Markets: {markets}  Horizons: {[c[0] for c in horizon_configs]}  "
          f"Date range: {args.start_date or '(unbounded)'} .. {args.end_date or '(unbounded)'}  "
          f"Batch size: {args.batch_size}")

    rows = []
    existing = {}
    ambiguous = {}
    from services.alpha_engine.store import get_ambiguous_pending_predictions, count_existing_outcomes

    for market in markets:
        for horizon, min_days, d1, d5, d20, d60 in horizon_configs:
            stats = resolve_pair(
                market, horizon, min_days, d1, d5, d20, d60,
                start_date=args.start_date, end_date=args.end_date,
                dry_run=True, batch_limit=args.batch_size,
            )
            rows.append(stats)
            key = (market, horizon)
            existing[key] = count_existing_outcomes(
                market, horizon, start_date=args.start_date, end_date=args.end_date
            )
            ambiguous[key] = get_ambiguous_pending_predictions(horizon, min_days_old=min_days, market=market)

    _print_report(rows, existing, ambiguous, args.batch_size)
    return 0


def _run_manifest_out(args) -> int:
    if args.execute:
        print("--manifest-out is a preview mode and cannot be combined with --execute.", file=sys.stderr)
        return 2
    if os.path.exists(args.manifest_out) and not args.manifest_overwrite:
        print(f"{args.manifest_out} already exists — refusing to overwrite without "
              f"--manifest-overwrite.", file=sys.stderr)
        return 2
    if args.market == "both":
        print("--manifest-out requires a single --market (IN or US), not 'both'.", file=sys.stderr)
        return 2
    if args.horizon == "all":
        print("--manifest-out requires a single --horizon (short/medium/long), not 'all'.", file=sys.stderr)
        return 2

    print("Mode: MANIFEST GENERATION (preview — zero database writes)")
    try:
        manifest = manifest_backfill.build_manifest(
            args.market, args.horizon,
            start_date=args.start_date, end_date=args.end_date, batch_limit=args.batch_size,
        )
    except manifest_backfill.ManifestGenerationError as e:
        print(f"MANIFEST GENERATION REFUSED — zero writes performed. Reason: {e}", file=sys.stderr)
        return 3
    with open(args.manifest_out, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"Market={manifest['market']} Horizon={manifest['horizon']} "
          f"Requested={manifest['requested_candidate_count']} "
          f"Actual={manifest['actual_candidate_count']} "
          f"SourcePopulationExhausted={manifest['source_population_exhausted']}")
    if manifest["actual_candidate_count"] < manifest["requested_candidate_count"]:
        print(f"NOTE: this manifest is under-filled — the eligible pool was exhausted "
              f"before reaching the requested count. This is recorded explicitly in the "
              f"manifest (source_population_exhausted=true) and will be re-validated by "
              f"the loader before any execution.")
    print(f"Date span: {manifest['start_date'] or '(unbounded)'} .. {manifest['end_date'] or '(unbounded)'}")
    sr = manifest.get("scan_report", {})
    print(f"Scan report: eligible_scanned={sr.get('eligible_scanned')} "
          f"usable_candidates={sr.get('usable_candidates')} "
          f"market_conflict_exclusions={sr.get('market_conflict_exclusions')} "
          f"unknown_symbol_warnings={sr.get('unknown_symbol_warnings')} "
          f"ambiguous_group_exclusions={sr.get('ambiguous_group_exclusions')} "
          f"missing_price_exclusions={sr.get('missing_price_exclusions')} "
          f"semantically_empty_exclusions={sr.get('semantically_empty_exclusions')} "
          f"requested_batch_limit={sr.get('requested_batch_limit')} "
          f"pool_exhausted_before_limit={sr.get('pool_exhausted_before_limit')}")
    print(f"Manifest written to: {args.manifest_out}")
    print(f"manifest_sha256: {manifest['manifest_sha256']}")
    print("\nThis was a preview — zero rows were written.")
    print("To execute: re-run with --manifest <path> --manifest-sha256 <hash above> "
          f"--execute --confirm {CONFIRM_TOKEN}")
    return 0


def _run_manifest_execute(args) -> int:
    if not args.manifest_sha256:
        print("--manifest requires --manifest-sha256 (the checksum you reviewed).", file=sys.stderr)
        return 2
    if args.execute and args.confirm != CONFIRM_TOKEN:
        print(f"--execute requires --confirm {CONFIRM_TOKEN} — refusing to write.", file=sys.stderr)
        return 2

    with open(args.manifest) as f:
        manifest = json.load(f)

    dry_run = not args.execute
    print(f"Mode: MANIFEST {'EXECUTE' if not dry_run else 'PREFLIGHT (dry-run)'}")
    print(f"Manifest: {args.manifest}  Market={manifest.get('market')} "
          f"Horizon={manifest.get('horizon')}  Candidates={manifest.get('candidate_count')}")

    try:
        result = manifest_backfill.execute_manifest(manifest, args.manifest_sha256, dry_run=dry_run)
    except manifest_backfill.ManifestValidationError as e:
        print(f"MANIFEST VALIDATION FAILED — zero writes performed. Reason: {e}", file=sys.stderr)
        return 3
    except manifest_backfill.ManifestPreflightError as e:
        print(f"PREFLIGHT FAILED for {len(e.issues)} candidate(s) — zero writes performed.",
              file=sys.stderr)
        for issue in e.issues:
            print(f"  - prediction_id={issue['prediction_id']}: {issue['reason']}", file=sys.stderr)
        return 3

    print(f"manifest_sha256: {args.manifest_sha256}")
    print(f"requested candidate count: {manifest.get('candidate_count')}")
    if result.get("source_commit_mismatch"):
        print(f"WARNING: source_commit mismatch — manifest was generated at "
              f"{result.get('manifest_source_commit')}, current checked-out commit is "
              f"{result.get('current_source_commit')}. Candidate data was still independently "
              f"re-validated by preflight regardless of code version; review before proceeding "
              f"if this difference is unexpected.")

    if dry_run:
        print(f"preflight-passed count: {result['would_write']} (all candidates)")
        print(f"\nPreflight passed for all {result['would_write']} candidates. "
              f"Zero rows were written (dry-run).")
        print(f"To execute for real: re-run with --execute --confirm {CONFIRM_TOKEN}")
    else:
        n_insert = sum(1 for w in result["written"] if w["operation"] == "INSERT")
        n_update = sum(1 for w in result["written"] if w["operation"] == "UPDATE")
        print(f"preflight-passed count: {len(result['written'])} (all candidates)")
        print(f"inserted rows: {n_insert}")
        print(f"updated rows: {n_update}")
        print("no-op rows: not applicable — a field already filled by someone else since "
              "manifest generation aborts the whole batch as drift rather than silently "
              "no-op'ing (see preflight/transaction drift checks), so every written row below "
              "is a genuine contribution from this batch")
        print("failed rows: not applicable — this batch is all-or-nothing; either every row "
              "below committed together, or (on any failure) zero rows did")
        print("transaction: COMMITTED")
        print(f"\nWROTE {len(result['written'])} row(s) in one transaction.")
        print("exact keys changed (symbol, market, horizon, pred_date):")
        for w in result["written"]:
            print(f"  - {w['symbol']} {w['market']} {w['horizon']} {w['pred_date']} ({w['operation']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing outcome forward-return columns via a "
                    "deterministic, checksummed manifest (dry-run by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--market", default="both", help="IN, US, or both (default: both)")
    parser.add_argument("--horizon", default="all", help="short, medium, long, or all (default: all)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, inclusive (default: unbounded)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, inclusive (default: unbounded)")
    parser.add_argument("--batch-size", type=int, default=500,
                         help="max predictions processed per (market, horizon) pair (default: 500)")
    parser.add_argument("--manifest-out", default=None,
                         help="write a deterministic, checksummed candidate manifest to this path "
                              "(preview only, zero writes)")
    parser.add_argument("--manifest-overwrite", action="store_true",
                         help="allow --manifest-out to overwrite an existing file")
    parser.add_argument("--manifest", default=None,
                         help="execute (or preflight-check) a previously generated manifest file")
    parser.add_argument("--manifest-sha256", default=None,
                         help="required with --manifest; must equal that manifest's own recorded checksum")
    parser.add_argument("--execute", action="store_true",
                         help="actually write outcomes (default: dry-run, zero writes). "
                              "REQUIRES --manifest — there is no direct-write mode.")
    parser.add_argument("--confirm", default=None,
                         help=f"required together with --execute; must equal {CONFIRM_TOKEN!r}")
    args = parser.parse_args()

    if args.manifest_out and args.manifest:
        print("--manifest-out and --manifest are mutually exclusive modes.", file=sys.stderr)
        return 2

    if args.execute and not args.manifest:
        print("--execute requires --manifest — there is no direct-write mode. Generate a manifest "
              "first with --manifest-out, review it, then re-run with --manifest --manifest-sha256 "
              f"--execute --confirm {CONFIRM_TOKEN}.", file=sys.stderr)
        return 2

    if os.getenv("USE_POSTGRES") == "1" and not os.getenv("DATABASE_URL"):
        print("USE_POSTGRES=1 but DATABASE_URL is not set — refusing to proceed.", file=sys.stderr)
        return 2

    if args.manifest:
        return _run_manifest_execute(args)
    if args.manifest_out:
        return _run_manifest_out(args)
    return _run_plain_preview(args)


if __name__ == "__main__":
    raise SystemExit(main())
