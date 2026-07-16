import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Stock Detail hero card — "Day Open" stat pill. page.tsx mounts through
// live data-fetching (useQuery, router, auth context) that isn't practically
// mountable in isolation here, matching this repo's existing convention for
// this situation (see picks/__tests__/premarketSchedule.test.ts) — source-text
// assertions instead of a full render.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);
const apiSource = readFileSync(
  path.resolve(process.cwd(), "src/utils/api.ts"),
  "utf-8",
);

describe("StockQuote type declares open price", () => {
  it("the open field is present on the StockQuote interface", () => {
    const interfaceBlock = apiSource.match(/export interface StockQuote \{[\s\S]*?\}/)?.[0] ?? "";
    expect(interfaceBlock).toMatch(/open\?:\s*number;/);
  });
});

describe("Day Open stat pill", () => {
  it("renders only when quote.open is present, matching the existing high/low pattern", () => {
    expect(pageSource).toContain(
      '...(quote.open != null ? [["Day Open", `${currency}${quote.open.toLocaleString()}`, "text-gray-200"]] : []),'
    );
  });

  it("Day Open appears before Day High/Day Low in the pill array (left-to-right reading order)", () => {
    const openIdx = pageSource.indexOf('["Day Open",');
    const highIdx = pageSource.indexOf('["Day High",');
    expect(openIdx).toBeGreaterThan(-1);
    expect(highIdx).toBeGreaterThan(-1);
    expect(openIdx).toBeLessThan(highIdx);
  });
});
