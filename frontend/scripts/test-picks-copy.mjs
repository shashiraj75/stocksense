#!/usr/bin/env node
/**
 * Release 12B — Daily Picks coverage-wording truthfulness checks.
 *
 * Static source assertions (no compile, no network): the Daily Picks page and
 * layout must never claim full NSE/US exchange coverage or hardcode a
 * screened count — the count must come from returned payload metadata.
 *
 * Run from frontend/:  node scripts/test-picks-copy.mjs
 */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

let n = 0;
const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

const page = readFileSync("src/app/picks/page.tsx", "utf8");
const layout = readFileSync("src/app/picks/layout.tsx", "utf8");

test("no full-exchange coverage claim anywhere", () => {
  for (const [name, src] of [["page.tsx", page], ["layout.tsx", layout]]) {
    assert.ok(!/full NSE/i.test(src), `${name} claims full NSE coverage`);
    assert.ok(!/full US universe/i.test(src), `${name} claims full US coverage`);
    assert.ok(!/all NSE-listed stocks are (screened|covered)/i.test(src), name);
  }
});

test("screened count comes from payload metadata, never hardcoded", () => {
  assert.ok(!/screened from 25\b/.test(page), "hardcoded 25 in page copy");
  assert.ok(page.includes("data?.screened_from"), "count must come from screened_from");
  assert.ok(page.includes("data.screened_from.toLocaleString()"), "count rendered from metadata");
});

test("India wording is universe-truthful", () => {
  assert.ok(page.includes("eligible"), "count line must say 'eligible' stocks");
  assert.ok(page.includes("quality-filtered universe"), "count line must scope the universe");
  assert.ok(page.includes("not all"), "disclosure must state coverage is not all listed stocks");
});

test("legacy payloads render safely with no count claim", () => {
  // The count sentence is conditional on screened_from being present/truthy —
  // a legacy payload without it renders no count and no NaN/undefined.
  const conditional = /data\?\.screened_from\s*\n?\s*\?/.test(page) || page.includes("data?.screened_from\n          ?");
  assert.ok(conditional, "count sentence must be conditional on screened_from");
});

test("layout copy is neutral and market-varying", () => {
  assert.ok(layout.includes("quality-filtered market universes"), "layout must use neutral universe wording");
  assert.ok(/coverage varies by market/i.test(layout), "layout must disclose per-market coverage variance");
});

console.log(`\npicks-copy regression: ${n} tests passed`);
