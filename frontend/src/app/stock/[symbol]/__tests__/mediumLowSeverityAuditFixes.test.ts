import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Regression tests for the 14 MEDIUM/LOW-severity findings fixed from the
// Stock Detail page forensic audit (Product Integrity #014's follow-up
// pass; #15 — score-history market scoping — is excluded, it requires a
// backend schema change and is tracked separately). page.tsx mounts through
// live data-fetching/auth/router context impractical to isolate here,
// matching this file's existing test convention — source-text assertions.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);
const scoreHistoryChartSource = readFileSync(
  path.resolve(process.cwd(), "src/components/ScoreHistoryChart.tsx"),
  "utf-8",
);

describe("Findings #8/#9 — Market Regime and Evidence/Research gated to horizon tabs", () => {
  it("Market Regime is gated the same way as Trade Levels (Short/Medium/Long only)", () => {
    expect(pageSource).toContain(
      'tab !== "backtest" && tab !== "history" && tab !== "fundamentals" && prediction?.market_regime && !predLoading'
    );
  });

  it("EvidenceSummary is gated the same way", () => {
    expect(pageSource).toContain(
      'tab !== "backtest" && tab !== "history" && tab !== "fundamentals" && !predLoading && <EvidenceSummary'
    );
  });

  it("ResearchSummary is gated the same way", () => {
    expect(pageSource).toContain(
      'tab !== "backtest" && tab !== "history" && tab !== "fundamentals" && !predLoading && <ResearchSummary'
    );
  });
});

describe("Finding #10 — hero AI Signal card shows an updating indicator", () => {
  it("shows '· updating…' while predFetching is true", () => {
    expect(pageSource).toContain('{predFetching && (');
    expect(pageSource).toContain('<span className="normal-case tracking-normal text-yellow-500/80"> · updating…</span>');
  });
});

describe("Finding #11 — sentiment/news independent-cache disclosure", () => {
  it("discloses that sentiment and the news list may not share a fetch window", () => {
    expect(pageSource).toContain(
      "Sentiment score above is computed independently and may not reflect the exact same fetch window as the articles below."
    );
  });
});

describe("Finding #12 — Take Profit always renders as the success color", () => {
  it("Take Profit box is unconditionally bull/green", () => {
    expect(pageSource).toContain('<div className="rounded-xl border p-4 bg-bull/10 border-bull/30">\n                        <p className="text-xs text-gray-400 mb-1">Take Profit</p>');
  });

  it("no longer computes tpUp off raw price-direction", () => {
    expect(pageSource).not.toContain('const tpUp = tpPct === null || parseFloat(tpPct) >= 0;');
  });
});

describe("Finding #13 — Mkt Cap uses ₹ Cr convention for India", () => {
  it("formats India market cap as ₹X Cr instead of T/B/M", () => {
    expect(pageSource).toContain('if (market === "IN") return `₹${Math.round(v / 1e7).toLocaleString()} Cr`;');
  });
});

describe("Finding #14 — hero header company name includes usFund fallback", () => {
  it("hero header fallback chain matches the sticky ticker's chain", () => {
    expect(pageSource).toContain(
      '{(screenerFund?.company_name || usFund?.company_name || quote?.company_name) && (\n                        <span className="text-2xl font-bold text-gray-400">\n                          {screenerFund?.company_name || usFund?.company_name || quote?.company_name}'
    );
  });
});

describe("Finding #16 — History chart date formatting pinned to UTC", () => {
  it("fmtDate passes timeZone: UTC so the label doesn't shift with the viewer's local timezone", () => {
    expect(scoreHistoryChartSource).toContain('timeZone: "UTC"');
  });
});

