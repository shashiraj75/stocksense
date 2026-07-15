#!/usr/bin/env node
/**
 * 3-phase US Daily Picks upgrade — static source assertions (no compile, no
 * network) for the Daily Picks timing copy fix and the premarket status
 * badge. The US base run moved from a stale "6:00 PM IST" claim to the
 * real early-run schedule; India's "2 AM IST" copy is untouched.
 *
 * Run from frontend/:  node scripts/test-picks-premarket-copy.mjs
 */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

let n = 0;
const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

const page = readFileSync("src/app/picks/page.tsx", "utf8");

test("misleading '6:00 PM IST' US copy is gone", () => {
  assert.ok(!/6:00\s*PM\s*IST/i.test(page), "stale 6:00 PM IST claim still present");
  assert.ok(!/6\s*PM\s*IST/i.test(page), "stale 6 PM IST claim still present");
});

test("US copy shows the real base-run time (06:00 UTC, corrected 2026-07-15 from the prior 04:00 UTC)", () => {
  assert.ok(page.includes("10:00 AM Dubai"), "must show 10:00 AM Dubai");
  assert.ok(page.includes("11:30 AM IST"), "must show 11:30 AM IST");
  assert.ok(!page.includes("8:00 AM Dubai"), "stale 04:00-UTC-era 8:00 AM Dubai claim still present");
  assert.ok(!page.includes("9:30 AM IST"), "stale 04:00-UTC-era 9:30 AM IST claim still present");
});

test("US copy mentions the Premarket Review runs after base generation completes (not before)", () => {
  assert.ok(/after today's base picks complete/i.test(page),
    "must disclose the Premarket Review stage runs after, not before, base generation");
  assert.ok(!/Premarket refresh runs before US market open/i.test(page),
    "stale pre-3-phase-split phrasing must not remain");
});

test("India genTime copy is unchanged", () => {
  assert.ok(page.includes('genTime: "2 AM IST"'), "India's 2 AM IST copy must be untouched");
});

test("premarket status badge renders unconditionally for US with a truthful pending fallback", () => {
  assert.ok(page.includes('const effectiveStatus = data?.premarket_status ?? "pending";'),
    "badge must derive an effective status with a pending fallback, not stay silent when premarket_status is absent");
  assert.ok(page.includes("PREMARKET_STATUS_LABEL"), "must map backend premarket_status to a display label");
});

test("skipped/failed outcomes do not misleadingly carry the future-looking schedule label", () => {
  assert.ok(page.includes('const isPending = !isCompletedOutcome && effectiveStatus !== "skipped"'),
    "only pending should get the 'Scheduled for 6:00 AM ET' label — skipped/failed are terminal outcomes");
});

test("no old cron-time artifacts leaked into copy (12:17 UTC / 8:17 AM ET / 5:47 PM IST / 7:35 AM)", () => {
  for (const stale of ["12:17 UTC", "8:17 AM ET", "5:47 PM IST", "7:35 AM"]) {
    assert.ok(!page.includes(stale), `stale copy fragment still present: ${stale}`);
  }
});

console.log(`\npicks-premarket-copy regression: ${n} tests passed`);
