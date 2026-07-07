#!/usr/bin/env node
/**
 * Deterministic regression tests for the Paper Trading closed-trade
 * outcome-label and break-even-visibility logic
 * (src/utils/paperTradeOutcome.ts).
 *
 * This repo has no frontend test framework — see
 * frontend/scripts/test-price-basis.mjs for the established precedent this
 * script follows (compile the pure module with the project's own
 * TypeScript compiler, assert against it directly, zero new dependencies).
 *
 * Run from frontend/:  node scripts/test-paper-trade-outcome.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "paper-trade-outcome-test-"));
try {
  execSync(
    `npx tsc src/utils/paperTradeOutcome.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { outcomeLabel, shouldShowBreakEven } =
    await import(pathToFileURL(join(outDir, "paperTradeOutcome.js")).href);

  let n = 0;
  const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

  test("TARGET_HIT maps to 'Target Hit'", () => {
    assert.equal(outcomeLabel("TARGET_HIT"), "Target Hit");
  });

  test("STOP_LOSS maps to 'Stop Loss'", () => {
    assert.equal(outcomeLabel("STOP_LOSS"), "Stop Loss");
  });

  test("MANUAL maps to 'Manual'", () => {
    assert.equal(outcomeLabel("MANUAL"), "Manual");
  });

  test("null exit_reason maps to 'Non-conclusive'", () => {
    assert.equal(outcomeLabel(null), "Non-conclusive");
  });

  test("undefined exit_reason maps to 'Non-conclusive'", () => {
    assert.equal(outcomeLabel(undefined), "Non-conclusive");
  });

  test("an unrecognized/legacy exit_reason value maps to 'Non-conclusive'", () => {
    assert.equal(outcomeLabel("EXPIRED"), "Non-conclusive");
    assert.equal(outcomeLabel("CANCELLED"), "Non-conclusive");
    assert.equal(outcomeLabel("some-legacy-value"), "Non-conclusive");
  });

  test("break-even is shown only when count > 0", () => {
    assert.equal(shouldShowBreakEven(0), false);
    assert.equal(shouldShowBreakEven(1), true);
    assert.equal(shouldShowBreakEven(5), true);
  });

  console.log(`\n${n} paper-trade-outcome tests passed.`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
