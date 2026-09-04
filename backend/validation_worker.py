"""
Prepared, NOT wired into any live trigger path — a standalone one-shot
entrypoint for running exactly one validation attempt (one horizon +
universe) as an isolated OS process, and exiting.

Status: this file exists so the exact code a future dedicated Railway
"validation worker" service would run is fully specified and reviewable
now — see Documentation/Engineering-Handbook/Operations/
Validation-Memory-Architecture-Review.md for the evidence-based
recommendation on whether/when to actually create that service. Until a
human explicitly creates and points a Railway service at this entrypoint
(a production change, out of scope for this PR), nothing calls this file
— it has zero effect on any currently running process, schedule, or
trigger. `api/main.py`'s existing in-process scheduler/catch-up loops are
completely untouched.

Design constraints (all satisfied by delegating entirely to the existing,
already-audited services.validation_engine.execute_admitted_validation —
this file adds NO new validation logic, no new persistence path, no new
scoring/methodology):
  - starts fresh for each invocation (a plain `python validation_worker.py`
    process — no server, no event loop, no background tasks);
  - invokes the existing authoritative validation engine unchanged;
  - persists using the existing atomic and fenced contract
    (execute_admitted_validation -> admit_validation_attempt's ledger/
    lease admission, exactly as the scheduler/catch-up/manual /run paths
    already do — so this worker CANNOT overlap with an already-running
    validation attempt, and CANNOT bypass the lease/fencing contract);
  - the validation computation itself already runs in a further-nested
    spawned child OS process (services.validation_engine's own
    multiprocessing.get_context("spawn") — unchanged), so this worker's
    own process holds only orchestration-level memory around that;
  - closes database connections (every validation_engine DB call already
    closes its connection in a `finally:` block — unchanged, not
    reimplemented here);
  - terminates and reaps the validation child process (unchanged —
    services.validation_engine._terminate_child_process /
    _reap_child_process, already invoked on every exit path inside
    execute_admitted_validation's call chain);
  - releases large payload references naturally: `result` is the only
    reference to the run's payload, held in this function's local scope,
    which ends when the process exits;
  - exits 0 only after execute_admitted_validation reports ok=True
    (i.e. successful admission, computation, AND persistence — a
    rejected admission, e.g. lease already held, is NOT a worker
    failure, see --allow-rejected below);
  - exits non-zero on any failure or exception;
  - never starts an HTTP server, never stays resident — this script has
    no event loop and returns control to the OS the moment
    execute_admitted_validation returns;
  - CANNOT activate a currently-disabled market/universe: --universe is
    restricted to the exact same hardcoded set api/main.py's scheduler
    already uses (nifty100, midcap, us) via argparse `choices=`, and
    --horizon to (medium, long) — short validation's separate,
    env-gated auto-schedule is deliberately out of scope for this
    worker, unchanged;
  - does not change deterministic outputs: calls the exact same
    execute_admitted_validation() the in-process scheduler calls, with
    the same default parameters (max_workers=6, lease/heartbeat/deadline
    defaults) — same inputs produce the same outputs regardless of
    which process orchestrates the call.
"""
import argparse
import sys
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot validation run — starts, runs exactly one "
                     "horizon/universe admission+execution+persistence "
                     "cycle via the existing shared admission path, then exits."
    )
    parser.add_argument("--horizon", required=True, choices=("medium", "long"))
    parser.add_argument("--universe", required=True, choices=("nifty100", "midcap", "us"))
    parser.add_argument(
        "--trigger-type", default="worker",
        help="Recorded on the attempt row for observability (default: worker). "
             "Never affects admission, fencing, or persistence semantics.",
    )
    parser.add_argument(
        "--allow-rejected", action="store_true",
        help="Exit 0 even if admission is rejected (e.g. another attempt already "
             "holds the lease) — useful when this worker may be scheduled "
             "concurrently with the in-process scheduler/catch-up during a "
             "migration window and a rejection is an EXPECTED outcome, not a "
             "worker failure. Without this flag, a rejection exits non-zero.",
    )
    args = parser.parse_args()

    # Local import: keep worker startup (argument parsing, --help) fast and
    # free of the full application's import graph until a real run is about
    # to happen.
    from services.validation_engine import execute_admitted_validation

    owner = f"worker-{args.horizon}-{args.universe}-{datetime.now(timezone.utc).isoformat()}"
    print(f"[validation_worker] starting {args.horizon}/{args.universe} "
          f"(trigger_type={args.trigger_type}, owner={owner})", flush=True)

    result = execute_admitted_validation(
        horizon=args.horizon, universe=args.universe,
        trigger_type=args.trigger_type, owner=owner,
    )

    if result.get("ok"):
        print(f"[validation_worker] {args.horizon}/{args.universe} complete "
              f"(run_id={result.get('run_id')})", flush=True)
        return 0

    reason = result.get("reason")
    print(f"[validation_worker] {args.horizon}/{args.universe} rejected/failed "
          f"— reason={reason}", file=sys.stderr, flush=True)
    return 0 if args.allow_rejected else 1


if __name__ == "__main__":
    sys.exit(main())
