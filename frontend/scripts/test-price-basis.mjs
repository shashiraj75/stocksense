#!/usr/bin/env node
/**
 * Deterministic regression tests for the Daily Picks price-basis rule
 * (src/utils/priceBasis.ts) — Wave 0B truthfulness correction.
 *
 * This repo has no frontend test framework (a long-standing, documented
 * state — see EPIC-005 Sprint #012 and the Wave 0A report). Rather than
 * introduce one for a single pure function, this standalone script compiles
 * the helper with the project's own TypeScript compiler and asserts against
 * it directly. Zero new dependencies; no network; no providers.
 *
 * Run from frontend/:  node scripts/test-price-basis.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "price-basis-test-"));
try {
  execSync(
    `npx tsc src/utils/priceBasis.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { isValidPrice, selectPriceBasis, computeEstimatedUpsidePct, hasValidGenerationBasis } =
    await import(pathToFileURL(join(outDir, "priceBasis.js")).href);

  let n = 0;
  const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

  // 1. Current price differs from generation price → upside uses CURRENT.
  test("upside uses current price when it differs from generation price", () => {
    const { basis, price } = selectPriceBasis(1056.30, 1026.90);
    assert.equal(basis, "current");
    assert.equal(price, 1056.30);
    const pct = computeEstimatedUpsidePct(1076.30, price);
    // (1076.30 - 1056.30) / 1056.30 * 100 = 1.8934…% — NOT the 4.81% the
    // old generation-price math would have produced.
    assert.ok(Math.abs(pct - 1.8934) < 0.001, `got ${pct}`);
    const stale = computeEstimatedUpsidePct(1076.30, 1026.90);
    assert.ok(Math.abs(pct - stale) > 1, "current-basis and stale-basis results must differ here");
  });

  // 2. Current equals generation → same correct number either way.
  test("upside correct when current equals generation price", () => {
    const { basis, price } = selectPriceBasis(100, 100);
    assert.equal(basis, "current");
    assert.ok(Math.abs(computeEstimatedUpsidePct(110, price) - 10) < 1e-9);
  });

  // 3. No valid current price, valid generation price → generation basis
  //    (the component labels it "(from generation price)").
  test("falls back to labelled generation basis when current price is missing", () => {
    for (const missing of [null, undefined, 0, -5, NaN, Infinity]) {
      const { basis, price } = selectPriceBasis(missing, 1026.90);
      assert.equal(basis, "generation", `current=${missing}`);
      assert.equal(price, 1026.90);
    }
  });

  // 4. Invalid current AND invalid generation price → no basis, no number.
  test("no fabricated percentage when no valid price basis exists", () => {
    for (const bad of [null, undefined, 0, -1, NaN, Infinity, -Infinity]) {
      const { basis, price } = selectPriceBasis(bad, bad);
      assert.equal(basis, null);
      assert.equal(price, null);
      assert.equal(computeEstimatedUpsidePct(110, price), null);
    }
  });

  // 5. Missing/zero/negative/non-finite target → upside unavailable (null),
  //    never NaN, never Infinity, never a silent 0.
  test("invalid target yields null upside, never NaN/Infinity/fabricated 0", () => {
    for (const badTarget of [null, undefined, 0, -50, NaN, Infinity, -Infinity]) {
      const pct = computeEstimatedUpsidePct(badTarget, 100);
      assert.equal(pct, null, `target=${badTarget} gave ${pct}`);
    }
    // And a zero-basis division can never leak through as Infinity:
    assert.equal(computeEstimatedUpsidePct(110, 0), null);
  });

  // 6. India and US use the identical mathematical rule — the helper takes
  //    plain numbers, so the same inputs give the same outputs regardless of
  //    market; currency/locale formatting lives entirely in the component.
  test("identical math for IN-style and US-style magnitudes", () => {
    const inPct = computeEstimatedUpsidePct(1076.30, 1056.30); // ₹-scale
    const usPct = computeEstimatedUpsidePct(107.630, 105.630); // $-scale (÷10)
    assert.ok(Math.abs(inPct - usPct) < 1e-9, `${inPct} vs ${usPct}`);
  });

  // 7. Negative upside (live price above target) is reported with its true
  //    sign, not silently clamped or mangled.
  test("negative upside keeps its sign", () => {
    const pct = computeEstimatedUpsidePct(95, 100);
    assert.ok(Math.abs(pct - -5) < 1e-9, `got ${pct}`);
  });

  // 8. isValidPrice boundary behavior.
  test("isValidPrice accepts positive finite numbers only", () => {
    assert.equal(isValidPrice(0.01), true);
    assert.equal(isValidPrice(1e9), true);
    for (const bad of [0, -0.01, NaN, Infinity, -Infinity, null, undefined]) {
      assert.equal(isValidPrice(bad), false, `value=${bad}`);
    }
  });

  // 9. Generated-summary gate: eligible to render (with its generation-time
  //    label) ONLY when both the generation price and target are valid —
  //    the backend fabricates "Target ₹0.00 implies 0% upside" into the
  //    frozen sentence when either was missing at generation.
  test("generated summary eligible only with valid generation price AND target", () => {
    assert.equal(hasValidGenerationBasis(1026.90, 1076.30), true);
    assert.equal(hasValidGenerationBasis(105.63, 107.63), true); // US-scale, identical rule
  });

  // 10. Invalid generation price → summary narrative suppressed (fallback).
  test("invalid generation price suppresses the generated target/upside narrative", () => {
    for (const bad of [null, undefined, 0, -5, NaN, Infinity, -Infinity]) {
      assert.equal(hasValidGenerationBasis(bad, 1076.30), false, `generationPrice=${bad}`);
    }
  });

  // 11. Invalid target → summary narrative suppressed (fallback). This is
  //     the exact input state under which the backend template would have
  //     embedded a fabricated "0% upside" — it must never be shown.
  test("invalid target suppresses the generated target/upside narrative", () => {
    for (const bad of [null, undefined, 0, -50, NaN, Infinity, -Infinity]) {
      assert.equal(hasValidGenerationBasis(1026.90, bad), false, `target=${bad}`);
    }
  });

  console.log(`\nprice-basis regression: ${n} tests passed`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
