# StockSense360 — Current Release Status

**Purpose:** This document is the authoritative operational-status register for live and pending releases. It records what is deployed, what remains disabled, what is pending validation, and which future actions require explicit approval.

**Use this document for current state.** Historical sprint reports, Epic closures, SSDS documents, and audit reports remain authoritative evidence for their own completed scope, but they do not automatically describe the current production operating state.

**As of:** 2026-07-03 — maintained as a live operational register

---

## Release 12B — Daily Picks Universe and Reliability Validation

**Status:** Deployed — controlled production validation pending.

- India validation requires a genuine fresh post-release generation window.
- US validation requires a normal US market session and a separate controlled validation.
- No Daily Picks scheduler enablement is approved until India and US validations both pass.
- No validation result may be described as passed until its release-specific evidence record is complete.

## Release 13C — Recommendation Consolidation Observability

**Status:** Deployed — RCI remains disabled.

- Aggregate RCI composition-success and fail-open counters are deployed for controlled operational observation.
- Counters reset on service restart and are not per-symbol, per-user, or persistent telemetry.
- Counter availability does not itself prove RCI correctness or authorize activation.

## Release 13D — Recommendation Consolidation Activation Readiness

**Status:** Runbook complete — activation not approved.

- `RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains disabled.
- The existing Evidence Summary frontend consumer is already deployed.
- Any future RCI activation is user-visible on the Stock Detail page; it is not a backend-only dark launch.
- RCI activation, Daily Picks validation, and scheduler enablement remain separate approval decisions.

## Release 14B — Debug Endpoint Security Hardening

**Status:** Deployed — deployment status checks passed; protected-endpoint runtime verification remains pending.

- `/api/predictions/debug/state` now requires a configured non-empty `PICKS_SECRET` and a matching non-empty `X-Secret` header.
- Missing, blank, whitespace-only, or incorrect secret values fail closed with a generic `401`.
- The endpoint now returns aggregate operational counts only; raw cache identifiers, in-flight identifiers, and raw background-log content are no longer returned.
- The change is isolated from Release 12B validation, RCI activation, and scheduler enablement.

## Operational Safety Rules

- No scheduler enablement before both India and US Release 12B validations pass.
- No RCI feature-flag change without explicit approval.
- No production-status documentation may claim a validation passed without recorded evidence.
- Historical test totals remain historical snapshots. Current test status must be taken from the latest validated release or CI evidence.
