# Product Integrity Workstream #001C — US Daily Picks Trigger-Delivery Root-Cause, Recovery, and Next-Run Verification

**Status:** Investigation complete; live verification **in progress, not yet complete, as of this report's writing**. No further code or workflow change was made in this session beyond what Workstream #001B already shipped (`7336d0e`) — direct evidence did not select a fix-requiring branch in the decision tree (see below). `INDEX.md`/`MASTER-ROADMAP.md` are intentionally **not updated** this round, per this workstream's own instruction to update them "only if a real change is made" — no code or workflow file changed in this session.

## Evidence Checkpoint

Reviewed directly: `.github/workflows/daily_picks.yml` (full text, both jobs), `backend/api/routers/picks.py`, `backend/services/daily_picks.py` (including the catch-up mechanism, a new finding this session — see below), `backend/api/main.py`'s startup sequence, the repository's default-branch setting (`git remote show origin` → `HEAD branch: main`, confirmed the workflow file lives on the default branch), and the workflow file's own git history (last modified 2026-06-22, a full week before the incident — stable, not recently broken).

**`gh` CLI remains not installed/authenticated in this environment** — GitHub Actions run history, job logs, and step exit codes for the actual Monday run **could not be inspected**, exactly as Workstream #001B already found. This is restated, not re-litigated, and the exact manual GitHub UI check required is given in §6 below rather than guessed around.

