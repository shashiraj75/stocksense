#!/usr/bin/env node
/**
 * Deterministic regression tests for the Paper Trading Trade History
 * lazy-older-page cursor seeding and de-duplication logic
 * (src/utils/closedTradeHistoryPaging.ts) — fixes the confirmed
 * duplicate-row defect (e.g. the observed duplicated LLY trade) where the
 * first "Show N earlier closed trades" request started from a null
 * cursor, causing the older-history endpoint to re-return rows already
 * shown in `latest_trades`.
 *
 * This repo has no frontend test framework (a long-standing, documented
 * state — see EPIC-005 Sprint #012 and frontend/scripts/test-price-basis.mjs).
 * Rather than introduce one for two pure functions, this standalone script
 * compiles the helpers with the project's own TypeScript compiler and
 * asserts against them directly. Zero new dependencies; no network.
 *
 * Run from frontend/:  node scripts/test-closed-trade-history-paging.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "closed-trade-history-paging-test-"));
try {
  execSync(
    `npx tsc src/utils/closedTradeHistoryPaging.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { seedInitialCursor, dedupeAppend } =
    await import(pathToFileURL(join(outDir, "closedTradeHistoryPaging.js")).href);

  let n = 0;
  const test = (name, fn) => { fn(); n += 1; console.log(`  ok - ${name}`); };

  // ── seedInitialCursor ──────────────────────────────────────────────────

  test("seeds the cursor from the final (oldest) row of latest_trades, not null", () => {
    const latestTrades = [
      { id: 10, closed_at: "2026-01-05T00:00:00+00:00" },
      { id: 9,  closed_at: "2026-01-04T00:00:00+00:00" },
      { id: 8,  closed_at: "2026-01-03T00:00:00+00:00" },
      { id: 7,  closed_at: "2026-01-02T00:00:00+00:00" },
      { id: 6,  closed_at: "2026-01-01T00:00:00+00:00" }, // final/oldest of the initial 5
    ];
    const cursor = seedInitialCursor(latestTrades);
    assert.notEqual(cursor, null, "cursor must never be null when initial rows exist");
    assert.equal(cursor.before_id, 6);
    assert.equal(cursor.before_closed_at, "2026-01-01T00:00:00+00:00");
  });

  test("returns null when there are no initial rows (nothing to seed from)", () => {
    assert.equal(seedInitialCursor([]), null);
  });

  test("returns null rather than a broken cursor when the final row has no closed_at", () => {
    const cursor = seedInitialCursor([{ id: 1, closed_at: null }]);
    assert.equal(cursor, null);
  });

  // ── dedupeAppend ───────────────────────────────────────────────────────

  test("drops trades whose id is already in the seen set", () => {
    const seen = new Set([1, 2, 3]);
    const { fresh, nextSeenIds } = dedupeAppend(seen, [
      { id: 3, symbol: "LLY" },
      { id: 4, symbol: "LLY" },
      { id: 5, symbol: "AAPL" },
    ]);
    assert.deepEqual(fresh.map(t => t.id), [4, 5], "id 3 was already seen and must be dropped");
    assert.ok(nextSeenIds.has(3) && nextSeenIds.has(4) && nextSeenIds.has(5));
  });

  test("a duplicate backend response cannot produce duplicate ids after two appends", () => {
    // Simulates the exact confirmed-defect scenario: the first older-page
    // response accidentally overlaps with latest_trades (e.g. because a
    // caller forgot to seed the cursor) — dedupeAppend must still yield
    // zero duplicate ids across both calls.
    const initialIds = new Set([10, 9, 8, 7, 6]); // latest_trades ids
    const overlappingOlderPage = [
      { id: 6, symbol: "LLY" },  // duplicate of latest_trades' last row
      { id: 5, symbol: "LLY" },  // genuinely new
      { id: 4, symbol: "MSFT" }, // genuinely new
    ];
    const { fresh, nextSeenIds } = dedupeAppend(initialIds, overlappingOlderPage);
    assert.deepEqual(fresh.map(t => t.id), [5, 4], "id 6 must be dropped as already-seen");
    // Simulate a second page fetch after this — no id from either page repeats.
    const secondPage = [{ id: 3, symbol: "LLY" }, { id: 4, symbol: "MSFT" }]; // id 4 repeats
    const second = dedupeAppend(nextSeenIds, secondPage);
    assert.deepEqual(second.fresh.map(t => t.id), [3], "id 4 already seen from page 1 must be dropped");
  });

  test("distinct trades with identical visible values but different ids are both retained", () => {
    const seen = new Set();
    const { fresh } = dedupeAppend(seen, [
      { id: 101, symbol: "LLY", entry_price: 700, exit_price: 720, closed_at: "2026-02-01T00:00:00Z" },
      { id: 102, symbol: "LLY", entry_price: 700, exit_price: 720, closed_at: "2026-02-01T00:00:00Z" },
    ]);
    assert.equal(fresh.length, 2, "two distinct trade_ids with identical visible fields must both be retained");
  });

  test("rapid repeated append with the identical page object cannot append the same page twice", () => {
    // Models "double click" at the call-site level: dedupeAppend is called
    // twice with the exact same fetched page and an already-updated seen
    // set from the first call — the second call must contribute nothing.
    const page = [{ id: 1 }, { id: 2 }];
    const first = dedupeAppend(new Set(), page);
    const second = dedupeAppend(first.nextSeenIds, page);
    assert.equal(second.fresh.length, 0, "re-appending the same page must add zero rows the second time");
  });

  console.log(`\n${n} closed-trade-history-paging tests passed.`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
