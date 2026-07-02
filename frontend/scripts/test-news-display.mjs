#!/usr/bin/env node
/**
 * Deterministic regression tests for the News & Sentiment display helpers
 * (src/utils/newsDisplay.ts) — Wave 0C display truthfulness.
 *
 * Same pattern as test-price-basis.mjs: no frontend test framework exists in
 * this repo, so this standalone script compiles the dependency-free helper
 * with the project's own TypeScript compiler and asserts against it. No new
 * dependencies; no network; no providers.
 *
 * Run from frontend/:  node scripts/test-news-display.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "news-display-test-"));
try {
  execSync(
    `npx tsc src/utils/newsDisplay.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { capContextAndHistorical, formatCompanyNewsBasis, groupArticlesByEligibility,
          groupArticlesByRelevance, parseArticleDate,
          RECENT_CONTEXT_DISPLAY_LIMIT, HISTORICAL_DISPLAY_LIMIT } =
    await import(pathToFileURL(join(outDir, "newsDisplay.js")).href);

  let n = 0;
  const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

  const fresh = { title: "f", sentiment_eligible: true };
  const stale = { title: "s", sentiment_eligible: false };
  const legacy = { title: "l" }; // no eligibility field (older cached payload)

  // 1. Fresh eligible articles land only in the current group.
  test("fresh articles group under current", () => {
    const g = groupArticlesByEligibility([fresh, stale]);
    assert.equal(g.hasEligibilityData, true);
    assert.deepEqual(g.current.map(a => a.title), ["f"]);
    assert.deepEqual(g.historical.map(a => a.title), ["s"]);
  });

  // 2. All-stale set → empty current group (drives "Insufficient fresh news
  //    evidence" at the section level), historical intact.
  test("all-stale set yields empty current group", () => {
    const g = groupArticlesByEligibility([stale, { ...stale, title: "s2" }]);
    assert.equal(g.current.length, 0);
    assert.equal(g.historical.length, 2);
    assert.equal(g.hasEligibilityData, true);
  });

  // 3. Legacy payloads (no eligibility field anywhere) make no claims:
  //    hasEligibilityData=false → UI renders ungrouped with no labels.
  test("legacy payloads make no inclusion claims", () => {
    const g = groupArticlesByEligibility([legacy, { title: "l2" }]);
    assert.equal(g.hasEligibilityData, false);
    assert.equal(g.current.length, 0);
    assert.equal(g.historical.length, 0);
  });

  // 4. Mixed legacy+annotated: annotation exists, unannotated items are NOT
  //    claimed as current (they fall to historical — never guessed fresh).
  test("unannotated articles are never claimed as current", () => {
    const g = groupArticlesByEligibility([fresh, legacy]);
    assert.equal(g.hasEligibilityData, true);
    assert.deepEqual(g.current.map(a => a.title), ["f"]);
    assert.deepEqual(g.historical.map(a => a.title), ["l"]);
  });

  // 5. Invalid dates parse to null — never an Invalid Date object, so
  //    date-fns can never throw and "Invalid Date" can never render.
  test("invalid dates parse to null, valid dates to Date", () => {
    for (const bad of ["", null, undefined, "garbage", "0000-99-99"]) {
      assert.equal(parseArticleDate(bad), null, `input=${bad}`);
    }
    const d = parseArticleDate("Wed, 01 Jul 2026 09:30:00 +0000");
    assert.ok(d instanceof Date && !Number.isNaN(d.getTime()));
    const iso = parseArticleDate("2026-07-01T09:30:00Z");
    assert.ok(iso instanceof Date && !Number.isNaN(iso.getTime()));
  });

  // 6. Market-agnostic by construction: helpers take no market/currency
  //    argument — identical behavior for IN and US payloads.
  test("helpers are market-agnostic (no market parameter exists)", () => {
    assert.equal(groupArticlesByEligibility.length, 1);
    assert.equal(parseArticleDate.length, 1);
  });

  // ── Wave 0D1 three-way relevance grouping ──────────────────────────────
  const companyFresh = { title: "cf", sentiment_eligible: true, company_sentiment_eligible: true };
  const contextFresh = { title: "ctx", sentiment_eligible: true, company_sentiment_eligible: false };
  const historicalArt = { title: "h", sentiment_eligible: false, company_sentiment_eligible: false };
  const release8Only = { title: "r8", sentiment_eligible: true }; // pre-0D1 cached payload

  // 7. Fresh company-specific → company-current group only.
  test("company-specific fresh articles group under companyCurrent only", () => {
    const g = groupArticlesByRelevance([companyFresh, contextFresh, historicalArt]);
    assert.equal(g.hasRelevanceData, true);
    assert.deepEqual(g.companyCurrent.map(a => a.title), ["cf"]);
    assert.deepEqual(g.recentContext.map(a => a.title), ["ctx"]);
    assert.deepEqual(g.historical.map(a => a.title), ["h"]);
  });

  // 8. Contextual-only fresh set → empty companyCurrent (drives the
  //    "Insufficient fresh company-specific news evidence" section state).
  test("contextual-only set yields empty companyCurrent group", () => {
    const g = groupArticlesByRelevance([contextFresh, historicalArt]);
    assert.equal(g.companyCurrent.length, 0);
    assert.equal(g.recentContext.length, 1);
    assert.equal(g.historical.length, 1);
  });

  // 9. Release-8-era payloads (freshness fields only, no relevance fields)
  //    → hasRelevanceData=false, page falls back to the two-group Release 8
  //    rendering; no company-inclusion claim is possible.
  test("release-8 payloads without relevance fields make no company claims", () => {
    const g = groupArticlesByRelevance([release8Only, { title: "r8b", sentiment_eligible: false }]);
    assert.equal(g.hasRelevanceData, false);
    assert.equal(g.companyCurrent.length, 0);
  });

  // 10. Fully-legacy payloads still short-circuit at the eligibility layer.
  test("fully-legacy payloads remain ungrouped and claim-free", () => {
    const e = groupArticlesByEligibility([{ title: "old1" }, { title: "old2" }]);
    assert.equal(e.hasEligibilityData, false);
    const g = groupArticlesByRelevance([{ title: "old1" }, { title: "old2" }]);
    assert.equal(g.hasRelevanceData, false);
  });

  // 11. Relevance grouping is market-agnostic (no market parameter exists).
  test("relevance grouping is market-agnostic", () => {
    assert.equal(groupArticlesByRelevance.length, 1);
  });

  // ── Wave 0D3 duplicate-event basis wording ─────────────────────────────
  // 12. Event count equals article count → concise article wording, no
  //     duplicate-story claim.
  test("matching event and article counts keep concise wording", () => {
    assert.equal(
      formatCompanyNewsBasis(3, 3),
      "Based on 3 recent company-specific articles.",
    );
    assert.equal(
      formatCompanyNewsBasis(1, 1),
      "Based on 1 recent company-specific article.",
    );
  });

  // 13. Fewer events than articles → truthful "events across articles" line.
  test("duplicate coverage reports events across articles", () => {
    assert.equal(
      formatCompanyNewsBasis(1, 3),
      "Based on 1 recent company-news event across 3 articles.",
    );
    assert.equal(
      formatCompanyNewsBasis(2, 5),
      "Based on 2 recent company-news events across 5 articles.",
    );
  });

  // 14. Legacy payload without event metadata → concise wording, never a
  //     duplicate claim the backend didn't make.
  test("missing event metadata falls back to article wording", () => {
    assert.equal(
      formatCompanyNewsBasis(undefined, 4),
      "Based on 4 recent company-specific articles.",
    );
    assert.equal(
      formatCompanyNewsBasis(0, 2),
      "Based on 2 recent company-specific articles.",
    );
  });

  // ── Release 11A: basis line must match visible current-company cards ──
  const co = (t) => ({ title: t, sentiment_eligible: true, company_sentiment_eligible: true });
  const ctxA = (t) => ({ title: t, sentiment_eligible: true, company_sentiment_eligible: false });
  const hist = (t) => ({ title: t, sentiment_eligible: false, company_sentiment_eligible: false });

  // 15. TSM-style interleaved feed: 6 company-specific articles, only 3 of
  //     them inside the old first-8 cutoff. Full-payload grouping must keep
  //     all 6 in companyCurrent and the basis count must be 6.
  test("interleaved feed keeps all company-specific cards visible", () => {
    const feed = [
      co("co1"), ctxA("x1"), co("co2"), hist("h1"), ctxA("x2"), co("co3"),
      hist("h2"), ctxA("x3"),                       // old cutoff was here (8)
      co("co4"), hist("h3"), co("co5"), ctxA("x4"), co("co6"), hist("h4"),
    ];
    const rel = capContextAndHistorical(groupArticlesByRelevance(feed));
    assert.deepEqual(rel.companyCurrent.map(a => a.title),
      ["co1", "co2", "co3", "co4", "co5", "co6"]);  // all six, feed order
    assert.equal(
      formatCompanyNewsBasis(6, rel.companyCurrent.length),
      "Based on 6 recent company-specific articles.",
    );
    // Contextual/historical never leak into the company count.
    assert.ok(rel.companyCurrent.every(a => a.company_sentiment_eligible === true));
  });

  // 16. Duplicate-event wording over the same displayed population.
  test("duplicate coverage wording: 4 events across 6 displayed articles", () => {
    const rel = groupArticlesByRelevance([co("a"), co("b"), co("c"), co("d"), co("e"), co("f")]);
    assert.equal(
      formatCompanyNewsBasis(4, rel.companyCurrent.length),
      "Based on 4 recent company-news events across 6 articles.",
    );
    assert.equal(
      formatCompanyNewsBasis(1, 2),
      "Based on 1 recent company-news event across 2 articles.",
    );
  });

  // 17. Legacy/invalid event metadata: displayed-count concise wording, no
  //     duplicate-event claim, no malformed sentence.
  test("invalid event metadata never produces event wording", () => {
    for (const bad of [undefined, null, 0, NaN, Infinity]) {
      assert.equal(
        formatCompanyNewsBasis(bad, 6),
        "Based on 6 recent company-specific articles.",
        `eventCount=${bad}`,
      );
    }
    // Inconsistent metadata (events > displayed articles) must not claim
    // duplicates either — concise displayed-count wording wins.
    assert.equal(formatCompanyNewsBasis(6, 3), "Based on 3 recent company-specific articles.");
  });

  // 18. Context/historical caps are per-group, after grouping, and can never
  //     reduce the current-company group.
  test("context and historical caps never affect companyCurrent", () => {
    const feed = [
      ...Array.from({ length: 6 }, (_, i) => co(`co${i}`)),
      ...Array.from({ length: 9 }, (_, i) => ctxA(`x${i}`)),
      ...Array.from({ length: 9 }, (_, i) => hist(`h${i}`)),
    ];
    const rel = capContextAndHistorical(groupArticlesByRelevance(feed));
    assert.equal(rel.companyCurrent.length, 6);      // uncapped
    assert.equal(rel.recentContext.length, RECENT_CONTEXT_DISPLAY_LIMIT);
    assert.equal(rel.historical.length, HISTORICAL_DISPLAY_LIMIT);
    assert.equal(rel.hasRelevanceData, true);
  });

  // 19. Empty / insufficient-evidence states unchanged: no fresh
  //     company-specific article → empty companyCurrent (drives the
  //     "Insufficient fresh company-specific news evidence" state), and the
  //     capped context groups still render.
  test("insufficient-evidence and legacy states remain unchanged", () => {
    const rel = capContextAndHistorical(groupArticlesByRelevance([ctxA("x"), hist("h")]));
    assert.equal(rel.companyCurrent.length, 0);
    assert.equal(rel.recentContext.length, 1);
    assert.equal(rel.historical.length, 1);
    const legacyRel = capContextAndHistorical(
      groupArticlesByRelevance([{ title: "old" }]));
    assert.equal(legacyRel.hasRelevanceData, false);  // ungrouped fallback intact
  });

  // 20. Market parity: no display helper takes a market parameter.
  test("release-11A helpers are market-agnostic", () => {
    assert.equal(capContextAndHistorical.length, 1);
    assert.equal(formatCompanyNewsBasis.length, 2);
  });

  console.log(`\nnews-display regression: ${n} tests passed`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
