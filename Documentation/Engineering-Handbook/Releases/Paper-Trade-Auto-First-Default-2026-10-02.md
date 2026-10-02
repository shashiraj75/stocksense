# Paper Trade — Auto-first, Auto-default (2026-10-02)

**State:** Draft PR; NOT merged or deployed. Owner acceptance and green CI required before release.

## Purpose and user-facing behavior
The new Buy modal shows Trade Management in the order **Auto · Manual · AI**, with **Auto** selected on each fresh modal mount. Choosing Manual still works and is persisted as `trade_management_mode: "manual"` for that purchase. AI stays disabled ("Coming Soon"). The explicitly selected mode, including the new Auto default, is sent in the existing Buy payload.

## Scope and compatibility
- Affected: `frontend/src/components/PaperTradeModal.tsx` and its focused regression tests.
- Unchanged: existing paper positions and their stored modes; portfolio mode-edit controls; backend API/schema and legacy default; server-side exit monitor; recommendation scoring, prediction evidence, horizons, and risk-based sizing; India/US separation; real-money trading (not part of this feature).
- No data migration, scheduler change, provider call, or extra Railway worker.
- This is an intentional default change for **new modal-based paper purchases only**, not a retroactive conversion of existing positions or a change to API clients that omit the mode.

## Auto-mode disclosure / known limitations
Auto exits are evaluated using available live quotes during market hours on the existing periodic monitor, currently scheduled approximately every five minutes. Trades without a positive Stop Loss or Target Price have no automatic trigger to check. An automated exit is not a guaranteed stop-order fill at the exact threshold or a continuous price watch. Manual remains selectable before submitting a paper Buy.

The no-trigger Auto case and exact-price fill limitations predate this preference change. Any future guard or copy redesign should be separately reviewed rather than silently changing the API or trade validation in this narrow UI change.

## Verification and acceptance
Focused React/Vitest regression covers option order, default selection, submitted Auto mode with visible stop/target, explicit Manual override, disabled AI, and a fresh mount after an unsubmitted Manual choice. Existing PaperTradeModal tests should continue to pass. Before merge, require frontend typecheck, complete frontend regression suite, production build and CI review, plus owner visual acceptance in Preview. If local/CI checks cannot be executed, report that fact rather than claiming success.

**Acceptance steps:** open a new Buy modal (US and IN where available); see Auto in the first selected position; inspect stop/target; switch to Manual and back; confirm AI disabled; check payload uses selected mode. Do not place an actual user trade as a test without approval.

## Rollback
Revert this PR to restore Manual-first/Manual-default. No migration or position rewrite is required. Existing trades retain their selected mode.

## Production verification boundary
No production push or deployment is authorized by creation of this draft PR. After owner approval and green CI, review any active Daily Picks validation/deployment blockers separately before merging; verify exact deployed SHA and visually check the modal afterward.
