# Product Integrity Workstream #017 — AI Prediction Confidence Display Fixes

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

User feedback on a live production screenshot (DIXON, long-term BUY, 12% confidence): (a) the "AI Prediction" card showed "Confidence" twice in immediate succession — once in the Signal Strip, once again via a standalone `ConfidenceMeter` right below it; (b) the Trade Levels card above it rendered a fully precise, confident-looking setup (Buy Zone ₹14,377–₹14,550, Take Profit +15.8%, Stop Loss, Risk/Reward 1:1.5) with no visual acknowledgment that the underlying signal was only 12% confident — undermining trust, since a card this detailed and precise-looking implies more conviction than the AI actually has.

## 2. Fixes

### 2a. Removed the duplicate Confidence display

The "AI Prediction" card's Signal Strip already shows Confidence (added/muted-consistently in Product Integrity #015 via `getSignalTone`). Immediately below it, a standalone `<ConfidenceMeter value={prediction.confidence} label="Confidence" />` repeated the identical number — and used a *different* muting threshold (70%/40% split) than `getSignalTone` (60%/45% split), so the same confidence value could visually imply two different "how sure is this" readings a few pixels apart. Removed; the Signal Strip is now the only confidence display on this card. `ConfidenceMeter` itself is untouched and still used for Technical/Sentiment/Fundamental scores elsewhere on the page, where it's the only display for those values (no duplication there).

### 2b. Trade Levels now discloses low confidence

Previously rendered identically regardless of confidence — a 12%-confidence BUY's Buy Zone/Take Profit/Stop Loss/Risk-Reward numbers looked exactly as actionable as a 90%-confidence one. Added a warning banner (`⚠ Low confidence (N%) — these levels are the AI's calculated risk boundaries for this signal, not a high-conviction trade setup.`) whenever `prediction.confidence < 45` — the same threshold `getSignalTone` already uses to mute a weak BUY, so this is consistent with, not a new definition of, "low confidence" on this page.

## 3. What this does not do

- Does not hide or restyle the individual trade-level boxes themselves (Buy Zone/Take Profit/Stop Loss colors stay as-is) — the numbers are still mathematically valid ATR-based levels; the fix is disclosure, not suppression.
- Does not apply the same low-confidence banner logic to SELL signals — `getSignalTone` itself doesn't mute SELL by confidence (always red, per its own documented rationale), so this release doesn't introduce a new muting rule SELL doesn't already have; the Trade Levels warning does fire for any signal type (BUY/SELL/HOLD) below 45% confidence, since low conviction deserves the same caution regardless of direction.
- Does not touch the backend confidence computation itself — purely a display-layer fix.

## 4. Tests

- `confidenceDisplayFixes.test.ts` — 5 new tests, source-assertion style (this page mounts through live data-fetching/auth/router context impractical to isolate, matching this file's existing test convention).
- Full frontend suite: **321/321 passed** (316 baseline + 5 new).
- Typecheck: clean.
- Verified visually via an isolated mock reproducing the exact reported card (12% confidence, ₹14,377 Buy Zone) — confirms the duplicate is gone and the warning renders correctly positioned above the trade-level grid.
- No backend changes.

## 5. Rollback

Both changes are isolated to the same file/region and independent — either can be reverted without affecting the other.
