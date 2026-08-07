# Trade Postmortem — Explainability Production Closure

**Status: LIVE. Merged, deployed, and activated in Production.**

This document is the authoritative closure record for Trade Postmortem
(Wave C base capability plus the Explainability/UX overhaul). It supersedes,
for current-state purposes, the pre-activation
[Wave C Release Certification](./Trade-Postmortem-Wave-C-Release-Certification.md),
which remains as a historical record of the pre-merge state and is not
current.

## Merge record

- **PR #35** (Wave C: WC-K current-report read API, WC-N per-trade frontend,
  WC-O observability) — merged, dark-deployed behind feature flags.
- **PR #36** ("Trade Postmortem explainability and evidence coverage",
  branch `feature/postmortem-explainability-phase`) — merged into `main` at
  commit `5170692f27b1742406a21d67fca8a74d62490c1f`. This PR delivered the
  three-layer report architecture, factor-specific price-path assessments,
  stock identity (`Company Name (SYMBOL)`), and the evidence-coverage
  matrix. Verified against GitHub: `git log` shows `5170692f...` as a merge
  commit ancestor of current `origin/main`, one commit behind HEAD
  (`8f9693b0eecc5ba68490dee4d44b8ba86f5f079d`, an unrelated watchlist
  DELETE-encoding fix).

## Production activation state

**Corrected 2026-08-07** (post-Jul-11 documentation reconciliation, PR #37):
this section previously stated that both Trade Postmortem flag pairs "are
reported enabled in Production," which conflated two independent, separately
gated features. `TRADE_POSTMORTEM_DAILY_ENABLED` /
`NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED` is the older Sprint 1 daily
surface (`frontend/src/utils/featureFlags.ts`: `isTradePostmortemDailyEnabled()`,
a fail-closed presentation gate, repo default disabled) — a genuinely
separate feature from the per-trade Wave C/Explainability release this
document closes. `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` /
`NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` gates the
`/postmortem/[tradeId]` route this document is actually about.

- The **`TRADE_POSTMORTEM_PRICE_PATH_ENABLED`** pair (backend + frontend) is
  reported enabled in Production per this closure's own validation evidence
  below (natural production lifecycle verification on trade 280, frontend
  activation, authenticated Production smoke test).
- The **`TRADE_POSTMORTEM_DAILY_ENABLED`** pair's Production runtime state
  was **not independently verified during this reconciliation** — it belongs
  to the separate Sprint 1 daily surface, not this PR #35/#36 release, and no
  evidence in this document's own validation record below speaks to it. Do
  not infer its state from this closure record.
- Authenticated route: `/postmortem/[tradeId]` (frontend).
- Read-only API: `GET /api/paper-trading/{tradeId}/current-report`
  (backend, `backend/api/routers/paper_trading.py`) — GET-only, no
  production write/backfill path.
- Schema version 1.2.0 (report schema); daily-flag schema version 2.0.0
  (`DAILY_POSTMORTEM_SCHEMA_VERSION` in `paper_trading.py`).

## Reported production validation evidence

The following operational milestones are recorded per the release owner's
closure narrative. They describe infrastructure/runtime state (feature-flag
values in the live Railway/Vercel environments, live traffic behavior) that
is not independently derivable from the repository checkout used to author
this document, and are recorded here as the release-of-record rather than
re-derived from logs by this pass:

- 48-hour backend-only stability observation: passed.
- Natural production lifecycle verification on a real trade (trade 280,
  EMMVEE), completing to `READY`/`LIMITED_EVIDENCE` status.
- Frontend production activation: passed.
- Authenticated Production smoke test: passed.
- Company Name (SYMBOL) identity gate: passed.
- Temporary Preview frontend flag and temporary Railway Preview CORS
  authorization (both granted for QA only): removed after QA completion.
- Final 60-minute Production observation: passed, zero rollback threshold
  crossed.

## Feature summary (see the full architecture chapter in
[`Documentation/STOCKSENSE_DOCUMENTATION.md`](../../STOCKSENSE_DOCUMENTATION.md)
for details)

Deterministic executive summary; ₹/US$ formatting; factor-by-factor
explainability; "What You Can Learn" classifications (CONFIRMED / SUPPORTED
BUT NOT PROVEN / NOT ESTABLISHED / DATA NEEDED FOR A DEEPER REPORT); MFE,
MAE, Target Level Touched, Stop Level Touched, Touch Sequence; factor-specific
price-path assessment as the investor-facing semantic authority, with the
original governed `evidence_class` preserved and visible in expandable
Layer-3 audit details; evidence gaps/warnings, source manifest,
version/provenance, and evidence manifest; outbox worker lifecycle,
queue-health heartbeat, provider-acquisition metrics, and fail-open
observability.

## Classification

LIVE USER-FACING (frontend `/postmortem/[tradeId]` route) and LIVE BACKEND
OPERATIONAL (current-report generation, outbox worker, observability). Not
dormant, not shadow, not feature-flagged off.

## Scope note on evidence completeness

Production activation is complete and stable. Separately, the evidence
*coverage* matrix (see
[Trade-Postmortem-Evidence-Coverage-Matrix.md](../Operations/Trade-Postmortem-Evidence-Coverage-Matrix.md))
identifies factors where the underlying evidence capture is still partial
(remediation categories, not release instability) — tracked in the
[Evidence Completion Roadmap](../Operations/Trade-Postmortem-Evidence-Completion-Roadmap.md).
