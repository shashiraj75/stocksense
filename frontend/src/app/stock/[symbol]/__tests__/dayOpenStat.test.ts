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

describe("Open stat pill", () => {
  it("renders only when quote.open is present, matching the existing high/low pattern", () => {
    expect(pageSource).toContain(
      '...(quote.open != null ? [["Open", `${currency}${quote.open.toLocaleString()}`, "text-gray-200"]] : []),'
    );
  });

  it("Open appears before High/Low in the pill array (left-to-right reading order)", () => {
    const openIdx = pageSource.indexOf('["Open",');
    const highIdx = pageSource.indexOf('["High",');
    expect(openIdx).toBeGreaterThan(-1);
    expect(highIdx).toBeGreaterThan(-1);
    expect(openIdx).toBeLessThan(highIdx);
  });
});

describe("Abbreviated pill labels", () => {
  it("Day-prefixed labels were shortened to Open/High/Low — the '52W' prefix on the other pair keeps them unambiguous", () => {
    expect(pageSource).not.toContain('"Day Open"');
    expect(pageSource).not.toContain('"Day High"');
    expect(pageSource).not.toContain('"Day Low"');
    expect(pageSource).toContain('["High", `${currency}${quote.high.toLocaleString()}`');
    expect(pageSource).toContain('["Low", `${currency}${quote.low.toLocaleString()}`');
  });

  it("Volume was shortened to Vol", () => {
    expect(pageSource).toContain('["Vol", quote.volume?.toLocaleString() ?? "—", "text-gray-200"]');
  });

  it("52W High/Low and Mkt Cap keep their full existing labels (already compact, changing them risks ambiguity next to the new High/Low pills)", () => {
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
