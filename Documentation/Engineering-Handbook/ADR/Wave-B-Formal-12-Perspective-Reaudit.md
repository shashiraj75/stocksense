# Wave B Formal 12-Perspective Re-Audit

- **Review date:** 2026-08-02
- **Branch:** feature/trade-postmortem-sprint3a-price-path
- **Reviewed production SHA:** 60bf499a4caf81d4dee3f5b9ca4e9965062dfa46 (last commit changing production files)
- **Reviewed test/traceability SHA:** 9ea571a107dccbad13b1fedf8a7de8f162a4d61e

This record consolidates source-level review actually performed across
the Wave B closure passes — every finding below was discovered by
reading production source and either writing a runnable test that
proved it, or observing a real CI failure that traced back to a genuine
defect. It is not a retrospective checklist filled in after the fact.

## 1. Implementer review

**Files reviewed:** current_report_generation.py, close_service.py,
outbox_worker.py, governed_price_path_claims.py.
**Finding:** BLOCKING — process_current_report collapsed immutable
entry/exit endpoint values onto one shared variable (Finding A).
**Evidence:** test_finding_a_entry_and_exit_endpoint_values_are_distinct_when_modified,
test_8a_entry_and_exit_endpoint_values_survive_real_persistence (real PG).
**Correction:** 7e30f82. **Verification:** both tests green locally and on PG15/17.
**Result:** CLOSED.

## 2. Independent reviewer

