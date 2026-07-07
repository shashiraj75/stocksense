#!/usr/bin/env node
/**
 * Deterministic regression tests for the synchronous in-flight request
 * guard (src/utils/inFlightGuard.ts) — closes the concurrency-hardening
 * gap where `loadNextPage` in ClosedTradeHorizonBlock relied only on
 * `loadingOlder` React state (batched, not synchronous) plus a disabled
 * button, letting two near-simultaneous invocations both read a stale
 * "not loading" closure before React re-rendered and issue duplicate
 * network requests for the same lazy "older closed trades" page.
 *
 * This repo has no frontend test framework — see
 * frontend/scripts/test-price-basis.mjs for the established precedent this
 * script follows (compile the pure module with the project's own
 * TypeScript compiler, assert against it directly, zero new dependencies).
 *
 * Run from frontend/:  node scripts/test-in-flight-guard.mjs
 */
import { execSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

const outDir = mkdtempSync(join(tmpdir(), "in-flight-guard-test-"));
try {
  execSync(
    `npx tsc src/utils/inFlightGuard.ts --outDir ${JSON.stringify(outDir)} --module es2020 --target es2020 --strict --skipLibCheck`,
    { stdio: "inherit" },
  );
  const { runIfNotInFlight } = await import(pathToFileURL(join(outDir, "inFlightGuard.js")).href);

  let n = 0;
  const test = async (name, fn) => { await fn(); n += 1; console.log(`  ok - ${name}`); };

  // A controllable "network call" — resolves only when the test explicitly
  // releases it, so we can deterministically simulate "still in flight".
  function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
  }

  await test("first call acquires the lock synchronously before network work starts", async () => {
    const flag = { current: false };
    const gate = deferred();
    let fnStarted = false;
    const p = runIfNotInFlight(flag, async () => {
      fnStarted = true;
      await gate.promise;
      return "done";
    });
    // Give the microtask queue one tick so the async function body runs up
    // to its first await — the flag must already be true by then, proving
    // it was set before (synchronously ahead of) the network call.
    await Promise.resolve();
    assert.equal(flag.current, true, "flag must be set before the awaited call resolves");
    assert.equal(fnStarted, true);
    gate.resolve();
    await p;
  });

  await test("a second immediate invocation is rejected while the first is in flight", async () => {
    const flag = { current: false };
    const gate = deferred();
    let secondFnRan = false;
    const first = runIfNotInFlight(flag, async () => {
      await gate.promise;
      return "first";
    });
    // Fired immediately, before the first call's awaited work resolves.
    const second = runIfNotInFlight(flag, async () => {
      secondFnRan = true;
      return "second";
    });
    const secondResult = await second;
    assert.equal(secondResult, undefined, "second call must be rejected (undefined), not run fn");
    assert.equal(secondFnRan, false, "the second call's fn must never execute while the first is in flight");
    gate.resolve();
    const firstResult = await first;
    assert.equal(firstResult, "first");
  });

  await test("the lock is released after a successful request", async () => {
    const flag = { current: false };
    await runIfNotInFlight(flag, async () => "ok");
    assert.equal(flag.current, false, "flag must be released after success");
  });

  await test("the lock is released after a failed request", async () => {
    const flag = { current: false };
    await assert.rejects(
      runIfNotInFlight(flag, async () => { throw new Error("network failure"); }),
    );
    assert.equal(flag.current, false, "flag must be released even when fn throws/rejects");
  });

  await test("a later call after release starts normally and its fn executes", async () => {
    const flag = { current: false };
    await runIfNotInFlight(flag, async () => "first page");
    let secondFnRan = false;
    const secondResult = await runIfNotInFlight(flag, async () => { secondFnRan = true; return "second page"; });
    assert.equal(secondFnRan, true, "a call issued after the previous one released must run normally");
    assert.equal(secondResult, "second page");
    assert.equal(flag.current, false);
  });

  console.log(`\n${n} in-flight-guard tests passed.`);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}
