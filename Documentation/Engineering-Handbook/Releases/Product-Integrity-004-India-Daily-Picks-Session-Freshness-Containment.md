# Product Integrity Workstream #004 — India Daily Picks Session-Freshness Containment (Phase 0)

**Status:** Phase 0 (frontend presentation-layer containment) implemented and validated. **This is not an AUROPHARMA-specific patch** — the underlying finding is systemic within India Daily Picks, confirmed against live production data. A permanent backend freshness gate is a separate, not-yet-started follow-up phase (see §5).

**Scope note:** this record covers the confirmed India Daily Picks finding and the Phase 0 containment implemented in response. **It does not claim the platform is confirmed defective.** Precisely: **confirmed systemic for India Daily Picks; global audit required** for US Daily Picks and every other price-consuming feature (§4).

## 1. Confirmed finding

Two rounds of read-only forensic verification against the live production API (`https://stocksense-production-7e0d.up.railway.app`) and the official NSE bhavcopy archive established:

- Of the 17 India Daily Picks present in production at time of audit, **12 (70.6%)** carried a `generation_reference_as_of` older than the NSE session that had actually completed by the time the batch was generated.
- Three of those twelve — **AUROPHARMA, EIHOTEL, MAHABANK** — were individually confirmed: each pick's `generation_reference_price` matched Friday 2026-07-10's official NSE close exactly (independently verified via `archives.nseindia.com` bhavcopy, not Yahoo alone), while the batch was generated in the early hours of Tuesday 2026-07-14 — **after** Monday 2026-07-13's regular NSE session had fully closed (15:30 IST) and published.
- Correlating every pick's `generated_at` against its `generation_reference_as_of` showed fresh and stale picks **interleaved throughout the same generation run**, with no time-based cutoff separating them (e.g. two horizons of the same symbol processed a second apart landed on the same freshness outcome, while different symbols processed at nearly the same instant did not) — consistent with a **per-symbol, provider-side data-availability inconsistency** at the moment of each individual price-history fetch, not a deterministic indexing bug in this codebase (a deterministic bug would affect every symbol uniformly, not a 71/29 split) and not a corporate action (dividend/split history for the affected symbols was directly checked and ruled out).
- Net effect: entry zones, targets, stop-losses, and estimated upside were computed from a stale reference price for a majority of India Daily Picks at the time of audit, and the existing "was ₹X at generation" UI wording did not disclose *how* stale that generation-time reference itself was.

This is a **presentation and generation-freshness defect**, not a scoring, ranking, signal, or confidence-calculation defect — the numbers displayed were exactly what the model computed from the reference price it was given; no value was fabricated or corrupted.

## 2. Phase 0 containment (this implementation)

Frontend-only, India Daily Picks only. No backend change, no database migration, no change to scoring/ranking/generation/confidence/technical-indicator logic, no generation triggered, no backfill executed, no Phase 1A or Phase 1A.3 code touched.

- `frontend/src/utils/marketHours.ts` — added `getExpectedLatestCompletedSession(now)` and `isNseTradingSessionDate(date)`, both built on the calendar already used by `getMarketStatus("IN", ...)` (same weekend/holiday rules — no second holiday list).
- `frontend/src/utils/sessionFreshness.ts` (new) — pure `evaluateSessionFreshness(generationReferenceAsOf, now)` returning `{ expectedLatestCompletedSession, referenceSessionDate, freshnessStatus: "fresh" | "stale" | "unknown", freshnessLagSessions }`, computed entirely from the `generation_reference_as_of` field the API already returns. Never assumes fresh: a missing/unparseable reference, or a reference somehow dated ahead of the expected session, resolves to `"unknown"`, never `"fresh"`.
- `frontend/src/app/picks/page.tsx` — for India picks only:
  - a prominent per-card warning for `stale` ("Price reference is stale — this pick used market data from {date}, not the latest completed NSE session.") and `unknown` ("Price freshness could not be verified.");
  - the old "was ₹X at generation" line is replaced, for stale/unknown picks only, with "Calculation reference: ₹X · as of {date} · stale/unverified";
  - Entry Zone, Scenario Target, Stop Loss, and Estimated Upside are visually de-emphasized and their values hidden (never recalculated) behind a "Not actionable until refreshed" label;
  - the entry-zone "verified above/below" and "quote differs" banners are suppressed for stale/unknown picks, so the card never frames a live quote against a reference it has just disclosed is unverified;
  - the Paper Trade button is disabled (and the prefilled modal cannot open) for stale/unknown picks;
  - a page-level banner appears whenever any currently displayed India pick is stale or unknown, with fresh/stale/unknown counts.
  - Fresh India picks and all US picks render identically to before this change.

