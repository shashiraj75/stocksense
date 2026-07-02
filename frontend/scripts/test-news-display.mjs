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
  const { groupArticlesByEligibility, groupArticlesByRelevance, parseArticleDate } =
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

  console.log(`\nnews-display regression: ${n} tests passed`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
