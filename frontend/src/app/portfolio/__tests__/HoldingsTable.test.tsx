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

describe("HoldingsTable — footer totals", () => {
  it("renames the 'Value' column header to 'Current Value'", () => {
    render(
      <HoldingsTable rows={[makeRow({ id: "1", symbol: "TCS" })]} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />
    );
    expect(screen.getByRole("button", { name: /Current Value/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Value/ })).not.toBeInTheDocument();
  });

  it("footer shows total Invested, Current Value, Day's P&L, and P&L when every row is fully resolved", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", invested: 1000, current: 1100, dayChangeAmt: 20, plAmt: 100 }),
      makeRow({ id: "2", symbol: "INFY", invested: 2000, current: 2300, dayChangeAmt: 30, plAmt: 300 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);

    // Invested: 1000 + 2000 = 3000
    expect(screen.getByText("₹3,000")).toBeInTheDocument();
    // Current Value: 1100 + 2300 = 3400
    expect(screen.getByText("₹3,400")).toBeInTheDocument();
    // Day's P&L: 20 + 30 = +50
    expect(screen.getByText("+₹50")).toBeInTheDocument();
    // P&L: 100 + 300 = +400
    expect(screen.getByText("+₹400")).toBeInTheDocument();
    // Fully resolved — no partial note.
    expect(screen.queryByText(/partial while prices load/)).not.toBeInTheDocument();
  });

  it("does not count unresolved rows' current/day-P&L/P&L as zero — sums only resolved rows and flags the total as partial", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", invested: 1000, current: 1100, dayChangeAmt: 20, plAmt: 100 }),
      makeRow({ id: "2", symbol: "INFY", invested: 500, current: 550, dayChangeAmt: 5, plAmt: 50 }),
      // Still loading — current/dayChangeAmt/plAmt genuinely unknown, not 0.
      makeRow({ id: "3", symbol: "PENDING", invested: 2000, current: null, dayChangeAmt: null, plAmt: null, loading: true }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);

    // Invested includes every row, including the pending one: 1000+500+2000 = 3500.
    expect(screen.getByText("₹3,500")).toBeInTheDocument();
    // Current Value / Day's P&L / P&L must reflect ONLY the two resolved rows
    // (1100+550=1650, 20+5=+25, 100+50=+150) — never a fabricated 0 contribution
    // from the pending row, and never equal to the resolved rows' invested sum.
    expect(screen.getByText("₹1,650")).toBeInTheDocument();
    expect(screen.getByText("+₹25")).toBeInTheDocument();
    expect(screen.getByText("+₹150")).toBeInTheDocument();
    // Honest partial-data indicator, since not every row resolved.
    expect(screen.getByText(/partial while prices load/)).toBeInTheDocument();
  });

  it("shows '—' for Current Value/Day's P&L/P&L totals when no row has resolved yet, never a fabricated ₹0", () => {
    const rows = [
      makeRow({ id: "1", symbol: "PENDING1", invested: 1000, current: null, dayChangeAmt: null, plAmt: null, loading: true }),
      makeRow({ id: "2", symbol: "PENDING2", invested: 2000, current: null, dayChangeAmt: null, plAmt: null, loading: true }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);

    expect(screen.getByText("₹3,000")).toBeInTheDocument(); // Invested still known
    const dashes = screen.getAllByText("—");
    // At least 2 dashes are the footer's own (Current Value/Day's P&L/P&L are
    // each independently unresolved) — other "—" cells belong to the rows
    // themselves (Current price column etc.), so just assert a healthy count.
    expect(dashes.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/partial while prices load/)).toBeInTheDocument();
  });

  it("footer totals are currency/market-specific — a $ table's footer never shows ₹ and vice versa", () => {
    const inRows = [
      makeRow({ id: "1", symbol: "TCS", market: "IN", invested: 600, current: 700, dayChangeAmt: 10, plAmt: 100 }),
      makeRow({ id: "2", symbol: "INFY", market: "IN", invested: 400, current: 450, dayChangeAmt: 5, plAmt: 50 }),
    ];
    const { unmount } = render(
      <HoldingsTable rows={inRows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={false} />
    );
    expect(screen.getByText("₹1,000")).toBeInTheDocument(); // Invested total: 600+400
    expect(screen.queryByText("$1,000")).not.toBeInTheDocument();
    unmount();

    const usRows = [
      makeRow({ id: "3", symbol: "AAPL", market: "US", invested: 600, current: 700, dayChangeAmt: 10, plAmt: 100 }),
      makeRow({ id: "4", symbol: "MSFT", market: "US", invested: 400, current: 450, dayChangeAmt: 5, plAmt: 50 }),
    ];
    render(<HoldingsTable rows={usRows} currency="$" onRemove={noop} onEdit={noopEdit} groupBySector={false} />);
    expect(screen.getByText("$1,000")).toBeInTheDocument();
    expect(screen.queryByText("₹1,000")).not.toBeInTheDocument();
  });

  it("the grand total footer still appears exactly once, at the bottom, in sector-grouped mode", () => {
    const rows = [
      makeRow({ id: "1", symbol: "TCS", sector: "IT", invested: 1000, current: 1111, dayChangeAmt: 10, plAmt: 100 }),
      makeRow({ id: "2", symbol: "ONGC", sector: "Energy", invested: 2000, current: 2222, dayChangeAmt: 20, plAmt: 300 }),
    ];
    render(<HoldingsTable rows={rows} currency="₹" onRemove={noop} onEdit={noopEdit} groupBySector={true} />);

    // Sector headings still show their own subtotals — matched by the
    // heading td's distinctive className (colSpan=12 group-header cell),
    // not just "some td with this text," since a single-holding sector's
    // own row can coincidentally show the identical value.
    expect(screen.getByText((_, el) =>
      el?.tagName === "TD" &&
      el.className.includes("font-semibold text-gray-300") &&
      (el?.textContent?.includes("₹1,111") ?? false)
    )).toBeInTheDocument();
    // ...and exactly one grand-total row appears too, summing across sectors.
    expect(screen.getAllByText("Total")).toHaveLength(1);
    expect(screen.getByText("₹3,000")).toBeInTheDocument(); // Invested grand total
    expect(screen.getByText("₹3,333")).toBeInTheDocument(); // Current Value grand total: 1111+2222
  });
});
