"""
Operator tool — historical backfill for the outcome-resolution lifecycle bug
fixed in services/postgres_store.py and services/alpha_engine/store.py
(2026-07 forensic audit, Phase 1A remediation).

Prior bug: once ANY outcome row existed for a prediction, the resolver would
never look at it again, even if only return_1d/return_5d had resolved and
return_20d/return_60d were still NULL. This tool re-sweeps predictions whose
horizon-relevant columns are still incomplete and fills in only what's
missing — it never overwrites an already-populated value (see
log_outcome's COALESCE upsert).

DRY-RUN IS THE DEFAULT. Nothing is written unless you pass --execute AND
--confirm BACKFILL_OUTCOMES_I_UNDERSTAND (both required together).

Usage:
    # Preview only — makes real price lookups (yfinance) but writes nothing.
    python scripts/backfill_outcomes.py --market IN --horizon medium \\
        --start-date 2026-06-01 --end-date 2026-07-01

    # Actually write — requires the explicit confirmation token.
    python scripts/backfill_outcomes.py --market IN --horizon medium \\
        --start-date 2026-06-01 --end-date 2026-07-01 \\
        --execute --confirm BACKFILL_OUTCOMES_I_UNDERSTAND

Filters:
    --market    IN | US | both        (default: both)
    --horizon   short | medium | long | all   (default: all)
    --start-date / --end-date   YYYY-MM-DD, inclusive, optional (unbounded if omitted)
    --batch-size   cap predictions processed per (market, horizon) pair (default: 500)

This script does not trigger Daily Picks, Multibagger, or any other job — it
only calls the same resolution primitives the periodic outcome resolver uses,
scoped to operator-chosen filters and (by default) in read-only preview mode.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.alpha_engine.outcome_logger import HORIZON_CONFIG, MARKETS, resolve_pair  # noqa: E402

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
                   dry_run: bool, batch_size: int) -> None:
    print("=" * 78)
    print(f"OUTCOME BACKFILL {'DRY RUN' if dry_run else 'EXECUTE'} REPORT")
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
        print(f"  {'would resolve' if dry_run else 'resolved'}:"
              f"{' ' * (18 if dry_run else 24)}{stats['resolved']}")
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
    print(f"TOTAL {'would resolve' if dry_run else 'resolved'}: {total_resolved}")
    print(f"TOTAL skipped:              {total_skipped}")
    print(f"TOTAL ambiguous (excluded): {total_ambiguous}")
    print("-" * 78)
    if dry_run:
        print("\nThis was a DRY RUN — zero rows were written.")
        print(f"To execute for real: re-run with --execute --confirm {CONFIRM_TOKEN}")
    else:
        print("\nWRITE MODE — the counts above reflect rows actually inserted/updated.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing outcome forward-return columns (dry-run by default).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--market", default="both", help="IN, US, or both (default: both)")
    parser.add_argument("--horizon", default="all", help="short, medium, long, or all (default: all)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, inclusive (default: unbounded)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, inclusive (default: unbounded)")
    parser.add_argument("--batch-size", type=int, default=500,
                         help="max predictions processed per (market, horizon) pair (default: 500)")
    parser.add_argument("--execute", action="store_true",
                         help="actually write outcomes (default: dry-run, zero writes)")
    parser.add_argument("--confirm", default=None,
                         help=f"required together with --execute; must equal {CONFIRM_TOKEN!r}")
    args = parser.parse_args()

    if args.execute and args.confirm != CONFIRM_TOKEN:
        print(f"--execute requires --confirm {CONFIRM_TOKEN} — refusing to write.", file=sys.stderr)
        return 2
    dry_run = not args.execute

    if not dry_run and not os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES") == "1":
        print("USE_POSTGRES=1 but DATABASE_URL is not set — refusing to proceed.", file=sys.stderr)
        return 2

    markets = _resolve_market_list(args.market)
    horizon_configs = _resolve_horizon_configs(args.horizon)

    print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'EXECUTE (writes enabled)'}")
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
                dry_run=dry_run, batch_limit=args.batch_size,
            )
            rows.append(stats)
            key = (market, horizon)
            existing[key] = count_existing_outcomes(
                market, horizon, start_date=args.start_date, end_date=args.end_date
            )
            ambiguous[key] = get_ambiguous_pending_predictions(horizon, min_days_old=min_days, market=market)

    _print_report(rows, existing, ambiguous, dry_run, args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
