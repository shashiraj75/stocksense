# Product Integrity Workstream #022 — Risk-Based Position Sizing in Paper Trade

**Status:** Implemented and tested. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

Follow-up to the confidence/allocation discussion in this session: the user asked why their US paper trading has a 75.6% win rate but a net loss, while India's lower 61.4% win rate is net-profitable. Pulling the user's actual closed trades (production data) showed the mechanism: US avg loss ($380) is 5.7x avg win ($67), traced to a handful of large-dollar positions — the three biggest losses were $11,894, $8,248, and $7,518 notional (e.g. 10 shares of a $1,189 stock), while nearly every winning trade was a small position in a cheap stock. The Paper Trade modal has no default quantity logic (`useState(existingQuantity ?? 1)`) — users pick a flat share count themselves, so a fixed "10 shares" carries wildly different dollar risk depending on the stock's price. The user agreed with the proposed fix: size positions by risk (a fixed % of virtual capital per trade, sized off the stop-loss distance) instead of a flat share count.

## 2. Feature

Added risk-based quantity suggestion to `PaperTradeModal.tsx` (buy flow only):

- New pure utility `frontend/src/utils/riskBasedSizing.ts` — `computeSuggestedQuantity({currentPrice, stopLoss, availableCash, riskPct})`. Suggests a share count sized so a stop-loss hit costs ~`riskPct` (default 1%) of available virtual capital, capped at what available cash can actually buy (a very tight stop can't suggest a position bigger than the account — no modeled leverage). Returns `null` when there's no stop loss, no cash data, or a non-positive price, so the modal can fall back cleanly.
- The modal fetches the user's portfolio (`useQuery(["paper-portfolio", userId], ...)` — same queryKey as the Paper Trading page, so it dedupes against an already-loaded cache) and reads `cash` (IN) or `cash_usd` (US) as the risk budget base.
- The suggested quantity auto-fills the Quantity field, using the exact same "pre-filled but never fights a manual edit" pattern already used for the AI stop-loss/target auto-fill (a `quantityEdited` ref, reset when the horizon changes since the stop-loss distance changes with it).
- A hint line under Quantity shows the suggestion and its basis (e.g. "Risk-based suggestion: 6 shares — risks ~$1,000 (1% of $100,000 available)"), with a "Use suggestion" link if the user has since typed a different number. When no stop loss is set, it shows a neutral prompt instead ("Set a stop loss to get a risk-based quantity suggestion.").

## 3. What this does not do

- Does not force the suggested quantity — it's a pre-filled default, fully editable, identical in spirit to the existing AI stop-loss/target pre-fill.
- Does not change the Sell flow, existing open positions, or any backend trade logic — purely a Buy-side client suggestion computed from data the modal already has or already fetches.
- Does not make the risk % user-configurable in this pass — hardcoded at 1% (`RISK_PCT_OF_CAPITAL`), matching the user's own stated suggestion and common real-world position-sizing convention. A settings UI for this wasn't requested and isn't added speculatively.
- Does not retroactively adjust or flag past trades — this only affects new Buy orders placed after this ships.

## 4. Tests

- New `riskBasedSizing.test.ts` — 13 tests, real numeric assertions (not source-text checks): standard sizing case; a scaled reproduction of the user's actual MU incident (confirms the function would suggest far fewer than the 10 flat shares that caused the real $1,439 loss); the available-cash cap on a very tight stop loss; the 1-share floor; null returns for missing/invalid stop loss, zero-distance stop loss, missing/non-positive cash, and non-positive/non-finite price; direction-agnostic sizing (SELL-side stop above entry behaves symmetrically); a custom `riskPct` override; and an India-scale (₹) example using real DIXON trade levels seen earlier this session.
- Full frontend suite: **347/347 passed** (334 baseline + 13 new).
- Clean `tsc --noEmit` and clean `next build` (all 18 routes generated).
- Browser verification limited: the modal requires an authenticated session, which wasn't available in this pass's local preview (same constraint as PI-021's CORS limitation). Verified instead via a clean dev-server compile (no runtime errors) plus the full behavioral test suite covering the exact sizing math end to end, including the specific real-world incident this feature targets.

## 5. Rollback

Two-file, additive change (`riskBasedSizing.ts` new; `PaperTradeModal.tsx` modified) plus a new test file — reverting restores the exact pre-feature manual-quantity-only interface. No backend, schema, or API contract change.
