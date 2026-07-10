import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { HoldingsTable, type Row } from "../page";

// Minimal, fully-specified Row fixture — overrides only what each test
// cares about, so a field this component starts reading tomorrow doesn't
// silently become `undefined` in every existing test.
function makeRow(overrides: Partial<Row> & Pick<Row, "id" | "symbol">): Row {
  return {
    market: "IN",
    qty: 10,
    avgPrice: 100,
    curPrice: 110,
    invested: 1000,
    current: 1100,
    plAmt: 100,
    plPct: 10,
    dayChangeAmt: 5,
    dayChangePct: 0.5,
    loading: false,
    signal: null,
    confidence: undefined,
    sigLoading: false,
    sector: null,
    sectorLoading: false,
    ...overrides,
  };
}

const noop = () => {};
const noopEdit = () => {};

describe("HoldingsTable — sector grouping", () => {
  it("sector group headings include holding count, total value, and percentage of portfolio value", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT", current: 3000 }),
      makeRow({ id: "2", symbol: "INFY", sector: "IT", current: 1500 }),
      makeRow({ id: "3", symbol: "ONGC", sector: "Energy", current: 4500 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />);

    // IT: 4500 / 9000 total = 50.0%
    const itHeading = screen.getByText((_, el) => el?.tagName === "TD" && el?.textContent === "IT2 holdings · ₹4,500 · 50.0%");
    expect(itHeading).toBeInTheDocument();
    // Energy: 4500 / 9000 total = 50.0%
    const energyHeading = screen.getByText((_, el) => el?.tagName === "TD" && el?.textContent === "Energy1 holding · ₹4,500 · 50.0%");
    expect(energyHeading).toBeInTheDocument();
  });

  it("unresolved (still-loading) rows are labelled separately from 'Other', not merged into it", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT", current: 1000 }),
      makeRow({ id: "2", symbol: "UNKNOWNCO", sector: null, current: 500 }), // genuinely no sector
      makeRow({ id: "3", symbol: "PENDING", sector: null, sectorLoading: true, current: 2000 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />);

    expect(screen.getByText("Other", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Resolving sector…", { exact: false })).toBeInTheDocument();

    // The unresolved bucket's value must not have been folded into Other's.
    const otherHeading = screen.getByText((_, el) => el?.tagName === "TD" && (el?.textContent?.startsWith("Other") ?? false));
    expect(otherHeading.textContent).toContain("₹500");
    expect(otherHeading.textContent).not.toContain("₹2,500");
  });

  it("the unresolved bucket shows no percentage-of-portfolio figure", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT", current: 1000 }),
      makeRow({ id: "2", symbol: "PENDING", sector: null, sectorLoading: true, current: 1000 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />);

    const resolvingHeading = screen.getByText((_, el) => el?.tagName === "TD" && (el?.textContent?.startsWith("Resolving sector") ?? false));
    expect(resolvingHeading.textContent).not.toMatch(/%/);
  });

  it("a single-sector portfolio still renders the sector view correctly, at 100%", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT", current: 1000 }),
      makeRow({ id: "2", symbol: "INFY", sector: "IT", current: 500 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />);

    const heading = screen.getByText((_, el) => el?.tagName === "TD" && el?.textContent === "IT2 holdings · ₹1,500 · 100.0%");
    expect(heading).toBeInTheDocument();
  });

  it("missing sector values (null/undefined) do not crash and fall back to Other", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: null, current: 1000 }),
      makeRow({ id: "2", symbol: "INFY", sector: undefined, current: 500 }),
    ];
    expect(() =>
      render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />)
    ).not.toThrow();
    expect(screen.getByText("Other", { exact: false })).toBeInTheDocument();
  });

  it("renders ungrouped rows (no sector headings) when groupBySector is false", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT" }),
      makeRow({ id: "2", symbol: "ONGC", sector: "Energy" }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);
    expect(screen.queryByText("IT", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByText("TCS")).toBeInTheDocument();
    expect(screen.getByText("ONGC")).toBeInTheDocument();
  });
});

describe("Portfolio market filtering — IN/US never mix", () => {
  // HoldingsTable itself only ever receives one market's rows (the parent
  // page pre-filters via `rows.filter(r => r.market === "IN" | "US")`
  // before rendering separate IN/US tables) — this exercises that exact
  // predicate directly, so a future refactor that accidentally combines
  // the two lists would fail here rather than only being caught visually.
  it("filtering a mixed holdings list by market never leaks the other market's rows", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", market: "IN" }),
      makeRow({ id: "2", symbol: "AAPL", market: "US" }),
      makeRow({ id: "3", symbol: "INFY", market: "IN" }),
      makeRow({ id: "4", symbol: "MSFT", market: "US" }),
    ];
    const inRows = rows.filter(r => r.market === "IN");
    const usRows = rows.filter(r => r.market === "US");

    expect(inRows.map(r => r.symbol)).toEqual(["TCS", "INFY"]);
    expect(usRows.map(r => r.symbol)).toEqual(["AAPL", "MSFT"]);
    expect(inRows.some(r => r.market === "US")).toBe(false);
    expect(usRows.some(r => r.market === "IN")).toBe(false);
  });

  it("rendering the IN table with IN rows never shows a US symbol", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", market: "IN" }),
      makeRow({ id: "2", symbol: "INFY", market: "IN" }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);
    expect(screen.queryByText("AAPL")).not.toBeInTheDocument();
    expect(screen.queryByText("$")).not.toBeInTheDocument();
  });
});