describe("Finding #17 — History tab distinguishes loading/error/empty", () => {
  it("ScoreHistoryChart accepts isLoading and isError props", () => {
    expect(scoreHistoryChartSource).toContain("isLoading?: boolean;");
    expect(scoreHistoryChartSource).toContain("isError?: boolean;");
  });

  it("renders a distinct loading state before the empty-points check", () => {
    const loadingIdx = scoreHistoryChartSource.indexOf("if (isLoading) {");
    const errorIdx = scoreHistoryChartSource.indexOf("if (isError) {");
    const emptyIdx = scoreHistoryChartSource.indexOf("if (!points || points.length === 0)");
    expect(loadingIdx).toBeGreaterThan(-1);
    expect(errorIdx).toBeGreaterThan(-1);
    expect(emptyIdx).toBeGreaterThan(-1);
    expect(loadingIdx).toBeLessThan(emptyIdx);
    expect(errorIdx).toBeLessThan(emptyIdx);
  });

  it("page.tsx passes isLoading/isError through from the scoreHistory query", () => {
    expect(pageSource).toContain("isLoading: scoreHistoryLoading, isError: scoreHistoryError");
    expect(pageSource).toContain("isLoading={scoreHistoryLoading}");
    expect(pageSource).toContain("isError={scoreHistoryError}");
  });
});

describe("Finding #19 — defensive trade-level ordering guard", () => {
  it("rejects a BUY where stop/target don't bracket the entry zone correctly", () => {
    expect(pageSource).toContain(
      'if (sig === "BUY" && !(tl.stop_loss < tl.entry_low && tl.take_profit > tl.entry_high)) return null;'
    );
  });

  it("rejects a SELL where stop/target don't bracket the entry zone correctly", () => {
    expect(pageSource).toContain(
      'if (sig === "SELL" && !(tl.stop_loss > tl.entry_high && tl.take_profit < tl.entry_low)) return null;'
    );
  });
});

describe("Finding #20 — Debt/Equity shown as a × ratio, matching Multibagger's convention", () => {
  it("US Key Ratios divides by 100 and appends ×", () => {
    expect(pageSource).toContain('{ label: "Debt/Equity",     val: usFund.debt_to_equity,      fmt: (v: number) => (v / 100).toFixed(2) + "×" },');
  });

  it("India's Debt-to-Equity note uses the same convention", () => {
    expect(pageSource).toContain("{(screenerFund.debt_to_equity_pct / 100).toFixed(2)}×");
  });
});

describe("Finding #21 — US Revenue/Earnings Growth color-coded", () => {
  it("both rows are marked colored: true and rendered with green/red based on sign", () => {
    expect(pageSource).toContain('{ label: "Revenue Growth",  val: usFund.revenue_growth_pct,  fmt: (v: number) => (v >= 0 ? "+" : "") + v.toFixed(1) + "%", colored: true },');
    expect(pageSource).toContain('{ label: "Earnings Growth", val: usFund.earnings_growth_pct, fmt: (v: number) => (v >= 0 ? "+" : "") + v.toFixed(1) + "%", colored: true },');
    expect(pageSource).toContain('colored ? ((val as number) >= 0 ? "text-green-400" : "text-red-400") : "text-white"');
  });
});

describe("Finding #22 — previously-missing US fundamentals fields surfaced", () => {
  it("ROCE, Operating Margin, EV/EBITDA, Interest Coverage, and P/S Ratio now appear in Key Ratios", () => {
    expect(pageSource).toContain('{ label: "ROCE",            val: usFund.roce_pct,            fmt: (v: number) => v.toFixed(1) + "%" },');
    expect(pageSource).toContain('{ label: "Operating Margin", val: usFund.opm_pct,            fmt: (v: number) => v.toFixed(1) + "%" },');
    expect(pageSource).toContain('{ label: "EV/EBITDA",       val: usFund.ev_ebitda,           fmt: (v: number) => v.toFixed(1) + "×" },');
    expect(pageSource).toContain('{ label: "Interest Coverage", val: usFund.interest_coverage_ratio, fmt: (v: number) => v.toFixed(1) + "×" },');
    expect(pageSource).toContain('{ label: "P/S Ratio",       val: usFund.price_to_sales,      fmt: (v: number) => v.toFixed(1) + "×" },');
  });
});

describe("Finding #23 — Backtest zero-trades edge case handled", () => {
  it("shows an explanatory message when total_tests is 0", () => {
    expect(pageSource).toContain("{btData && btData.total_tests === 0 && (");
    expect(pageSource).toContain("No signals to backtest for this symbol/horizon in the available history window");
  });

  it("summary cards only render when total_tests > 0", () => {
    expect(pageSource).toContain("{btData && btData.total_tests > 0 && (");
  });
});
