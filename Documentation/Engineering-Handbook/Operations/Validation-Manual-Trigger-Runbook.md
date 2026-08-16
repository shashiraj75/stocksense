# Validation Manual Trigger Runbook

**Status:** Authoritative operator runbook for manually triggering walk-forward validation. Created alongside the V-SEC1/V-SEC2 security hotfix (`fix/validation-run-endpoint-auth`) that closed a confirmed unauthenticated-trigger defect on `POST /api/validation/run`.

**Scope:** This document governs manual/operator-triggered validation runs only. It does not change, and is not a source of truth for, the automatic scheduler's cadence, validation formulas, run-state schema, or freshness semantics — see `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` for current operational status of related work.

---

## 1. Normal production mechanism: automatic scheduling

Validation is **normally** produced by an in-process scheduler (`backend/api/main.py`'s `_validation_schedule_loop`), not by any manual trigger:

- Medium-horizon validation runs **daily**, across all three universes (Nifty 100, Midcap, US).
- Long-horizon validation runs **weekly, on Sunday**, across all three universes.

This scheduler calls `run_validation()` **directly as a Python function** — it never goes through the HTTP route documented below, and is therefore entirely unaffected by that route's authentication requirement.

## 2. The public `/validation` page cannot launch a job

`stocksense360.com/validation` is a **read-only** results display. As of the V-SEC2 fix, it contains no control, button, or code path that can call `POST /api/validation/run`. This was a deliberate security fix — the page previously had an unauthenticated "Run Now" button that any site visitor could click to launch a real production validation run. See the PR that introduced this runbook for the full root-cause record.

The timestamp shown on the page (labeled **"Validation completed"**) means the last successful validation completion for the selected horizon/universe. It does **not** mean the underlying market data is fresh as of that moment — validation completion time and source-data freshness are two different things.

## 3. Authorized manual execution

Manual validation runs are only possible through the protected backend endpoint, invoked from outside the browser (an operator's own shell, an internal script, or equivalent server-side tooling) — never from the public website:

```
POST /api/validation/run?horizon=<short|medium|long>&universe=<nifty100|midcap|us>
Header: X-Secret: <value of the PICKS_SECRET environment variable>
```

This reuses the project's existing shared admin-secret convention (the same `PICKS_SECRET` value already required for `/api/picks/generate` and other protected operator endpoints) rather than introducing a second, separate credential.

**Redacted example only — do not substitute a real secret into a copy-pasted command, and do not run this against production without the approval gate in §6:**

```
curl -X POST "https://<backend-host>/api/validation/run?horizon=medium&universe=nifty100" \
  -H "X-Secret: <PICKS_SECRET value — obtain from your own secrets manager, never from this file>"
```

## 4. The secret must never

- be committed to this repository, in this file or any other;
- be logged, printed, or echoed by any script or CI job;
- be placed in a URL or query string;
- be exposed through a `NEXT_PUBLIC_*` environment variable or any frontend/browser code;
- be sent to, or made reachable by, an ordinary website user.

The frontend `/validation` page has no knowledge of this secret and must never be given one — if a future change appears to require the browser to hold this credential, that is a sign the design is wrong, not a reason to add it.

## 5. Fail-closed behavior

`POST /api/validation/run` rejects with `401 {"detail": "Invalid secret"}` for:

- a missing `X-Secret` header;
- a blank or whitespace-only header;
- an incorrect header value;
- a missing/blank `PICKS_SECRET` in the environment (the route never treats an unconfigured secret as "anything matches").

A rejected request never claims the validation job slot and never invokes `run_validation()` — confirmed by the router's own test suite (`backend/tests/regression/test_validation_run_endpoint_auth.py`).

## 6. Operational approval gate

Manual production validation execution is **not** a routine action. Before running the command in §3 against production:

1. Get explicit approval for this specific manual run (this runbook does not itself constitute standing authorization).
2. **Confirm no conflicting validation activity is already in progress** — `GET /api/validation/status` (no credential required, read-only) reports `"running": true/false` and, if true, the active job's market/universe/horizon. The validation job slot is a single global lock across all markets/universes; a manual run while one is already active returns `{"status": "already_running", ...}` and does not start a second run.
3. Confirm the target horizon/universe is actually what's intended — a manual run consumes real provider/CPU time (the US medium run alone has been observed to take several hours).

## 7. Rollback

If this security change ever needs to be reverted, rollback is a **normal code revert** of the V-SEC1/V-SEC2 commits — no database migration, no schema change, no production data mutation, no secret rotation is required. Reverting simply restores the previous (unauthenticated) behavior, which is why this hotfix exists in the first place.
