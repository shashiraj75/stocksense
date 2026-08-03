# Trade Postmortem — Wave C Release Certification

**Status: certified for dark deployment only. Current main has been
merged into the feature branch. PR #35 has not been merged into main.
No deployment occurred. No production environment change occurred. No
production PostgreSQL operation occurred. No backfill occurred. No
activation occurred.** This document certifies that Wave C (WC-K
current-report read API, WC-N per-trade frontend, WC-O observability),
reconciled against current `main`, is complete, tested, and ready for
Owner Gate 1 (merge and dark deploy) approval. Nothing in this document
authorizes merging, deploying, activating either feature flag, or
touching production.

## Correction notice

An earlier version of this document (as of documentation SHA `f900bb2`)
incorrectly stated that `origin/main` was still `a718726...` and that
zero reconciliation was required. That conclusion was based on a stale
local Git reference: this worktree's `git config remote.origin.fetch`
was scoped only to
`+refs/heads/feature/trade-postmortem-sprint3a-price-path`, with no
refspec for `refs/heads/main` — so a plain `git fetch origin` silently
never updated the local `origin/main` tracking ref, even though
`git ls-remote origin refs/heads/main` (a direct GitHub query,
independent of local refspecs) always returned the true current main.
Authoritative current main was actually
`3689d6ffd13aea11fc41d15124b3ab3d8cde1fea`, 4 commits ahead of the
believed merge-base. This has been repaired and reconciled; see below.

## Identifiers

