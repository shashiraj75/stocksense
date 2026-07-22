"""
DP-033 governed SEC point-in-time ingestion tool (2026-07-22).

Mirrors this repository's own established safety pattern for
production-affecting operator scripts (see backfill_outcomes.py's
dry-run / manifest / --execute --confirm convention) — default to
local/dry-run, require a deliberate flag AND a typed confirmation phrase
before any production write, and lock the target universe to a versioned
manifest so accidental universe drift fails closed instead of silently
processing more (or fewer) symbols than intended.

This tool performs ONLY Stage 1 (source-data ingestion) of the five-stage
production plan recorded in the implementation register. It NEVER runs
validation, NEVER publishes results, NEVER touches the UI/warning layer,
and NEVER touches India.

Usage:

  # Schema preflight only — creates/verifies tables, ingests nothing.
  python scripts/sec_pit_ingest.py preflight

  # Dry run against the LOCAL/default SQLite store (safe, default mode).
  python scripts/sec_pit_ingest.py ingest

  # Explicit production run — requires BOTH flags. Refuses without both.
  python scripts/sec_pit_ingest.py ingest --production \\
      --confirm SEC_PIT_INGEST_I_UNDERSTAND

  # Reconciliation report for a completed run.
  python scripts/sec_pit_ingest.py reconcile --run-id <run_id>
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONFIRM_PHRASE = "SEC_PIT_INGEST_I_UNDERSTAND"


def _expected_universe():
    from services.validation_engine import US_BASKET
    return list(US_BASKET)


def cmd_preflight(args):
    import services.sec_pit_store as store
    store.init_db()
    print(f"Schema preflight OK. schema_version={store.SCHEMA_VERSION}, "
          f"target={'PRODUCTION Postgres' if store._USE_POSTGRES else 'local ' + store._SQLITE_PATH}")
    print("No ingestion performed. No validation run. No publication. No UI change.")


def cmd_ingest(args):
    import services.sec_pit_store as store

    expected = _expected_universe()
    manifest_version = f"dp033_v2:{len(expected)}:{','.join(sorted(expected))}"

    if args.production:
        if args.confirm != CONFIRM_PHRASE:
            print(f"REFUSED: --production requires --confirm {CONFIRM_PHRASE}", file=sys.stderr)
            sys.exit(2)
        if not store._USE_POSTGRES:
            print("REFUSED: --production requires DATABASE_URL and USE_POSTGRES=1 to be set "
                  "in the environment — this process does not currently target Postgres.", file=sys.stderr)
            sys.exit(2)
        print("*** PRODUCTION INGESTION — writing to the configured production Postgres database ***")
    else:
        print(f"Dry-run / local mode — writing to {store._SQLITE_PATH} (not production).")

    store.init_db()
    run_id = args.run_id or store.new_run_id()
    print(f"run_id={run_id} manifest_version={manifest_version}")
    print(f"Universe: {len(expected)} symbols (locked to validation_engine.US_BASKET at invocation time)")

    t0 = time.time()
    results = []
    for i, sym in enumerate(expected, 1):
        r = store.ingest_symbol(sym, run_id, manifest_version)
        results.append(r)
        print(f"  [{i}/{len(expected)}] {sym}: {r['completion_status']}"
              + (f" ({r['fact_rows_inserted']} rows)" if r["completion_status"] == "complete" else
                 f" ({r['failure_reason']})"))

    elapsed = time.time() - t0
    by_status = {}
    for r in results:
        by_status.setdefault(r["completion_status"], []).append(r["symbol"])

    print(f"\n=== Reconciliation (run_id={run_id}, {round(elapsed,1)}s) ===")
    for status, symbols in sorted(by_status.items()):
        print(f"  {status}: {len(symbols)} — {symbols}")

    unexpected_unsupported = [s for s, r in zip(expected, results)
                               if r["completion_status"] not in ("complete", "governed_unsupported")]
    if unexpected_unsupported:
        print(f"\nWARNING: {len(unexpected_unsupported)} symbol(s) failed unexpectedly: {unexpected_unsupported}")
        print("Review before treating this ingestion as complete for publication purposes.")

    print("\nNo validation run was triggered. No results were published. No warning changed.")

    report_path = f"/tmp/sec_pit_ingest_{run_id}.json"
    with open(report_path, "w") as f:
        json.dump({"run_id": run_id, "manifest_version": manifest_version,
                   "elapsed_sec": round(elapsed, 2), "results": results}, f, indent=2)
    print(f"\nMachine-readable report: {report_path} (local only — not committed, not production data)")


def cmd_reconcile(args):
    import services.sec_pit_store as store
    expected = _expected_universe()
    print(f"Reconciliation for run_id={args.run_id}")
    for sym in expected:
        status = store.get_ingestion_status(sym, run_id=args.run_id)
        if status is None:
            print(f"  {sym}: NOT FOUND for this run_id")
        else:
            print(f"  {sym}: {status['completion_status']} "
                  f"(taxonomy={status['taxonomy_classification']}, "
                  f"rows={status['fact_rows_inserted']}, "
                  f"accessions={status['distinct_accessions']}, "
                  f"filed {status['earliest_filed']}..{status['latest_filed']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser("preflight", help="Create/verify schema only — no ingestion")
    p_pre.set_defaults(func=cmd_preflight)

    p_ing = sub.add_parser("ingest", help="Ingest the governed US_BASKET universe (dry-run by default)")
    p_ing.add_argument("--production", action="store_true",
                        help="Target production Postgres (requires --confirm and DATABASE_URL/USE_POSTGRES=1)")
    p_ing.add_argument("--confirm", default="",
                        help=f"Must equal '{CONFIRM_PHRASE}' when --production is set")
    p_ing.add_argument("--run-id", default=None, help="Reuse an existing run_id (default: generate new)")
    p_ing.set_defaults(func=cmd_ingest)

    p_rec = sub.add_parser("reconcile", help="Print per-symbol reconciliation for a completed run")
    p_rec.add_argument("--run-id", required=True)
    p_rec.set_defaults(func=cmd_reconcile)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
