import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Finding #15 from the Stock Detail page forensic audit (Product Integrity
// #016) — score-history had no market scoping, confirmed in production
// (225 symbols exist in both IN and US universes). page.tsx mounts through
// live data-fetching impractical to isolate here, matching this file's
// existing test convention — source-text assertions.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);
const apiSource = readFileSync(
  path.resolve(process.cwd(), "src/utils/api.ts"),
  "utf-8",
);

describe("fetchScoreHistory requires a market parameter", () => {
  it("api.ts's signature includes market before horizon", () => {
    expect(apiSource).toContain(
      "export const fetchScoreHistory = (symbol: string, market: Market, horizon: Horizon, days = 90) =>"
    );
  });

  it("sends market as a query param", () => {
    expect(apiSource).toContain("{ params: { market, horizon, days } }");
  });
});

describe("Stock Detail page's score-history query is market-scoped", () => {
  it("queryKey includes market — a symbol switching markets gets a distinct cache entry", () => {
    expect(pageSource).toContain('queryKey: ["score-history", symbol, market, historyHorizon],');
  });

  it("passes market through to fetchScoreHistory", () => {
    expect(pageSource).toContain("queryFn: () => fetchScoreHistory(symbol, market, historyHorizon, 90),");
  });
});