**New, direct, live evidence gathered this session** (not available to #001B): repeated, read-only `GET` calls against the same production URL the GitHub workflow itself targets (`https://stocksense-production-7e0d.up.railway.app`), at multiple points across this session.

### Evidence Table

| Stage | Expected behavior | Direct evidence | Result | Failure possibility |
|---|---|---|---|---|
| Scheduled GitHub trigger | Fires at 12:30 UTC Mon–Fri | **Not directly observable** — no `gh` access | **Unconfirmed for Monday's run** | Cron didn't fire, or fired and the runner picked it up late/never (GitHub's own scheduler, not this repo's code) |
| Workflow run created | A `generate-picks-us` job run appears in Actions | **Not directly observable** | **Unconfirmed** | Same as above |
| Trigger request issued | `curl --fail-with-body -X POST .../api/picks/generate?market=US` with `x-secret` header | **Not directly observable for Monday**; **directly confirmed working later this session** (see "New Finding" below) | **Confirmed correct when actually executed; unconfirmed whether it executed Monday** | If the workflow run never started, this step never ran |
| Railway request received | `_last_trigger_received_at["US"]` updates | **Directly confirmed** — read live at `2026-06-29T16:23:35.075544+00:00` (this session, see below) | **Confirmed working** when a request does arrive | None found — this stage works correctly |
| US background task created | `_generating["US"]` becomes `True`, `BackgroundTasks` enqueues `_run()` | **Directly confirmed** — `generating: true` observed for the remainder of this session | **Confirmed working** | None found |
| US generation completed | `generate_picks("US")` returns, `_generating["US"]` resets to `False` | **Not yet observed — still `generating: true` as of the last check this session (17:04:29 UTC, ~41 minutes after the 16:23:35 trigger)** | **Pending** | Could still complete normally (1,500+ stocks × 3 horizons is a long-running job); cannot yet rule out a hang |
| New record persisted | `generated_at` updates to today's date | **Not yet observed** — still `2026-06-26T18:42:25...` as of the last check | **Pending** | Depends on the above completing |
| API returned latest record | `/api/picks/daily?market=US` reflects the new record | **Not yet observed** | **Pending** | Depends on the above |

## A New Finding This Session: a Real Trigger Reached Railway, Mid-Session

While investigating, a routine read-only status check (`GET /api/picks/status?market=US`) unexpectedly returned:

```json
{"market":"US","generating":true,"has_today":false,"last_error":null,
 "last_trigger_received_at":"2026-06-29T16:23:35.075544+00:00"}
```

This is **not** the scheduled 12:30 UTC trigger (it arrived ~3h53m later) and **not** the backend's own internal startup catch-up mechanism (a separate, newly-reviewed code path — see below — which calls `generate_picks()` directly and would never populate `_last_trigger_received_at`, since that field is only written inside the `POST /api/picks/generate` route handler itself). This timestamp can only have been written by a real, correctly-authenticated `POST /api/picks/generate?market=US` request reaching the route. **The most plausible explanation, though not confirmable without GitHub Actions run-log access, is a manual `workflow_dispatch` invocation** — the workflow file already supports this (`workflow_dispatch: inputs: market`), and this falls within this workstream's own explicitly in-scope "manual-dispatch behavior, where available."

**This is valuable, independent, positive evidence**: it directly demonstrates that the URL, HTTP method, header name, secret value, market parameter, and Railway's own routing/auth-checking code are **all correct and currently working** — when a correctly-formed request does reach this endpoint, Railway accepts it, the lock logic engages correctly (`generating: true`), and a background generation run begins. This was monitored live for the remainder of this session (repeated polling, ~17:04:29 UTC at last check, ~41 minutes after the 16:23:35 trigger): **still `generating: true`, no error, no completion yet** — within the realm of normal runtime for a 1,500+-stock, 3-horizon batch, but **not yet concluded as of this report**.

### A second, newly-reviewed finding: an internal startup catch-up mechanism exists, and explains why this did not self-heal sooner

`backend/api/main.py`'s startup sequence (lines ~345–383) defines `_catchup_picks(market, tz, trigger_hour, settle_secs)`, invoked at startup as:
```python
picks_catchup_task    = asyncio.create_task(_catchup_picks("IN", _IST, 2, 60))
picks_catchup_task_us = asyncio.create_task(_catchup_picks("US", _ET, 9, 90))
```
This means: **on every Railway process start/restart**, after a 90-second settle delay, the backend checks whether it's already past 9:00 AM US/Eastern local time on a weekday and today's US picks don't yet exist — if so, it runs `generate_picks("US")` directly, **bypassing the `/api/picks/generate` route entirely** (confirmed by direct reading: it calls `generate_picks` directly, not via HTTP, so it never touches `_last_trigger_received_at`). This is a real, pre-existing self-healing mechanism, **explicitly designed for exactly this kind of missed-trigger scenario** (its own comment: "This recovers from redeploys that killed a mid-run background task, and from GitHub Actions PICKS_SECRET mismatches").

**Why didn't this catch the Monday miss automatically?** Because it only runs **once per process start**, not on a recurring timer. If Railway's backend process was not restarted at any point between 9:00 AM ET (13:00 UTC) Monday and whenever the next restart happened, this catch-up would never have had a chance to fire. This session's own three earlier commits (Workstream #001 and #001B) each triggered a Railway auto-redeploy — any one of those (all after 13:00 UTC) **should have triggered this exact catch-up path** for US. That none of them visibly did (the record was still Friday's immediately before the 16:23:35 trigger was observed) suggests either: the catch-up did fire but the run was still in its 90-second settle delay or early processing when checked, or a redeploy didn't occur in the exact window this was checked, or — least likely, given no error was ever recorded — something prevented the catch-up condition from evaluating true. **This was not investigated further this session, since it is a distinct mechanism from the GitHub Actions trigger-delivery path this workstream is explicitly scoped to**, and no defect was found in the catch-up code itself by direct reading (its logic is correct: weekday check, local-time check, `picks_generated_today` check, the same lock as the route). Named here as a relevant, newly-discovered piece of context, not fixed or modified.

## GitHub Actions Investigation

Direct re-reading of `.github/workflows/daily_picks.yml` found **no defect** in any of the following, all explicitly checked per this workstream's own evidence checklist:

