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
  const { groupArticlesByEligibility, parseArticleDate } =
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

  console.log(`\nnews-display regression: ${n} tests passed`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
