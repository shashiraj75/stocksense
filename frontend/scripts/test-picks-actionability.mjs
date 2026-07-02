#!/usr/bin/env node
/**
 * Deterministic regression tests for the entry-zone actionability helper
 * (src/utils/actionability.ts) — Release 12A price-basis safety.
 *
 * Same pattern as test-news-display.mjs: no frontend test framework exists,
 * so this standalone script compiles the dependency-free helper with the
 * project's TypeScript compiler. No network; no providers; no live clock —
 * `now` is injected everywhere it matters.
 *
 * Run from frontend/:  node scripts/test-picks-actionability.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "actionability-test-"));
try {
  execSync(
    `npx tsc src/utils/actionability.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { evaluateEntryZoneActionability, isQuoteVerifiedComparable, isVerifiedOutsideEntryZone,
          selectUnverifiedEntryZoneNote, MARKET_CLOSED_NOTE, QUOTE_UNVERIFIED_NOTE } =
    await import(pathToFileURL(join(outDir, "actionability.js")).href);

  let n = 0;
  const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

  const NOW = new Date("2026-07-02T10:05:00Z");
  // Release 12A1: verification requires an IDENTICAL known price basis, so
  // the canonical verifiable fixture is same-basis on both sides.
  const base = {
    generationBasis: "last_traded_unadjusted",
    generationPrice: 3097,
    generatedAt: "2026-07-01T20:36:00Z",
    entryLow: 3090, entryHigh: 3105,
    quotePrice: 3175.4,
    quoteBasis: "last_traded_unadjusted",
    quoteTimestamp: "2026-07-02T10:00:00Z",
    marketOpen: true,
    now: NOW,
  };

  // 1. Quote outside the zone but with NO timestamp → never "moved".
  test("missing quote timestamp never claims movement", () => {
    const s = evaluateEntryZoneActionability({ ...base, quoteTimestamp: null });
    assert.equal(s, "quote_timestamp_unknown");
    assert.equal(isVerifiedOutsideEntryZone(s), false);
  });

  // 2. Quote timestamp earlier than or equal to generated_at → never "moved".
  test("stale-or-equal quote timestamp never claims movement", () => {
    for (const ts of ["2026-07-01T20:36:00Z", "2026-07-01T09:00:00Z"]) {
      const s = evaluateEntryZoneActionability({ ...base, quoteTimestamp: ts });
      assert.equal(isVerifiedOutsideEntryZone(s), false, `ts=${ts}`);
    }
  });

  // 3. Market closed → neutral closed state, no warning — even when the
  //    quote is newer, same-basis, and differs (Rule 3: verification
  //    requires an open session).
  test("market closed is always neutral, never verified", () => {
    for (const quoteTimestamp of [null, "2026-07-02T10:00:00Z"]) {
      const s = evaluateEntryZoneActionability({ ...base, quoteTimestamp, marketOpen: false });
      assert.equal(s, "market_closed_unverified", `ts=${quoteTimestamp}`);
      assert.equal(isVerifiedOutsideEntryZone(s), false);
    }
  });

  // 4. Incompatible or unknown price basis → incomparable, no warning.
  test("incompatible price basis never claims movement", () => {
    for (const quoteBasis of ["unknown_basis", null, undefined]) {
      const s = evaluateEntryZoneActionability({ ...base, quoteBasis });
      assert.equal(s, "quote_incomparable", `basis=${quoteBasis}`);
    }
    const s = evaluateEntryZoneActionability({ ...base, generationBasis: "raw_close", quoteBasis: "raw_close" });
    assert.equal(s, "quote_incomparable"); // same but UNKNOWN basis is still unproven
  });

  // 4b. THE Release 12A1 critical case: adjusted generation reference vs
  //     unadjusted exchange quote — newer timestamp, open market, fresh,
  //     outside the zone — must be incomparable in BOTH directions. A
  //     corporate action between generation and viewing makes these
  //     economically different numbers; no verified warning, no strike-
  //     through, no "from current price" label may appear.
  test("adjusted vs unadjusted cross-basis pair is never verified", () => {
    const crossA = evaluateEntryZoneActionability({
      ...base, generationBasis: "adjusted_close", quoteBasis: "last_traded_unadjusted",
    });
    const crossB = evaluateEntryZoneActionability({
      ...base, generationBasis: "last_traded_unadjusted", quoteBasis: "adjusted_close",
    });
    for (const s of [crossA, crossB]) {
      assert.equal(s, "quote_incomparable");
      assert.equal(isVerifiedOutsideEntryZone(s), false);
      assert.equal(isQuoteVerifiedComparable(s), false);
    }
  });

  // 4c. Same-basis material-divergence guard: a split-style gap (>20% from
  //     the generation reference) is never presented as verified, even with
  //     every other condition passing — e.g. a 1:2 split halves the quote.
  test("material divergence beyond guard is never verified", () => {
    const split = evaluateEntryZoneActionability({ ...base, quotePrice: 1548.5 }); // ~-50%
    assert.equal(split, "quote_incomparable");
    assert.equal(isQuoteVerifiedComparable(split), false);
    const spike = evaluateEntryZoneActionability({ ...base, quotePrice: 3097 * 1.25 }); // +25%
    assert.equal(spike, "quote_incomparable");
  });

  // 4d. Divergence just inside the guard verifies normally when everything
  //     else passes (guard absorbs corporate actions, not genuine breaches).
  test("divergence just under the guard still verifies", () => {
    const s = evaluateEntryZoneActionability({ ...base, quotePrice: 3097 * 1.19 }); // +19%
    assert.equal(s, "verified_above_entry_zone");
  });

  // 5. Fully verified quote above the zone → verified above state.
  test("verified newer comparable quote above entry_high is proven", () => {
    const s = evaluateEntryZoneActionability(base);
    assert.equal(s, "verified_above_entry_zone");
    assert.equal(isVerifiedOutsideEntryZone(s), true);
  });

  // 6. Verified below-zone equivalent.
  test("verified quote below entry_low is proven", () => {
    const s = evaluateEntryZoneActionability({ ...base, quotePrice: 3000 });
    assert.equal(s, "verified_below_entry_zone");
    assert.equal(isVerifiedOutsideEntryZone(s), true);
  });

  // 7. Verified quote inside the zone → no warning state.
  test("verified quote inside the zone has no warning", () => {
    const s = evaluateEntryZoneActionability({ ...base, quotePrice: 3100 });
    assert.equal(s, "verified_within_entry_zone");
    assert.equal(isVerifiedOutsideEntryZone(s), false);
    assert.equal(isQuoteVerifiedComparable(s), true);
  });

  // 8. Unknown/legacy metadata renders safely: no basis, no generated_at,
  //    malformed generated_at, missing entry zone, or missing/invalid
  //    generation reference price.
  test("legacy or unknown metadata is always the legacy state", () => {
    assert.equal(evaluateEntryZoneActionability({ ...base, generationBasis: null }), "legacy_reference_unknown");
    assert.equal(evaluateEntryZoneActionability({ ...base, generatedAt: null }), "legacy_reference_unknown");
    assert.equal(evaluateEntryZoneActionability({ ...base, generatedAt: "not-a-date" }), "legacy_reference_unknown");
    assert.equal(evaluateEntryZoneActionability({ ...base, entryLow: null }), "legacy_reference_unknown");
    for (const generationPrice of [null, undefined, 0, -1, NaN]) {
      assert.equal(evaluateEntryZoneActionability({ ...base, generationPrice }),
        "legacy_reference_unknown", `refPrice=${generationPrice}`);
    }
  });

  // 9. Legacy picks (no provenance) can never show a movement claim even
  //    when the raw numbers sit far outside the zone.
  test("legacy picks never produce a verified-outside state", () => {
    const s = evaluateEntryZoneActionability({
      ...base, generationBasis: undefined, generatedAt: undefined, quotePrice: 99999,
    });
    assert.equal(isVerifiedOutsideEntryZone(s), false);
    assert.equal(isQuoteVerifiedComparable(s), false);
  });

  // 10. Upside gating: comparability is false in every unverified state.
  test("upside may claim current-price basis only in verified states", () => {
    const unverified = [
      { ...base, quoteTimestamp: null },
      { ...base, quoteBasis: "unknown_basis" },
      { ...base, quoteTimestamp: null, marketOpen: false },
      { ...base, generationBasis: null },
      { ...base, quotePrice: null },
    ];
    for (const input of unverified) {
      assert.equal(isQuoteVerifiedComparable(evaluateEntryZoneActionability(input)), false);
    }
  });

  // 11. Verified comparable quote data may drive current-price upside.
  test("verified states allow current-price upside", () => {
    for (const price of [3100, 3175.4, 3000]) {
      const s = evaluateEntryZoneActionability({ ...base, quotePrice: price });
      assert.equal(isQuoteVerifiedComparable(s), true, `price=${price}`);
    }
  });

  // 12. Market-agnostic: the helper takes no market parameter — IN and US
  //     share identical rules, differing only in the marketOpen input. A
  //     stale open-market quote is also rejected.
  test("identical rules for IN and US; stale open-market quote rejected", () => {
    assert.equal(evaluateEntryZoneActionability.length, 1); // single input object, no market arg
    const stale = evaluateEntryZoneActionability({
      ...base, quoteTimestamp: "2026-07-02T09:00:00Z", now: new Date("2026-07-02T10:05:00Z"),
    });
    assert.equal(stale, "quote_timestamp_unknown"); // > 15 min old while open
    // Unknown session state is never treated as verified.
    assert.equal(isVerifiedOutsideEntryZone(
      evaluateEntryZoneActionability({ ...base, marketOpen: null })), false);
  });

  // 13. No quote at all → quote_unavailable, nothing claimed.
  test("missing quote is an explicit unavailable state", () => {
    for (const quotePrice of [null, undefined, NaN]) {
      assert.equal(evaluateEntryZoneActionability({ ...base, quotePrice }), "quote_unavailable");
    }
  });

  // ── Release 12A2: closed-market display priority (presentation only) ──

  // 14. Market closed + cross-basis quote: internal state stays
  //     quote_incomparable, but the DISPLAYED note is the closed-market
  //     guidance — and no verified affordance can appear.
  test("closed market + cross-basis shows closed-market note", () => {
    const s = evaluateEntryZoneActionability({
      ...base, generationBasis: "adjusted_close", quoteBasis: "last_traded_unadjusted",
      marketOpen: false,
    });
    assert.equal(s, "quote_incomparable");             // state machine unchanged
    assert.equal(selectUnverifiedEntryZoneNote(s, false), MARKET_CLOSED_NOTE);
    assert.equal(isVerifiedOutsideEntryZone(s), false); // no warning/strike/(passed)
    assert.equal(isQuoteVerifiedComparable(s), false);  // no "from current price"
  });

  // 15. Market closed + same-basis quote without timestamp: closed note.
  test("closed market + timestampless same-basis quote shows closed note", () => {
    const s = evaluateEntryZoneActionability({ ...base, quoteTimestamp: null, marketOpen: false });
    assert.equal(s, "market_closed_unverified");
    assert.equal(selectUnverifiedEntryZoneNote(s, false), MARKET_CLOSED_NOTE);
  });

  // 16. Market closed + legacy pick: closed note, no verified behavior.
  test("closed market + legacy pick shows closed note", () => {
    const s = evaluateEntryZoneActionability({
      ...base, generationBasis: undefined, generatedAt: undefined, marketOpen: false,
    });
    assert.equal(s, "legacy_reference_unknown");
    assert.equal(selectUnverifiedEntryZoneNote(s, false), MARKET_CLOSED_NOTE);
    assert.equal(isQuoteVerifiedComparable(s), false);
  });

  // 17. Market open + cross-basis quote: the technical incomparable wording
  //     stays (it is the operative fact while trading is live).
  test("open market + cross-basis keeps technical wording", () => {
    const s = evaluateEntryZoneActionability({
      ...base, generationBasis: "adjusted_close", quoteBasis: "last_traded_unadjusted",
    });
    assert.equal(s, "quote_incomparable");
    assert.equal(selectUnverifiedEntryZoneNote(s, true), QUOTE_UNVERIFIED_NOTE);
    // Unknown market state also keeps the technical wording, never the
    // closed-market claim.
    assert.equal(selectUnverifiedEntryZoneNote(s, null), QUOTE_UNVERIFIED_NOTE);
  });

  // 18. Verified states are untouched: the note helper returns null so the
  //     existing verified warning path renders instead.
  test("verified states bypass the neutral-note helper", () => {
    const above = evaluateEntryZoneActionability(base);
    assert.equal(above, "verified_above_entry_zone");
    assert.equal(selectUnverifiedEntryZoneNote(above, true), null);
    const within = evaluateEntryZoneActionability({ ...base, quotePrice: 3100 });
    assert.equal(selectUnverifiedEntryZoneNote(within, true), null);
  });

  console.log(`\npicks-actionability regression: ${n} tests passed`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