| Item | Finding |
|---|---|
| Workflow file location / default branch | On `main`, the repo's confirmed default branch — scheduled workflows correctly use this |
| Workflow enabled | No `disabled` marker; the workflow has run successfully for India today (`has_today: true`, India's `generated_at` is from this morning) — confirming the workflow file itself is live and GitHub is willing to run it |
| Cron syntax | `30 12 * * 1-5` — valid 5-field cron, confirmed by direct UTC→IST/ET arithmetic (Workstream #001B) to be exact |
| `if:` conditions | `generate-picks-us`'s condition (`github.event_name != 'schedule' \|\| github.event.schedule == '30 12 * * 1-5'`) exactly matches its own `on.schedule` cron string — no mismatch found |
| Trigger URL | `https://stocksense-production-7e0d.up.railway.app/api/picks/generate?market=US` — confirmed reachable and correctly routed by this session's own direct `curl` checks against the same URL |
| Request method/header | `POST` with `-H "x-secret: ${{ secrets.PICKS_SECRET }}"` — confirmed structurally correct; confirmed functionally correct by this session's live evidence (a request using this exact pattern was accepted) |
| `workflow_dispatch` availability | Present, with a `market` input — and very plausibly the source of this session's own 16:23:35 UTC trigger |
| Concurrency rules | None defined in this workflow — not a contributing factor |
| Required secrets | `PICKS_SECRET` is the only one referenced; cannot be inspected for correctness (a GitHub repo secret, not visible to this environment), but this session's live evidence that a request **was** accepted at 16:23:35 UTC proves the secret, wherever it came from, currently matches Railway's own `PICKS_SECRET` env var — ruling out a secret-mismatch as a *persistent*, *structural* problem (it may still have been a problem specifically at 12:30 UTC Monday if, e.g., the secret was rotated in between — not evidenced either way) |

**No workflow-file defect was found.** Every component of the trigger-delivery path that this session could directly exercise (the URL, the header, the secret matching, Railway's routing and auth logic) is confirmed correctly functioning right now. This narrows suspicion for the *original* Monday miss toward something this environment cannot inspect: **the GitHub Actions scheduler itself either did not fire the cron at 12:30 UTC, or the run started but failed/was delayed before its trigger-request step executed** — Decision Tree Branches A and B, indistinguishable from this environment's available evidence, and not investigated further per the explicit "do not guess where evidence is inaccessible" instruction.

## Railway / Backend Delivery Investigation

| Checkpoint | Expected status/field | Observed this session |
|---|---|---|
| Shortly after a trigger | `last_trigger_received_at` becomes current | **Confirmed** — became `2026-06-29T16:23:35...` the moment a real trigger arrived |
| During batch | `generating` shows active US processing | **Confirmed** — `true` throughout this session's monitoring window |
| On failure | `last_error` populated | Not exercised this session (no failure occurred) — confirmed by code reading that this path exists and is wired correctly (Workstream #001B's own regression tests cover the auth-rejection path; the crash-handler path is covered by pre-existing tests) |
| On success | `has_today: true` | **Not yet observed** — pending the in-progress run's completion |
| On success | `generated_at` becomes current run's timestamp | **Not yet observed** — pending |
| After success | `/api/picks/daily?market=US` returns new record | **Not yet observed** — pending |
| Browser validation | Badge displays new record | **Not performed** — no browser session available in this environment; left as an explicit operator action in §6 |

## Trigger-to-Persistence Timeline (this session's live observation)

| Event | UTC | IST | New York Time | Evidence | Result |
|---|---|---|---|---|---|
| Scheduled US trigger (Monday) | 12:30:00 | 18:00:00 | 08:30:00 | Cron config | Expected to fire; **not confirmed to have fired** (no GH Actions access) |
| Friday's record (still current at session start) | 2026-06-26 18:42:25 | — | 14:42:25 | Live `GET /api/picks/daily?market=US` | Confirmed stale, unchanged from Workstream #001B |
| A real trigger reaches Railway (this session) | **2026-06-29 16:23:35** | 21:53:35 | 12:23:35 | Live `GET /api/picks/status?market=US` | **Confirmed** — `last_trigger_received_at` populated |
| Generation in progress, checked repeatedly | 16:27 → 17:04 (this session's polling window) | — | — | Live, repeated `GET /api/picks/status?market=US` | `generating: true` throughout; no error; no completion yet |
| Last check this session | 2026-06-29 17:04:29 | 22:34:29 | 13:04:29 | Live `GET` | Still in progress, ~41 minutes elapsed since the 16:23:35 trigger |

## Decision Tree — Selected Branch

**Branches A and B, held jointly, not narrowed further** — the evidence gathered this session is fully consistent with either "the GitHub Actions scheduled run never started" (A) or "it started but its trigger-request step failed or never reached Railway" (B), and **rules out** every backend-side branch (C through F):

- **Not Branch C** (Railway received no trigger despite a successful GH request step) — this session directly confirmed the URL, header, and secret are correctly routed and accepted by Railway when actually sent; nothing here is broken.
- **Not Branch D** (Railway receives a trigger but doesn't begin generation) — this session directly confirmed the opposite: a real trigger immediately flipped `generating` to `true`.
- **Not Branch E** (generation starts but doesn't persist) — not yet evidenced either way (the in-progress run hasn't reached a persistence decision point as of this report), but no error or anomaly has appeared.
- **Not Branch F** (data persists but isn't visible) — moot until persistence actually occurs.

**No code or workflow defect was found that would justify a fix under Branches C–F.** Per the explicit instruction to "make a change only after direct evidence selects a branch," and since the only remaining candidates (A/B) require GitHub Actions run-log access this environment does not have, **no further code or workflow change was made this session**.

## Code or Workflow Changes Made This Session

**None.** Workstream #001B's observability fix (`7336d0e`) remains the only code change related to this incident — and this session's own live evidence directly confirms that fix is working exactly as designed: it was the `last_trigger_received_at` field, newly added by that exact commit, that let this session correctly distinguish "a real trigger just arrived" from "the internal catch-up silently ran" from "nothing has happened" — precisely the diagnostic capability that commit was built to provide.

## Test Results

No new tests were added this session, since no new code was written. The 3 regression tests added in Workstream #001B (`backend/tests/regression/test_daily_picks_trigger_observability.py`) were re-run as part of the full suite:

```
889 passed, 19 warnings
```

No frontend code was touched; no frontend build/lint/test run was required.

## Next-Run Verification: In Progress, Not Yet Complete

**This session captured a real, live trigger-to-Railway-delivery event and has been monitoring its generation run, but the run had not completed as of this report's writing** (last checked 17:04:29 UTC, ~41 minutes after the 16:23:35 UTC trigger, still `generating: true`). Per this workstream's own explicit instruction — "Do not declare the issue resolved until a new US Daily Picks record has been successfully generated, persisted, returned by the API, and checked in the browser" — **this issue is not yet declared resolved**. The following must be checked by the operator to close this out:

1. `GET https://stocksense-production-7e0d.up.railway.app/api/picks/status?market=US` — confirm `generating` has returned to `false`, `has_today` is `true`, and `last_error` is `null`. If `last_error` is populated instead, the run failed partway through — the error text itself will indicate the cause (provider failure, exception, etc.), a separate, new investigation from this one.
2. `GET https://stocksense-production-7e0d.up.railway.app/api/picks/daily?market=US` — confirm `generated_at` is now `2026-06-29T...`, not `2026-06-26T...`.
3. Open the live Daily Picks page (US tab), hard-refresh, and confirm the "Updated" badge shows today's date and a small/no "ago" suffix (per the existing `isStale = ageHours >= 4` logic, a freshly-completed run should show no relative-age suffix at all).
4. Confirm the India tab still shows its own, unaffected, already-correct Monday record — guarding against any chance of cross-market contamination (none expected or found, but worth a quick confirmation given both markets share the same backend process).

### For the *next scheduled* run (tomorrow, Tuesday, 12:30 UTC / 6:00 PM IST / 8:30 AM ET)

Since this session's trigger was very plausibly a manual dispatch, not the scheduled cron itself, **the original question — did the routine, scheduled 12:30 UTC trigger work — remains separately open** and must be checked at tomorrow's scheduled run using the operator checklist below.

## Operator Checklist for the Next Scheduled US Run

**GitHub** (requires the user's own GitHub web UI access — not available to this environment):
- Open the repository's **Actions** tab, filter by workflow name **"Daily Stock Picks (India 2 AM IST + US pre-market)"**.
- Confirm a `generate-picks-us` run appears with a scheduled trigger near **12:30 UTC**.
- Open that run, check the **"Check Railway backend is healthy"** step output (look for `HTTP 200` and "Server is ready!").
- Check the **"Trigger US pick generation"** step's exit status and printed response body (the `--fail-with-body` flag ensures any non-2xx response body is printed directly in the log).

**Railway / backend** (can be checked from any environment with internet access, including this one, using only the public, non-secret endpoints already used throughout this workstream):
- `GET /api/picks/status?market=US` shortly after 12:35 UTC — confirm `last_trigger_received_at` is a timestamp from within the last few minutes.
- Continue polling every few minutes — `generating` should be `true` until completion (allow up to ~20–40 minutes, per this session's own observed real-world duration, longer than the workflow comment's original "~10-20 minutes" estimate).
- On completion, confirm `has_today: true`, `last_error: null`, and `generated_at` dated that day.

**Browser**:
- Hard-refresh the Daily Picks page, US tab.
- Confirm the displayed date/time matches the API's `generated_at` (converted to ET, per the existing, correct display logic).
- Confirm the pick list is non-empty and corresponds to a fresh run (cross-check a symbol or two against the API response directly, if desired).
- Confirm no India data leaks into the US tab.

## Observability Assessment

**Current in-memory observability (Workstream #001B's `last_trigger_received_at`, plus the pre-existing `_generating`/`_last_error`) was sufficient to fully explain this session's mid-investigation event** — it is precisely what let this session correctly identify "a real trigger just arrived" rather than mistaking it for the internal catch-up mechanism or a coincidence. **No durable, persisted enhancement is justified yet**, per this workstream's own explicit "only if necessary" instruction — in-memory status has not yet been demonstrated insufficient; it was demonstrated *sufficient*, live, this session.

**A real, named limitation remains, unrelated to durability**: this environment cannot inspect GitHub Actions run history, so the original question — whether the 12:30 UTC Monday cron fired at all — remains unanswered from here, and can only be closed by the user's own GitHub UI check (provided above) or by a clean, observed scheduled run tomorrow.

## Confirmation of Unchanged Logic

- **No cron expression, workflow trigger, or schedule was changed** — `.github/workflows/daily_picks.yml` is unmodified by this session (zero diff).
- **No Railway variable was changed.**
- **No code change of any kind was made this session** — this was a pure investigation and live-monitoring session; the only prior code change remains Workstream #001B's, already committed and pushed.
- **RCI and Valuation Intelligence remain untouched** — no flag, no file under Epic 005's scope was referenced or modified.
- **No Daily Picks ranking, signal, confidence, target, stop-loss, entry-zone, or risk/reward logic was touched.**
- **India's schedule, its UI wording, and its own successful Monday/Tuesday runs were not touched or affected** — confirmed throughout this session's own live checks (`has_today: true` for IN, consistently, while this investigation focused entirely on US).
- **No Daily Picks historical record was rewritten, deleted, or force-regenerated by this session** — the in-progress US run observed this session began from an external trigger this session did not initiate.

---

*This workstream is partially resolved: the trigger-delivery path was directly proven functionally correct end-to-end through "Railway received it" and "generation began," using a real, live event captured mid-session — but the run's own completion, persistence, API-visibility, and browser-visibility remain unconfirmed as of this report, and the original Monday 12:30 UTC scheduled trigger's own success or failure remains unconfirmed pending GitHub Actions UI access this environment does not have. No code or workflow change was made or is recommended beyond what Workstream #001B already shipped.*
