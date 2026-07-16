import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Regression tests for the 7 HIGH-severity findings from the Stock Detail
// page forensic audit. page.tsx mounts through live data-fetching
// (useQuery, router, auth context) that isn't practically mountable in
// isolation here, matching this repo's existing convention for this
// situation (see picks/__tests__/premarketSchedule.test.ts) — source-text
// assertions instead of a full render.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);

describe("Finding #1/#2 — AI Signal card discloses the Medium Term fallback", () => {
  it("shows a '· Medium Term' label when on the Fundamentals or History tab", () => {
    expect(pageSource).toContain('{(tab === "fundamentals" || tab === "history") && (');
    expect(pageSource).toContain('<span className="normal-case tracking-normal text-gray-600"> · Medium Term</span>');
  });

  it("the disclosure sits inside the same 'AI Signal' label the hero card already shows", () => {
    const idx = pageSource.indexOf('AI Signal\n                            {/* Fundamentals/History');
    expect(idx).toBeGreaterThan(-1);
  });
});

describe("Finding #4 — 52W High/Low pills are null-guarded", () => {
  it("52W High only renders when quote.fifty_two_week_high is present", () => {
    expect(pageSource).toContain(
      '...(quote.fifty_two_week_high != null ? [["52W High", `${currency}${quote.fifty_two_week_high.toLocaleString()}`, "text-gray-200"]] : []),'
    );
  });

  it("52W Low only renders when quote.fifty_two_week_low is present", () => {
    expect(pageSource).toContain(
      '...(quote.fifty_two_week_low != null ? [["52W Low", `${currency}${quote.fifty_two_week_low.toLocaleString()}`, "text-gray-200"]] : []),'
    );
  });

  it("no longer unconditionally interpolates fifty_two_week_high/low (the literal 'undefined' bug)", () => {
    expect(pageSource).not.toContain(
      '["52W High", `${currency}${quote.fifty_two_week_high?.toLocaleString()}`, "text-gray-200"],'
    );
  });
});

describe("Finding #5 — AI Prediction card's signal strip uses getSignalTone", () => {
  it("the strip container class comes from getSignalTone's tone.container, not raw signal branching", () => {
    const stripBlock = pageSource.split("Signal strip — uses getSignalTone")[1]?.slice(0, 2000) ?? "";
    expect(stripBlock).toContain("const stripTone = getSignalTone(prediction.signal, prediction.confidence);");
    expect(stripBlock).toContain("stripTone.container");
    expect(stripBlock).toContain("stripTone.text");
  });

  it("no longer colors the strip purely from prediction.signal (ignoring confidence)", () => {
    expect(pageSource).not.toContain(
      'prediction.signal === "BUY"  ? "bg-bull/10 border-bull/30" :\n                    prediction.signal === "SELL" ? "bg-bear/10 border-bear/30" :\n                    "bg-neutral/10 border-neutral/30"'
    );
  });
});

describe("Finding #3 — Trade Levels discloses drift between live quote and prediction.current_price", () => {
  it("compares quote.price against cp (prediction.current_price) with the same 0.01 threshold PaperTradeModal uses", () => {
    expect(pageSource).toContain(
      '{!isCrypto && quote?.price != null && cp != null && Math.abs(quote.price - cp) > 0.01 && ('
    );
  });

  it("discloses which price the levels were computed against", () => {
    expect(pageSource).toContain("Levels based on {currency}{fmt(cp)} (price when this prediction was generated)");
  });
});

describe("Finding #6 — Paper Trade button disabled during a background horizon refetch", () => {
  it("the button is disabled while predFetching is true", () => {
    expect(pageSource).toContain("disabled={predFetching}");
  });

  it("shows an 'Updating…' state instead of Paper Buy/Sell while fetching", () => {
    expect(pageSource).toContain('predFetching ? "Updating…" : isBuy ? "Paper Buy" : isSell ? "Paper Sell / Short" : "Paper Trade"');
  });
});

describe("Finding #7 — Backtest state resets on symbol/market navigation", () => {
  it("has a useEffect keyed on [symbol, market] that clears btData/btError/btRunning", () => {
    const effectBlock = pageSource.split("Backtest results are plain useState")[1]?.slice(0, 700) ?? "";
    expect(effectBlock).toContain("setBtData(null);");
    expect(effectBlock).toContain('setBtError("");');
    expect(effectBlock).toContain("setBtRunning(false);");
    expect(effectBlock).toContain("}, [symbol, market]);");
  });
});
