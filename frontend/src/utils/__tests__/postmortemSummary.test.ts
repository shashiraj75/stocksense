import { describe, it, expect } from "vitest";
import { fmtAbs, fmtPct, currencySymbolFor, buildLayer1Summary } from "@/utils/postmortemSummary";
import type { CurrentReportReadResponse } from "@/utils/api";

describe("fmtAbs — regression coverage for the reported '+1896.45' defect", () => {
  it("thousands-groups an India amount with the rupee symbol: +₹1,896.45", () => {
    expect(fmtAbs(1896.45, currencySymbolFor("IN"))).toBe("+₹1,896.45");
  });

  it("thousands-groups a US amount with the dollar symbol", () => {
    expect(fmtAbs(12345.6, currencySymbolFor("US"))).toBe("+$12,345.60");
  });

  it("never attaches a currency symbol to a percentage", () => {
    expect(fmtPct(2.2)).toBe("+2.20%");
    expect(fmtPct(2.2)).not.toMatch(/[₹$]/);
  });

  it("places the sign before the currency symbol for a negative amount", () => {
    expect(fmtAbs(-50, currencySymbolFor("US"))).toBe("-$50.00");
  });

  it("shows N/A for a null amount, never a bare currency symbol", () => {
    expect(fmtAbs(null, currencySymbolFor("IN"))).toBe("N/A");
  });

  it("shows a plain number, no invented symbol, for an unrecognized market", () => {
    expect(fmtAbs(100, currencySymbolFor("XYZ"))).toBe("+100.00");
  });
});

function makeReport(overrides: Partial<CurrentReportReadResponse>): CurrentReportReadResponse {
  return {
    trade_id: 280, availability: "READY", report_schema_version: "1.2.0", calculation_version: "1",
    attribution_rules_version: "1", evidence_bundle_version: "1", market: "IN",
    report_trading_date: "2026-08-05", market_timezone: "Asia/Kolkata", status: "LIMITED_EVIDENCE",
    generated_at: "2026-08-05T05:41:03Z",
    structured_report: {
      postmortem: {
        outcome: "WIN", realized_pnl_abs: 1896.45, realized_pnl_pct: 2.2, exit_mechanism: "TARGET_HIT",
      },
      price_path: { version_and_provenance: {} },
    } as unknown as CurrentReportReadResponse["structured_report"],
    claims: [], evidence_items: [], evidence_gaps: [], warnings: [], source_manifest: null,
    supersedes_report_id: null,
    ...overrides,
  };
}

describe("buildLayer1Summary — currency formatting matches the summary card", () => {
  it("formats realized P&L with the market-derived currency and thousands grouping, not a bare number", () => {
    const summary = buildLayer1Summary(makeReport({ market: "IN" }));
    expect(summary).toContain("+₹1,896.45");
    expect(summary).not.toContain("+1896.45");
  });

  it("uses the dollar symbol for a US report", () => {
    const summary = buildLayer1Summary(makeReport({ market: "US" }));
    expect(summary).toContain("+$1,896.45");
  });
});