- **Pre-reconciliation main believed:** `a71872641657169da1d5c2f5e9511393d7f0bb9d` (was actually only the merge-base, not current main)
- **Actual authoritative main before reconciliation:** `3689d6ffd13aea11fc41d15124b3ab3d8cde1fea`
- **Commits behind before reconciliation:** 4
- **Reconciliation merge SHA:** `c9ced06394f22790725e336b83ae5709f92d0f7f` (`git merge --no-ff origin/main`, zero conflicts)
- **Final executable candidate SHA (frozen):** `5b3fb964ddae7782aa28fb9c290a403e8838608a`
- **Final documentation SHA (this corrected certification):** set by the commit that follows this file's update
- **`origin/main` at final diff review:** `3689d6ffd13aea11fc41d15124b3ab3d8cde1fea` — merge-base with the final candidate is now identical (0 commits behind)
- **Branch:** `feature/trade-postmortem-sprint3a-price-path`
- **Base:** `main`
- **Open PR:** [#35](https://github.com/shashiraj75/stocksense/pull/35)

## Main reconciliation

The 4 reconciled main commits (`8f56745`, `137b353`, `127899c`,
`3689d6f`) are entirely Daily Picks/alpha-engine Supabase-egress-
containment work — `get_picks_status_metadata` (metadata-only
`/status` reads), TTL caching for picks payloads and IC-engine live-IC
data, shared training-data-join caching, and cache invalidation on
persistence/premarket-finalization/new-outcome events. **None of the
four commits touch any Trade Postmortem file.**

`backend/services/postgres_store.py` was the only file both sides
modified — it auto-merged cleanly with **zero conflicts**: main's new
`get_picks_status_metadata` function and the Postmortem branch's own
schema additions (outbox/report/price-path-evidence tables,
level-history columns/triggers) occupy non-overlapping regions of the
file.

Focused post-merge verification: **173 egress-containment/TTL-cache/
training-data-cache/IC-cache/market-integrity tests passed**; **266
focused Postmortem tests passed** (including the traceability
validator's own live subprocess collection, re-confirming every cited
test still collects post-merge). No PostgreSQL dispatch was needed at
the intermediate reconciliation step — no merge conflict changed
PostgreSQL SQL/schema behavior, and focused tests fully proved the
resolution.

## Package closure evidence (unchanged by reconciliation)

| Package | Status | Closure SHA | Key PostgreSQL evidence |
|---|---|---|---|
| WC-K (current-report read API) | CLOSED | `7432851ea7affa7a80e8db131337763c7cbb69eb` | Run 30804393885, PG15/17 314/314 passed |
| WC-N (per-trade frontend) | CLOSED | `d9096a9b16c2267a5cd6c4fac866467acffe07df` | N/A (frontend-only); 528 frontend tests, typecheck/build green |
| WC-O (observability) | CLOSED | `85d7edba540018f7b8c1fb625126fe25edceb49f` | Run 30833627292, PG15/17 321/321 passed; real EXPLAIN evidence captured |

## Formal audit result

`Documentation/Engineering-Handbook/ADR/Trade-Postmortem-Wave-C-Formal-12-Perspective-Audit.md`
— original audit: **zero BLOCKING findings** across all 12 required
perspectives. One NONBLOCKING finding (AUDIT-01, untested negative-
currency-formatting edge case) was found and corrected during the
audit itself. One NONBLOCKING finding (AUDIT-02, the browser-smoke
limitation for READY/LIMITED_EVIDENCE live states) is accepted with an
explicit future disposition.

The same document's **POST-MAIN-RECONCILIATION AUDIT ADDENDUM**
reassessed financial/model correctness, PostgreSQL/schema integration,
authorization/privacy/caching, API compatibility, database egress, and
release/rollback readiness against the 4 reconciled commits with real
diff evidence — result: **zero new BLOCKING or NONBLOCKING findings**;
AUDIT-01/AUDIT-02 dispositions unchanged.

## Backend assurance (final exact-head cycle, post-reconciliation)

- Non-PostgreSQL suite: **5694 passed, 322 skipped, 0 failed** (up from
  5659 pre-reconciliation — the increase is main's own new egress-
  containment tests, now included).
- PostgreSQL 15: **321 passed, 0 failed, 0 errors, 0 skipped** —
  workflow run [30841945629](https://github.com/shashiraj75/stocksense/actions/runs/30841945629)
  (auto-triggered by the reconciliation push to PR #35), job
  `91780866537`, artifact `postgres-integration-results-pg15`
  (id `8867169669`, digest
  `sha256:23b03d56179d639961f3dfe44cb9e3587af402d99f9b82306fd724af67b3b95a`).
- PostgreSQL 17: **321 passed, 0 failed, 0 errors, 0 skipped** — job
  `91780866601`, artifact `postgres-integration-results-pg17`
  (id `8867165422`, digest
  `sha256:cca324edffe9324f84f083916f8360d20c71bfd8ac9821bec852e779fd7c1719`).

## Frontend assurance (final exact-head cycle, post-reconciliation)

- Complete test suite: **529 passed, 0 failed**.
- TypeScript typecheck: **0 errors**.
- Production build: succeeded, route list unchanged (the 4 reconciled
  main commits contain zero frontend files — only the previously-added
  `/postmortem/[tradeId]` dynamic route is new, no unintended exposure).

## Browser-smoke scope and limitations (carried forward)

No frontend executable file changed during reconciliation or audit
correction, so the existing browser-smoke evidence (from WC-N closure,
commit `d9096a9`) is carried forward rather than re-run: the feature-
disabled route, the invalid-trade-ID route, the unauthenticated-user
route, at both desktop and mobile (375px) widths, zero console errors.

**Not verified live** (accepted NONBLOCKING limitation, AUDIT-02):
READY COMPLETE and READY LIMITED_EVIDENCE layouts — no safe mock/
interception mechanism exists in this environment. Covered instead by
42 passing component tests.

## Remaining NONBLOCKING findings

| ID | Description | Disposition |
|---|---|---|
| AUDIT-02 | Browser-smoke could not exercise READY/LIMITED_EVIDENCE live | Accepted — covered by component tests; revisit only if a safe live-mocking tool is added |

## Feature-flag confirmation

Both flags remain **disabled by default** in the reconciled candidate:

- Backend: `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` — `os.getenv(..., "0")`, unset/`"0"` resolves to disabled (`_trade_postmortem_price_path_enabled()`, `backend/api/routers/paper_trading.py`) — untouched by the reconciled commits.
- Frontend: `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` — unset resolves to disabled (`isTradePostmortemPricePathEnabled()`, `frontend/src/utils/featureFlags.ts`) — untouched by the reconciled commits (which contain no frontend files at all).

## Egress-containment preservation confirmation

- `/status` remains metadata-only (`get_picks_status_metadata` uses JSONB path operators, never the full payload).
- All cache invalidation (persistence, premarket-finalization, outcome-driven) remains present.
- Real model training remains `force_fresh=True`, unaffected by caching.
- Caching does not alter any financial/model output.
- `postgres_store.py` preserves both main's and the Postmortem branch's schema additions with zero conflict.

## Confirmation of no production action

Current main was merged into the feature branch via a local merge
commit. PR #35 has not been merged into main. No deployment occurred.
No production environment variable changed. No production PostgreSQL
operation occurred. No production backfill occurred. No feature was
activated. All PostgreSQL evidence in this certification comes from
the GitHub Actions CI matrix (ephemeral PostgreSQL 15/17 instances),
never a production database.

## Next step

PR #35 has been updated with this corrected reconciliation, evidence,
and certification. Merge requires explicit **OWNER APPROVAL: MERGE AND
DARK DEPLOY** — not granted by this document.
