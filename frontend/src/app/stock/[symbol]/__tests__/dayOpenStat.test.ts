import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Stock Detail hero card — quote stat pill row (Open/High/Low/52W/Mkt Cap/
// Vol). page.tsx mounts through live data-fetching (useQuery, router, auth
// context) that isn't practically mountable in isolation here, matching
// this repo's existing convention for this situation (see
// picks/__tests__/premarketSchedule.test.ts) — source-text assertions
// instead of a full render.
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

describe("D-Open stat pill", () => {
  it("renders only when quote.open is present, matching the existing high/low pattern", () => {
    expect(pageSource).toContain(
      '...(quote.open != null ? [["D-Open", `${currency}${quote.open.toLocaleString()}`, "text-gray-200"]] : []),'
    );
  });

  it("D-Open appears before D-High/D-Low in the pill array (left-to-right reading order)", () => {
    const openIdx = pageSource.indexOf('["D-Open",');
    const highIdx = pageSource.indexOf('["D-High",');
    expect(openIdx).toBeGreaterThan(-1);
    expect(highIdx).toBeGreaterThan(-1);
    expect(openIdx).toBeLessThan(highIdx);
  });
});

describe("Abbreviated pill labels", () => {
  it("Day-prefixed labels were shortened to D-Open/D-High/D-Low — the 'D-' prefix mirrors '52W' so day-vs-52-week stays explicit", () => {
    expect(pageSource).not.toContain('"Day Open"');
    expect(pageSource).not.toContain('"Day High"');
    expect(pageSource).not.toContain('"Day Low"');
    expect(pageSource).toContain('["D-High", `${currency}${quote.high.toLocaleString()}`');
    expect(pageSource).toContain('["D-Low", `${currency}${quote.low.toLocaleString()}`');
  });

  it("Volume was shortened to Vol", () => {
    expect(pageSource).toContain('["Vol", quote.volume?.toLocaleString() ?? "—", "text-gray-200"]');
  });

  it("52W High/Low and Mkt Cap keep their full existing labels (already compact, and 52W already carries its own distinguishing prefix)", () => {
    expect(pageSource).toContain('"52W High"');
    expect(pageSource).toContain('"52W Low"');
    expect(pageSource).toContain('"Mkt Cap"');
  });
});

describe("Stat pill row scroll affordance", () => {
  it("stays single-line (flex-nowrap) rather than wrapping to a second row", () => {
    expect(pageSource).toContain('className="flex flex-nowrap gap-2 mb-3 overflow-x-auto');
  });

  it("forces a persistently visible styled scrollbar, matching portfolio/page.tsx's holdings-table convention", () => {
    // Native scrollbars are invisible until actively scrolling on macOS/
    // trackpad systems — an overflow-x-auto row with no visible scrollbar
    // looks like a truncation bug, not "scroll for more". Same fix as the
    // portfolio holdings table.
    expect(pageSource).toContain("[&::-webkit-scrollbar]:h-1.5");
    expect(pageSource).toContain("[&::-webkit-scrollbar-thumb]:bg-dark-border");
  });
});
