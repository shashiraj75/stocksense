# Trade Postmortem — Wave C Formal 12-Perspective Audit

**Status: audit complete.** Candidate SHA at time of audit:
`9264898393f77f32f53fa6df31f9123ae0cebb09` (documentation-only and
test-only commits since the last executable closure SHAs; no backend
executable behavior changed since WC-O closed at `85d7edba54001...`,
no frontend executable behavior changed since WC-N closed at
`d9096a9b1626...` other than the one additive test in commit
`9264898`).

Frozen references: WC-K `7432851ea7affa7a80e8db131337763c7cbb69eb`;
WC-N `d9096a9b16c2267a5cd6c4fac866467acffe07df`; WC-O
`85d7edba540018f7b8c1fb625126fe25edceb49f`. Traceability manifest:
`backend/tests/unit/wave_c_traceability_manifest.json`.

Stage M note: `origin/main` was confirmed (fresh `git fetch`) to be
exactly the merge-base with this branch (`a71872641657169da1d5c2f5e9511393d7f0bb9d`)
— zero commits of drift. No reconciliation merge was required or
performed.

---

## 1. Architecture and authority boundaries

**Scope inspected:** the three-layer boundary between the canonical
read API (`GET .../current-report`), the write/generation orchestrator
(`current_report_generation.process_current_report`), and the
background worker (`outbox_worker.py`); the frontend/backend flag
boundary; the metrics module's fail-open boundary.

**Evidence inspected:** WC-K's own docstring assertions (verified via
Gate 2's real-PostgreSQL no-forbidden-call proof); WC-N's read-only
client (`fetchCurrentPostmortemReport` never calls a generation
endpoint); WC-O's `safe_*` wrapper contract with its own test suite.

**Risks considered:** a future change accidentally calling
`process_current_report` from the GET path; the frontend triggering
generation; a metrics failure altering control flow.

**Findings:** none. Every boundary crossing found in this session was
either already tested (Gate 2's `test_gate2_no_mutation_and_no_forbidden_calls_across_states`)
or newly tested this session (WC-O's fail-open proofs).

**Classification: CLOSED.**

## 2. Financial and numerical correctness

**Scope inspected:** `fmtAbs`/`fmtPct`/`currencySymbolFor` in
`frontend/src/app/postmortem/[tradeId]/page.tsx`; the backend's
existing MFE/MAE/P&L calculators (unchanged by Wave C — Wave C only
formats and displays already-computed backend values, never
recomputes them client-side).

**Evidence inspected:** `tradePostmortemPage.test.tsx`'s currency
tests (IN/US/unrecognized-market/percentage-never-currency/negative-
sign-ordering — the last added during this audit, see finding
AUDIT-01 below); confirmed no new financial CALCULATION logic exists
in the frontend (it is a pure display layer over backend-computed
`realized_pnl_abs`, `mfe_abs`, `mae_magnitude_abs`).

**Risks considered:** a currency-symbol/sign ordering bug (e.g.
`$-50.00` instead of `-$50.00`); a percentage accidentally carrying a
currency symbol; closure classification inferred from the wrong source.

**Findings:**
- **AUDIT-01** (found and closed during this audit): negative-value
  currency formatting had no test, though the implementation was
  already correct by inspection. Closed in commit `9264898` with a
  new test proving `-$50.00`, never `$-50.00`.

**Classification: CLOSED** (AUDIT-01 resolved).

## 3. Evidence, provenance and claim integrity

**Scope inspected:** `VersionAndProvenance`/`PricePathSection`/
`StructuredReportModel` typing (WC-K-12); supersession proof (WC-K-13);
referential integrity (WC-K-11); claim semantics (WC-K-10); the
frontend's mirrored TypeScript contract and its compile-time governed-
vocabulary assertions (WC-N-01).

**Evidence inspected:** the full Gate 1 real-PostgreSQL test suite
(6 new tests, green on both engines, run 30804393885 and successors);
`currentReportContract.types.ts`'s `@ts-expect-error` assertions,
verified by a clean `tsc --noEmit` (an unused directive would fail the
build if any assertion stopped being a real error).

**Risks considered:** the frontend contract silently drifting from the
backend's governed enums (e.g. a missing `EXTERNAL_UNOFFICIAL_DAILY`
source_type value — found and fixed during the WC-N closure pass,
already closed).

**Findings:** none new.

**Classification: CLOSED.**

## 4. Persistence and PostgreSQL integrity

**Scope inspected:** the outbox claim query
(`claim_next_current_outbox_batch`), the immutable report trigger, the
queue-health snapshot's read-only guarantee, the unique/claim indexes.

**Evidence inspected:** Gate 1/2/3's full real-PostgreSQL matrix (314
tests green); WC-O's `test_outbox_queue_health.py` no-mutation proof
and `test_outbox_queue_health_explain.py`'s `EXPLAIN ANALYZE`
no-mutation proof; the two genuine PostgreSQL-only defects found and
fixed during this wave (a `ON CONFLICT`/synthetic-user-prefix test bug,
and a Python-style underscore numeric literal invalid in SQL) —
listed here for completeness, not as open findings (both closed
same-session, confirmed via a subsequent green dispatch).

