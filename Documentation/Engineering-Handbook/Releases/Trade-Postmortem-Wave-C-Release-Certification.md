# Trade Postmortem — Wave C Release Certification

**Status: certified for dark deployment only. Not merged. Not
activated.** This document certifies that Wave C (WC-K current-report
read API, WC-N per-trade frontend, WC-O observability) is complete,
tested, and ready for Owner Gate 1 (merge and dark deploy) approval.
Nothing in this document authorizes merging, deploying, activating
either feature flag, or touching production.

## Identifiers

- **Final executable candidate SHA:** `901bba15c36c9e4a35ea30c99177d2e5b1be0509`
- **Final documentation SHA (this certification + traceability
  evidence update):** `d4c4fe3b68f3db391dc553f8dcc644d84197dffc`
- **Main reconciliation:** none required — `origin/main`
  (`a71872641657169da1d5c2f5e9511393d7f0bb9d`) is exactly the merge-base
  with this branch, confirmed via a fresh `git fetch` immediately before
  Stage M; `git merge origin/main` reported "Already up to date." Zero
  commits of drift existed to reconcile.
- **Branch:** `feature/trade-postmortem-sprint3a-price-path`
- **Base:** `main`

## Package closure evidence

| Package | Status | Closure SHA | Key PostgreSQL evidence |
|---|---|---|---|
| WC-K (current-report read API) | CLOSED | `7432851ea7affa7a80e8db131337763c7cbb69eb` | Run 30804393885, PG15/17 314/314 passed |
| WC-N (per-trade frontend) | CLOSED | `d9096a9b16c2267a5cd6c4fac866467acffe07df` | N/A (frontend-only); 528 frontend tests, typecheck/build green |
| WC-O (observability) | CLOSED | `85d7edba540018f7b8c1fb625126fe25edceb49f` | Run 30833627292, PG15/17 321/321 passed; real EXPLAIN evidence captured |

## Formal audit result

`Documentation/Engineering-Handbook/ADR/Trade-Postmortem-Wave-C-Formal-12-Perspective-Audit.md`
— **zero BLOCKING findings** across all 12 required perspectives. One
NONBLOCKING finding (AUDIT-01, untested negative-currency-formatting
edge case) was found and corrected during the audit itself (commit
`9264898`). One NONBLOCKING finding (AUDIT-02, the browser-smoke
limitation for READY/LIMITED_EVIDENCE live states) is accepted with an
explicit future disposition — it does not compromise the ratified
contract, financial correctness, authorization/privacy, persistence
integrity, concurrency safety, or safe dark deployment/disablement/
rollback.

## Backend assurance (final exact-head cycle)

- Non-PostgreSQL suite: **5659 passed, 322 skipped, 0 failed**.
- PostgreSQL 15: **321 passed, 0 failed, 0 errors, 0 skipped** —
  workflow run [30836665992](https://github.com/shashiraj75/stocksense/actions/runs/30836665992),
  job `91763386468`, artifact `postgres-integration-results-pg15`
  (id `8865140616`, digest
  `sha256:46b604991caa18d71ce1f81f5e2ecaea32b18f61e2c446c7ec9aa6f21d9a4153`).
- PostgreSQL 17: **321 passed, 0 failed, 0 errors, 0 skipped** — job
  `91763386393`, artifact `postgres-integration-results-pg17`
  (id `8865144274`, digest
  `sha256:3815d9fa414e614ed35bf6f66929344add221cbc76bae25f865b5a3283e9b05b`).

## Frontend assurance (final exact-head cycle)

- Complete test suite: **529 passed, 0 failed**.
- TypeScript typecheck: **0 errors**.
- Production build (temporary, non-production
  `NEXT_PUBLIC_SUPABASE_URL`/`_ANON_KEY`/`_API_URL` values for
  build-verification only — never committed, never a real credential):
  succeeded. Route list unchanged except the new `/postmortem/[tradeId]`
  dynamic route — no unintended route or feature exposure.

## Browser-smoke scope and limitations

Verified live (temporary `npm run dev -- -p <port>`, non-production env
values, no changes to `.claude/launch.json`): the feature-disabled
route, the invalid-trade-ID route, the unauthenticated-user route, at
both desktop and mobile (375px) widths, with zero console errors in
every case.

**Not verified live** (accepted NONBLOCKING limitation, AUDIT-02):
READY COMPLETE and READY LIMITED_EVIDENCE layouts — this environment
has no safe mock/interception mechanism (e.g. MSW) for producing a real
authenticated session with controlled backend responses. These states
are instead covered by 42 passing component tests
(`tradePostmortemPage.test.tsx`).

## Remaining NONBLOCKING findings

| ID | Description | Disposition |
|---|---|---|
| AUDIT-02 | Browser-smoke could not exercise READY/LIMITED_EVIDENCE live | Accepted — covered by component tests; revisit only if a safe live-mocking tool is added to this frontend |

## Feature-flag confirmation

Both flags remain **disabled by default** in this candidate:

- Backend: `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` — unset resolves to
  disabled (`_trade_postmortem_price_path_enabled()`,
  `backend/api/routers/paper_trading.py`).
- Frontend: `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` — unset
  resolves to disabled (`isTradePostmortemPricePathEnabled()`,
  `frontend/src/utils/featureFlags.ts`).

## Confirmation of no production action

This entire Wave C effort, across every stage recorded in this
repository's commit history, performed **no merge, no deployment, no
production environment variable change, no production PostgreSQL
access, no production backfill, and no activation of either Postmortem
feature flag.** All PostgreSQL evidence in this certification comes
from the GitHub Actions CI matrix (ephemeral PostgreSQL 15/17
instances), never a production database.

## Next step

Stage P: open the pull request `feature/trade-postmortem-sprint3a-price-path`
→ `main`, referencing this certification. Merge requires explicit
**OWNER APPROVAL: MERGE AND DARK DEPLOY** — not granted by this
document.
