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

test("US copy shows the real early base-run time", () => {
  assert.ok(page.includes("8:00 AM Dubai"), "must show 8:00 AM Dubai");
  assert.ok(page.includes("9:30 AM IST"), "must show 9:30 AM IST");
});

test("US copy mentions the premarket refresh", () => {
  assert.ok(/Premarket refresh runs before US market open/i.test(page),
    "must disclose the premarket finalizer phase for US");
});

test("India genTime copy is unchanged", () => {
  assert.ok(page.includes('genTime: "2 AM IST"'), "India's 2 AM IST copy must be untouched");
});

test("premarket status badge renders only when the field is present, US-only", () => {
  assert.ok(page.includes('market === "US" && data?.premarket_status'),
    "badge must be gated on market === US and a real premarket_status value, never a fabricated default");
  assert.ok(page.includes("PREMARKET_STATUS_LABEL"), "must map backend premarket_status to a display label");
});

test("no old cron-time artifacts leaked into copy (12:17 UTC / 8:17 AM ET / 5:47 PM IST / Pre-Open)", () => {
  for (const stale of ["12:17 UTC", "8:17 AM ET", "5:47 PM IST", "US Pre-Open"]) {
    assert.ok(!page.includes(stale), `stale copy fragment still present: ${stale}`);
  }
});

console.log(`\npicks-premarket-copy regression: ${n} tests passed`);