**Risks considered:** the queue-health query never being covered by an
index leading to a future performance cliff as the table grows.

**Findings:** none blocking. The absence of a new index is a
deliberate, evidence-based decision (§3 of the observability review),
not a gap — real EXPLAIN evidence at 5,000 rows supports it; the
document states the exact scale at which to re-measure rather than
extrapolate further.

**Classification: CLOSED.**

## 5. Authorization, privacy and caching

**Scope inspected:** `get_current_governed_report`'s auth/ownership
check ordering; `Cache-Control: private, no-store`; every metrics/log
call site's privacy discipline (no prices, P&L, claims, or evidence
content ever logged).

**Evidence inspected:** the indistinguishable-404 tests
(`test_another_users_trade_is_indistinguishable_from_nonexistent`);
the Cache-Control assertion present in every availability-state test;
`current_report_metrics.py`'s own module docstring and the
`test_internal_failure_log_never_includes_the_raw_exception_text` test
proving a metrics-store failure's own log line never leaks the caught
exception's text.

**Risks considered:** a future log line accidentally interpolating raw
report content; the ownership check being bypassed by the capability
check running first (it does not — verified by direct source read and
by `WC-O-10`'s explicit correction of the runbook's earlier incorrect
"404" claim).

**Findings:** none.

**Classification: CLOSED.**

## 6. Concurrency, leases and idempotency

**Scope inspected:** `FOR UPDATE SKIP LOCKED` batch claim; lease
expiry classification; `MAX_ATTEMPTS_BEFORE_TERMINAL`; per-row
isolation in `_process_claimed_row`; worker start/stop idempotency.

**Evidence inspected:** Gate 1/2's real-PostgreSQL concurrency proofs
(inherited from Wave B, re-verified unchanged); WC-O's
`test_outbox_worker_metrics_fail_open.py` proving isolation survives
even when metrics/logging both fail simultaneously;
`test_outbox_worker_shutdown.py`'s pre-existing graceful-shutdown
proofs, re-verified unaffected by this wave's changes.

**Risks considered:** the WC-O correction's change to `_poll_once`
(passing an explicitly-computed version identity into
`claim_next_current_outbox_batch` instead of relying on its internal
default) accidentally changing claim behavior — ruled out: the function
computes the identical values internally when not passed, so this is a
behavior-preserving refactor, confirmed by the unchanged claim-query
test results across all three PG dispatches in this wave.

**Findings:** none.

**Classification: CLOSED.**

## 7. Failures, retries and fail-closed behavior

**Scope inspected:** `INTEGRITY_CONTRADICTION` detection;
`FAILED_RETRYABLE`/`FAILED_TERMINAL` transitions; the metrics/logging
fail-open contract's interaction with real application failures.

**Evidence inspected:** `test_current_report_generation_provider_metrics.py`'s
proof that a real provider exception's outcome (`FAILED_RETRYABLE`) is
unchanged even when the metrics store AND the logging subsystem both
fail simultaneously; `test_current_report_endpoint_metrics_fail_open.py`'s
14 tests proving all seven availability responses are byte-identical
under a forced metrics failure.

**Risks considered:** a metrics/logging failure masking or replacing
the original application exception — explicitly tested and disproven.

**Findings:** none.

**Classification: CLOSED.**

## 8. API and historical compatibility

**Scope inspected:** the legacy `GET /postmortem/{trade_id}` route
(Sprint 1, unrelated response shape) versus the canonical
`GET .../current-report` route (Wave C); no third competing route
introduced; no deprecation without owner approval.

**Evidence inspected:** the ADR's own §2 "Canonical route decision"
(unchanged this wave); no modification to `PostmortemResponse` or the
daily-report route's behavior anywhere in this session's diffs.

**Findings:** none.

**Classification: CLOSED.**

## 9. Frontend correctness and accessibility

