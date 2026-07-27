import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// DP-026 — the per-stock "✓ NN% accurate" chip is computed per selected
// horizon (`stockAccuracy?.accuracy?.[horizon]`) and rendered for any
// market's symbol page (India or US symbols both route through this same
// component/file), so a single marker in this one render path covers both
// markets and all three horizons by construction.
const src = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);

describe("DP-026 disclosure wiring — per-stock accuracy chip", () => {
  it("imports DataLimitationsMark", () => {
    expect(src).toContain('import { DataLimitationsMark } from "@/components/DataLimitationsNotice";');
  });

  it("renders the mark next to the per-stock accuracy chip, inside the acc-guard block", () => {
    const guardIdx = src.indexOf("if (!acc || !acc.total || acc.total < 5) return null;");
    const markIdx = src.indexOf("<DataLimitationsMark />");
    expect(guardIdx).toBeGreaterThan(-1);
    expect(markIdx).toBeGreaterThan(guardIdx);
  });

  it("uses the per-horizon accuracy lookup (acc = stockAccuracy?.accuracy?.[horizon]) — one code path serves short/medium/long", () => {
    expect(src).toContain("const acc = stockAccuracy?.accuracy?.[horizon];");
  });

  it("appears exactly once — not duplicated across the fundamentals/history/backtest tabs", () => {
    const occurrences = src.split("<DataLimitationsMark").length - 1;
    expect(occurrences).toBe(1);
  });

  it("is not rendered on the Paper Trade button/modal trigger block", () => {
    const paperTradeIdx = src.indexOf('onClick={() => setShowPaperModal(true)}');
    const markIdx = src.indexOf("<DataLimitationsMark />");
    expect(paperTradeIdx).toBeGreaterThan(-1);
    expect(markIdx).toBeLessThan(paperTradeIdx);
  });
});
