import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// DP-026 user-facing disclosure on the standalone /validation page — this
// page renders results for every horizon (short/medium/long) and universe
// (nifty100/midcap/us, i.e. both India and US) via the same `res` object
// and the same JSX, so a single unconditional notice covers all of them by
// construction, not per-combination branches.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/validation/page.tsx"),
  "utf-8",
);

describe("DP-026 disclosure wiring — /validation page", () => {
  it("imports DataLimitationsNotice", () => {
    expect(pageSource).toContain('import { DataLimitationsNotice } from "@/components/DataLimitationsNotice";');
  });

  it("renders the notice once results are available, before the primary metrics grid", () => {
    const resultsBlockStart = pageSource.indexOf("{/* Results */}");
    const noticeIndex = pageSource.indexOf("<DataLimitationsNotice", resultsBlockStart);
    const metricsIndex = pageSource.indexOf("{/* Primary metrics */}", resultsBlockStart);
    expect(resultsBlockStart).toBeGreaterThan(-1);
    expect(noticeIndex).toBeGreaterThan(resultsBlockStart);
    expect(metricsIndex).toBeGreaterThan(noticeIndex);
  });

  it("horizon selector (short/medium/long) and universe selector (nifty100/midcap/us) both exist and neither gates the notice — one render path covers all combinations", () => {
    expect(pageSource).toContain('{ key: "short",  label: "Short",  sub: "5-day forward" }');
    expect(pageSource).toContain('{ key: "medium", label: "Medium", sub: "21-day forward" }');
    expect(pageSource).toContain('{ key: "long",   label: "Long",   sub: "63-day forward" }');
    expect(pageSource).toContain('{ key: "nifty100", label: "🇮🇳 Nifty 100"');
    const noticeLine = pageSource.split("\n").find((l) => l.includes("<DataLimitationsNotice"))!;
    expect(noticeLine).not.toMatch(/&&|\?/);
  });

  it("is rendered inside the `{res && (...)}` results branch, not gated by an extra data_limitations check", () => {
    const resultsBranchIdx = pageSource.indexOf("{res && (");
    const noticeIdx = pageSource.indexOf("<DataLimitationsNotice");
    expect(resultsBranchIdx).toBeGreaterThan(-1);
    expect(noticeIdx).toBeGreaterThan(resultsBranchIdx);
    expect(pageSource).not.toMatch(/res\.data_limitations\s*&&/);
  });
});