**Scope inspected:** all seven availability states' rendering;
COMPLETE/LIMITED_EVIDENCE distinctness; keyboard operability;
`aria-expanded` on every disclosure; status/alert roles; mobile
layout; no color-only meaning (evidence-class labels always pair a
text label with color, never color alone).

**Evidence inspected:** 529 frontend tests (42 in the page test file
alone); the browser-smoke pass (feature-disabled, invalid-trade-ID,
unauthenticated-user routes verified live at desktop and mobile width,
zero console errors); the explicitly recorded limitation that
READY/LIMITED_EVIDENCE states were not verified live (no safe mock/
interception mechanism in this environment) — covered instead by
component tests.

**Risks considered:** the recorded browser-smoke limitation being
understated or later mistaken for full live verification.

**Findings:** none blocking; the limitation itself is the honest,
already-recorded finding (see WC-N-12 in the traceability manifest),
not a gap to silently close.

**Classification: CLOSED**, with the pre-existing NONBLOCKING
disposition for the READY/LIMITED_EVIDENCE live-browser gap carried
forward (see Findings Ledger, AUDIT-02).

## 10. Observability and operational readiness

**Scope inspected:** the entire WC-O correction arc — fail-open
metrics/logging, queue-health snapshot, noise-bounded logging,
provider signals, runbooks.

**Evidence inspected:** all WC-O test files (metrics, queue-health,
worker fail-open, generation provider-metrics, endpoint fail-open);
both Operations documents' explicit disclosures (process-local state,
no dashboard, no cross-replica aggregation, honest log-volume
estimate).

**Findings:** none new — this perspective's substance was already the
subject of the entire WC-O correction cycle documented above.

**Classification: CLOSED.**

## 11. Performance, query cost and capacity

**Scope inspected:** the queue-health query's real EXPLAIN evidence;
the DB connection pool size; the rate limiter; worker throughput
ceiling.

**Evidence inspected:** §3/§7 of the observability review document
(measured 2.3-2.5ms execution at 5,000 rows on both PG15/17,
`max_size=10` pool, `60/minute` rate limit, ~20 rows/minute/replica
worker ceiling).

**Risks considered:** a burst of concurrent GET/POST traffic during a
worker batch approaching the pool ceiling — recorded as a capacity fact
for the owner to weigh before scaling, not silently assumed safe.

**Findings:** none blocking; the capacity assessment is honest about
what it does and does not prove (beta-scale evidence, not unlimited-
growth proof).

**Classification: CLOSED.**

## 12. Release, rollback and activation readiness

**Scope inspected:** both feature flags' default-disabled state; the
runbooks' dark-deployment/enablement/disablement/rollback procedures;
the corrected (no ad hoc SQL) remediation procedure; the evidence-based
global-beta boundary.

**Evidence inspected:** `_trade_postmortem_price_path_enabled()`
returns `False` when the env var is unset (existing, unchanged
behavior); `isTradePostmortemPricePathEnabled()` same convention
frontend-side; the corrected runbook's 9-step remediation procedure;
the corrected controlled-beta section's honest "global, not cohort"
framing.

**Findings:** none blocking. Confirmed: no merge, no deploy, no
production environment variable change, no production PostgreSQL
access, no backfill, and no feature activation occurred at any point
in this entire Wave C effort.

**Classification: CLOSED.**

---

## Findings Ledger

| ID | Perspective | Classification | Evidence | Consequence if unaddressed | Required action | Correction SHA | Verification | Final status |
|---|---|---|---|---|---|---|---|---|
| AUDIT-01 | 2 (financial correctness) | NONBLOCKING → CLOSED | `fmtAbs` had no negative-value test | Low — cosmetic risk only, implementation was already correct by inspection | Add the missing test | `9264898393f77f32f53fa6df31f9123ae0cebb09` | `frontend` suite: 529 passed | CLOSED |
| AUDIT-02 | 9 (frontend/accessibility) | NONBLOCKING (accepted, carried forward) | Browser-smoke could not exercise READY/LIMITED_EVIDENCE live (no safe mock/interception tooling in this environment) | None to safety/correctness — these states are covered by 42 component tests instead; only LIVE visual confirmation is missing | None required to proceed; a future pass with a safe interception mechanism (e.g. MSW) could close this fully | N/A (documented limitation, not a defect) | Component test suite (WC-N-06/07/08/09) | NONBLOCKING, accepted disposition: revisit only if/when a safe live-mocking tool is added to this frontend |

**No BLOCKING findings were identified in this audit.**

---

## Formal audit result