Validated with `vitest` (calendar edge cases: weekend, NSE holiday, missing reference, exact-session match, multi-session lag spanning a weekend — all against fixtures, no production calls) and source-wiring assertions confirming the UI containment is actually wired to that logic. See the Phase 0 pull request / diff for the full test list.

## 3. What Phase 0 deliberately does not do

- It does not change what any pick's price, target, entry zone, or stop-loss *is* — only whether those values are presented as actionable.
- It does not prevent a stale pick from being generated or persisted — that requires a backend-side freshness gate (§5), out of scope for this phase.
- It does not touch the `pick.summary` backend-generated narrative text, which was out of the explicit containment scope for this phase.
- It does not change US Daily Picks in any way — the finding in §1 was established for India only; US has not yet been audited (§4).

## 4. Required follow-up: platform-wide audit

**Confirmed systemic for India Daily Picks; global audit required.** The mechanism identified in §1 (a per-symbol daily-bar fetch that can return before the latest session's bar is available, with no freshness check anywhere in the pipeline) is not architecturally specific to India Daily Picks — the same unguarded fetch pattern exists in other price-consuming code paths. This workstream does **not** claim any of the following are affected — it flags them as unaudited and requiring the same live-evidence verification applied to India Daily Picks before any claim is made either way:

- US Daily Picks (separate generation pipeline, separate premarket finalizer — not yet checked against this failure mode).
- Any other feature that persists a `generation_reference_*`-style price snapshot and later presents it as current or actionable (e.g. Multibagger screens, individual stock prediction pages, alerts).

## 5. Canonical comparability requirements (for the follow-up backend phase)

Any future price comparison across this platform — live vs. reference, entry vs. outcome, backtest entry vs. exit — must prove all of the following before being presented as verified, not merely assume them:

- **Same symbol**
- **Same market** (IN vs. US — never cross-compared)
- **Expected session** — the reference's session date must be checked against the calendar-computed latest-completed session, not merely assumed current
- **Timestamp** — the comparison quote must be provably newer than the reference it's being compared against
- **Price basis** — adjusted vs. unadjusted must match; cross-basis comparisons are not valid even when both numbers are real
- **Source** — which provider (NSE / Yahoo / Finnhub) supplied each side of the comparison must be recorded, not assumed consistent

This mirrors the rule `frontend/src/utils/actionability.ts` already enforces for basis/timestamp comparability (Release 12A/12A1) — session-date comparability was the one dimension that rule did not yet cover, which is what this workstream adds.

**The permanent backend freshness gate — rejecting or excluding a stale-referenced pick at generation time rather than disclosing it after the fact — remains a separate, not-yet-scheduled follow-up phase.** It is out of scope for Phase 0, which is presentation-layer containment only.

## Explicit confirmation

No Daily Picks generation was triggered by this work (no `POST /api/picks/generate` call). No historical backfill was executed. No database or production data was changed — this phase touches only frontend TypeScript/TSX source and this document. Phase 1A, Phase 1A.3, outcome-lifecycle, and validation-watch code were not read or modified as part of this change. This was treated throughout as a systemic India Daily Picks containment, not a single-symbol patch — the fix targets `generation_reference_as_of` generically, and applies to every India pick the field is present on, not to AUROPHARMA specifically.