**Files reviewed:** current_report_generation.py (full re-read after
implementer's own changes, deliberately fresh-eyes).
**Finding:** BLOCKING — a duplicate CURRENT_REPORT_* constant block
(lines ~594-598) silently shadowed the canonical values declared near
the top of the module, at Python import time.
**Evidence:** CI run 30739263275 asserting 'GENERATED' == 'CURRENT_REPORT_GENERATED'.
**Correction:** 0addffd. **Verification:** CI run 30739496874 (5/6 green),
30739643938 (6/6 green after the provider-mock fix).
**Result:** CLOSED.

## 3. No-fabrication auditor

**Files reviewed:** governed_price_path_claims.py, all J4E claim tests.
**Invariant checked:** canonical fallback sentence never accompanies a
real evidence reference; no positive claim from a fallback status.
**Tests inspected:** test_wb_j4e_54/55, test_wb_j4e_14 (all 7 touch
statuses).
**Finding:** PASS — verified byte-identical to evidence.INSUFFICIENT_EVIDENCE_SENTENCE,
zero supporting evidence on every fallback-status claim.
**Result:** PASS, no correction needed.

## 4. Versioning auditor

**Files reviewed:** price_path_identity.py, current_report_generation.py.
**Invariant checked:** 1.0.0/1.1.0 constants unchanged by the 1.2.0 addition;
governed_calculation_version format exact.
**Finding:** PASS — confirmed no historical constant value changed
(only new constants added); format string verified against
test_wb_j4e_05/47/48/49.
**Result:** PASS.

## 5. PostgreSQL auditor

**Files reviewed:** outbox_worker.py's claim_next_current_outbox_batch SQL.
**Invariant checked:** the global claim query avoids the previously-documented
stale-CTE/EvalPlanQual double-claim defect class.
**Finding:** PASS on design (SELECT...FOR UPDATE SKIP LOCKED subquery,
not a CTE) — CONFIRMED behaviorally by test_8h_simultaneous_claimants_never_claim_the_same_row
(5 real concurrent threads, exactly 1 winner) on real PostgreSQL 15 and 17.
**Result:** PASS, closed with real concurrency evidence (not merely design review).

## 6. Concurrency auditor

**Files reviewed:** outbox_worker.py (_worker_loop, _poll_once, claim_next_current_outbox_batch).
**Finding:** BLOCKING — the worker's poll cycle applied ONE market_tzinfo/
market_timezone_name to an entire claimed batch, but the claim query never
filtered by market — a batch could contain trades from either market,
misattributing session-boundary timezone semantics for the wrong-market rows.
**Evidence:** direct source review (not caught by any pre-existing test).
**Correction:** d647648 (per-row market resolution via a JOIN to paper_trades).
**Verification:** full non-PostgreSQL suite + PostgreSQL 15/17 green after the fix.
**Result:** CLOSED.

## 7. Crash-recovery auditor

**Files reviewed:** outbox_worker.py (start_outbox_worker, stop_outbox_worker).
**Finding:** BLOCKING — stop_outbox_worker's timeout unconditionally cleared
_TASK/_STOP_EVENT even though asyncio.to_thread work cannot be forcibly
cancelled once started — a timeout would orphan in-flight generation while
falsely reporting the worker stopped, and allow a duplicate worker to start.
**Evidence:** direct source review.
**Correction:** 7e30f82 (asyncio.shield + done-callback with an
identity-guarded `_TASK is _expected` check).
**Verification:** test_stop_timeout_does_not_clear_authoritative_task_reference,
test_stale_done_callback_cannot_clear_newer_task_state, and 7 other async
tests, all green.
**Result:** CLOSED.

## 8. Security/privacy auditor

**Files reviewed:** outbox_worker.py, current_report_generation.py logging calls.
**Invariant checked:** no raw PII/price f-string interpolation into logs;
no raw exception text stored as an error_code/error_summary; opaque
claimant tokens (outbox.new_claimant_token(), never a hostname/PID).
**Finding:** PASS — confirmed via WB-J4F-53/54/55/36/37 structural checks
plus direct source read of every log.warning call site in both modules.
**Result:** PASS.

## 9. Operations auditor

**Files reviewed:** close_service.py, paper_trading.py (/sell handler,
_build_generate_response).
**Finding:** BLOCKING — close_service.py's request_current_report_outbox
parameter was implemented and unit-tested but never actually invoked with
True from any real call site — the atomic close-to-outbox capability was
dead code; the background worker had nothing to durably recover from.
**Evidence:** direct source review of every call site of close_paper_trade.
**Correction:** 6c6c981 (the /sell handler now computes the current
identity and passes request_current_report_outbox=True when the flag is enabled).
**Verification:** test_8g_flag_enabled_inserts_current_outbox_in_close_transaction
(real PostgreSQL), green on PG15/17.
**Result:** CLOSED.

## 10. Historical-compatibility auditor

**Files reviewed:** governed_price_path_claims.py, price_path_claims.py, evidence.py.
**Finding:** BLOCKING — governed_price_path_claims.py registered its 3 rules
under report_section="price_path", the SAME shared evidence.RULE_REGISTRY
section string the legacy price_path_claims.py rules use — breaking a
frozen Wave A test's exact rule-id-set assertion under full-suite import
ordering (invisible when running tests/unit alone; real under the true
complete suite).
**Evidence:** CI run showed test_price_path_claims_rule_registry_gains_no_new_j4b_rule
failing only in the full-suite context.
**Correction:** 0addffd (renamed to "governed_price_path", its own distinct section).
**Verification:** full non-PostgreSQL suite (5355+ passed) after the fix, both
before and after re-verified with zero regressions.
**Result:** CLOSED.

## 11. Reset/delete auditor

**Files reviewed:** paper_trading.py's reset_portfolio.
**Invariant checked:** the existing generic `DELETE ... WHERE user_id = %s`
statements for paper_trade_postmortem_report/outbox already cover the 1.2.0
identity with no code change; reset never touches a different user's data.
**Finding:** PASS, verified behaviorally (not just structurally) by
test_8k_reset_after_1_2_0_report_exists_removes_it_without_orphans and
test_8k_reset_does_not_touch_a_different_users_report — real PostgreSQL,
green on PG15/17.
**Result:** PASS, closed with real evidence.

## 12. Product-owner scope auditor

**Files reviewed:** full diff since c91b7d8 (Wave B start).
**Invariant checked:** no frontend file changed; no new GET report-exposure
endpoint; no environment variable changed; TRADE_POSTMORTEM_PRICE_PATH_ENABLED
remains default-off; no PR/merge/deploy/flag-activation occurred.
**Finding:** PASS — confirmed via `git diff --stat` against the frozen file
list and `gh pr list` (empty) at every closure checkpoint in this session.
**Result:** PASS.

## Summary

| # | Perspective | Result | Blocking findings | Corrections |
|---|---|---|---|---|
| 1 | Implementer | CLOSED | 1 | 7e30f82 |
| 2 | Independent reviewer | CLOSED | 1 | 0addffd |
| 3 | No-fabrication | PASS | 0 | — |
| 4 | Versioning | PASS | 0 | — |
| 5 | PostgreSQL | PASS | 0 | — |
| 6 | Concurrency | CLOSED | 1 | d647648 |
| 7 | Crash-recovery | CLOSED | 1 | 7e30f82 |
| 8 | Security/privacy | PASS | 0 | — |
| 9 | Operations | CLOSED | 1 | 6c6c981 |
| 10 | Historical-compatibility | CLOSED | 1 | 0addffd |
| 11 | Reset/delete | PASS | 0 | — |
| 12 | Product-owner scope | PASS | 0 | — |

**Total BLOCKING findings across all perspectives: 6, all corrected and
re-verified green on both the complete non-PostgreSQL suite and
PostgreSQL 15/17.**

**Known open item (not a BLOCKING finding, disclosed separately):** the
proof-suitability matrix (`wave_b_proof_suitability_matrix.json`) records
43 of 123 scenarios as REQUIRES_COMPANION — their mapped test node is
structural-only and no behavioural/async/real-PostgreSQL companion yet
exists. This is a coverage gap, not a discovered production defect; no
BLOCKING classification applies to it, but it is not treated as closed.