**FORMAL 12-PERSPECTIVE AUDIT COMPLETE — ZERO BLOCKING FINDINGS. ONE
NONBLOCKING FINDING (AUDIT-01) WAS CORRECTED DURING THE AUDIT ITSELF.
ONE NONBLOCKING FINDING (AUDIT-02) IS ACCEPTED WITH AN EXPLICIT FUTURE
DISPOSITION AND DOES NOT COMPROMISE THE RATIFIED CONTRACT, FINANCIAL
CORRECTNESS, AUTHORIZATION/PRIVACY, PERSISTENCE INTEGRITY, CONCURRENCY
SAFETY, OR SAFE DARK DEPLOYMENT/DISABLEMENT/ROLLBACK.**

This replaces "Formal audit blockers not yet assessed" as of commit
`9264898393f77f32f53fa6df31f9123ae0cebb09`.

---

# POST-MAIN-RECONCILIATION AUDIT ADDENDUM

**Trigger:** the local `origin/main` tracking reference used to certify
the audit above was stale — `git config remote.origin.fetch` in this
worktree was scoped only to
`+refs/heads/feature/trade-postmortem-sprint3a-price-path`, with no
refspec for `refs/heads/main`, so repeated `git fetch origin` calls
never updated the local `origin/main` ref even though `git ls-remote
origin refs/heads/main` (a direct GitHub query) always returned the
true current main. Authoritative current main
(`3689d6ffd13aea11fc41d15124b3ab3d8cde1fea`) was 4 commits ahead of the
merge-base the original Stage M believed was current. Repaired via
`git fetch --prune origin +refs/heads/main:refs/remotes/origin/main`;
reconciled via merge commit `c9ced06394f22790725e336b83ae5709f92d0f7f`
(`git merge --no-ff origin/main`, zero conflicts — `postgres_store.py`
auto-merged cleanly, every other file untouched by main's 4 commits).

The four reconciled main commits (`8f56745`, `137b353`, `127899c`,
`3689d6f`) are entirely Daily Picks/alpha-engine Supabase-egress-
containment work: `get_picks_status_metadata` (metadata-only `/status`
reads), TTL caching for picks payloads and IC-engine live-IC data,
shared training-data-join caching, and cache invalidation on
persistence/premarket-finalization/new-outcome events. **None of the
four commits touch any Trade Postmortem file** (`services/postmortem/`,
`api/routers/paper_trading.py`'s current-report route, `outbox_worker.py`,
`current_report_metrics.py`, `outbox_queue_health.py`, or any frontend
Postmortem file) — confirmed by the file list in each commit and by
`git diff a718726 origin/main --stat` showing only the 12 files listed
in Stage M's own known-affected-file list.

## Perspective-by-perspective reassessment

| # | Perspective | Disposition |
|---|---|---|
| 1 | Architecture and authority boundaries | CARRIED FORWARD WITH DIFF EVIDENCE — the reconciled commits add zero new call paths touching the current-report/outbox/worker boundary; `git diff a718726 origin/main --stat` confirms no Postmortem file appears. |
| 2 | Financial/model correctness | **REASSESSED.** `test_ic_engine_live_ic_cache.py` and `test_store_training_data_cache.py` explicitly proven post-merge (33 total tests across both, part of the 173 focused-verification total) — caching never alters a computed IC value or a trained model's output; `test_store_training_data_cache.py` asserts `force_fresh=True` is used for real model training regardless of cache state. No Postmortem financial display logic (`fmtAbs`/`fmtPct`/`currencySymbolFor`) was touched by the reconciled commits. |
| 3 | Evidence, provenance and claim integrity | CARRIED FORWARD WITH DIFF EVIDENCE — untouched by the 4 commits. |
| 4 | PostgreSQL and schema integration | **REASSESSED.** `postgres_store.py`'s merge was inspected line-by-line (§ above) — main's `get_picks_status_metadata` function and the Postmortem branch's own schema additions (outbox/report/price-path-evidence tables, level-history columns/triggers) occupy non-overlapping regions of the file; the merge was a clean auto-merge, not a manually-resolved conflict, eliminating the primary risk of an accidental silent drop. `test_wave_c_traceability_validator.py`'s live subprocess collection (part of the 266-test focused Postmortem verification) re-confirmed every cited Postmortem test still collects correctly post-merge. |
| 5 | Authorization, privacy and caching | **REASSESSED.** `test_picks_ttl_cache.py` and `test_picks_egress_containment_endpoints.py` (173-test focused run) explicitly prove cold/warm `/daily` behavior, metadata-only `/status` reads for both IN and US markets, and stampede-safe concurrent cache misses — all green post-merge. No Postmortem privacy/caching code was touched. |
| 6 | Concurrency, leases and idempotency | CARRIED FORWARD WITH DIFF EVIDENCE — untouched; `test_outbox_worker_metrics_fail_open.py`/`test_outbox_worker_shutdown.py` re-run green post-merge as part of the 266-test Postmortem focused verification. |
| 7 | Failures, retries and fail-closed behavior | CARRIED FORWARD WITH DIFF EVIDENCE — untouched by the reconciled commits; re-verified green post-merge. |
| 8 | API and historical compatibility | **REASSESSED.** `backend/api/routers/picks.py`'s changes are additive (`/api/picks/status` now uses `get_picks_status_metadata`) and scoped entirely to the Daily Picks surface — no interaction with `api/routers/paper_trading.py`'s routes. |
| 9 | Frontend correctness and accessibility | CARRIED FORWARD WITH DIFF EVIDENCE — the reconciled main commits contain zero frontend changes (confirmed: none of the 12 known-affected files are under `frontend/`); no frontend re-verification triggered by reconciliation itself (still re-run in Step 6 as the required once-per-final-candidate cycle). |
| 10 | Observability and operational readiness | CARRIED FORWARD WITH DIFF EVIDENCE — untouched; WC-O's own metrics/logging modules re-verified green post-merge (266-test focused run). |
| 11 | Performance, database egress and capacity | **REASSESSED.** This is the perspective the reconciled commits are MOST relevant to (they exist specifically to reduce Supabase egress) — `test_picks_egress_containment_endpoints.py`'s read-count assertions (`test_cold_daily_performs_at_most_one_full_payload_read` and siblings) confirm the egress-reduction behavior is present and passing at the reconciled SHA, independent of and unaffected by the Postmortem branch's own query-cost work (§O-07). |
| 12 | Release, rollback and activation readiness | **REASSESSED.** Both Postmortem feature flags remain default-disabled after reconciliation (unchanged files: `paper_trading.py`'s `_trade_postmortem_price_path_enabled`, `featureFlags.ts`'s `isTradePostmortemPricePathEnabled` — neither appears in the reconciled diff). No production configuration changed by the reconciliation itself (a local merge commit, never touching any deployed environment). |

## Explicit verifications required by this addendum

- **`/status` remains metadata-only:** confirmed —
  `get_picks_status_metadata` (main, `postgres_store.py`) uses
  JSONB path operators (`->>`) to extract only
  `generated_at`/`base_generated_at`/`premarket_finalized_at`/
  `premarket_status`/`premarket_finalizer_version`, never the full
  payload; `test_picks_egress_containment_endpoints.py` passes.
- **All cache invalidation remains present:** confirmed —
  `test_picks_ttl_cache.py` and `test_store_training_data_cache.py`
  (persistence/premarket-finalization/outcome-driven invalidation)
  pass.
- **Force-fresh training remains authoritative:** confirmed —
  `test_store_training_data_cache.py` asserts real model training uses
  `force_fresh=True` regardless of cache state.
- **Caching does not alter financial/model outputs:** confirmed — the
  same test suite asserts numerical/model behavior is unchanged by the
  cache layer.
- **`postgres_store.py` preserves both sets of changes:** confirmed by
  direct inspection (§ above) and by the clean auto-merge with zero
  manual conflict resolution.
- **Postmortem behavior remains unchanged:** confirmed — 266 focused
  Postmortem tests pass post-merge with zero modification to any
  Postmortem source file during reconciliation.
- **Feature flags remain disabled:** confirmed — neither flag-reading
  function was touched by the reconciled commits.

## Findings ledger addendum

No new BLOCKING or NONBLOCKING findings were identified during this
reconciliation reassessment. AUDIT-01 and AUDIT-02 from the original
audit remain as previously dispositioned (AUDIT-01 CLOSED, AUDIT-02
accepted NONBLOCKING with its existing future disposition) — neither is
affected by the reconciled main commits.

**POST-MAIN-RECONCILIATION AUDIT RESULT: ZERO BLOCKING FINDINGS. ALL
REASSESSED PERSPECTIVES CONFIRM THE RECONCILED CANDIDATE PRESERVES BOTH
MAIN'S EGRESS-CONTAINMENT WORK AND THE COMPLETE TRADE POSTMORTEM
BEHAVIOR, WITH NO REGRESSION TO EITHER.**
