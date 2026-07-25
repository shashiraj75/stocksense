import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Market Leadership context is equity-only (RS/Trend Lifecycle have no
// crypto meaning) — this proves the render site is gated on !isCrypto,
// matching the source-level wiring convention already established by
// dataLimitationsWiring.test.ts for this same page file.
const src = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);

describe("Market Leadership wiring — crypto pages never invoke or render the component", () => {
  it("imports MarketLeadershipContext", () => {
    expect(src).toContain('import { MarketLeadershipContext } from "@/components/MarketLeadershipContext";');
  });

  it("renders MarketLeadershipContext only inside a block gated by !isCrypto", () => {
    const componentIdx = src.indexOf("<MarketLeadershipContext symbol={symbol} market={market} />");
    expect(componentIdx).toBeGreaterThan(-1);

    // Walk backward to the nearest `{... && (` guard opening this JSX
    // expression and confirm !isCrypto is part of that same condition.
    const guardStart = src.lastIndexOf("{tab !== \"backtest\"", componentIdx);
    expect(guardStart).toBeGreaterThan(-1);
    const guardClause = src.slice(guardStart, componentIdx);
    expect(guardClause).toContain("!isCrypto");
  });
});
