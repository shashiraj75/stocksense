import { useState } from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PortfolioAllocationChart } from "../PortfolioAllocationChart";

// The chart is a controlled component (mode/onModeChange lifted to the
// parent page, so the same toggle can also drive the holdings table's
// grouping) — this wrapper supplies the state the parent would own, so
// clicking a toggle button in these tests actually changes what renders.
function ControlledChart(props: Omit<React.ComponentProps<typeof PortfolioAllocationChart>, "mode" | "onModeChange">) {
  const [mode, setMode] = useState<"sector" | "stock" | null>(null);
  return <PortfolioAllocationChart {...props} mode={mode} onModeChange={setMode} />;
}

describe("PortfolioAllocationChart", () => {
  it("renders the By Sector / By Stock toggle once any sector data has resolved", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "AAPL", value: 100, signal: "BUY" }]}
        sectorSlices={[{ sector: "Technology", value: 100 }]}
      />,
    );
    expect(screen.getByRole("button", { name: "By Sector" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "By Stock" })).toBeInTheDocument();
  });

  it("does NOT hide the sector toggle merely because only one sector has resolved so far", () => {
    // Regression case: a stricter ">1 distinct sector" threshold previously
    // hid the toggle entirely while a large portfolio's sector data was
    // still loading (everything briefly sitting in one bucket).
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "AAPL", value: 100, signal: "BUY" },
          { symbol: "MSFT", value: 50, signal: "HOLD" },
        ]}
        sectorSlices={[{ sector: "Other", value: 150 }]}
      />,
    );
    expect(screen.getByRole("button", { name: "By Sector" })).toBeInTheDocument();
  });

  it("falls back to By Stock (no toggle) when no sector data has resolved yet", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "AAPL", value: 100, signal: "BUY" }]}
        sectorSlices={[]}
      />,
    );
    expect(screen.queryByRole("button", { name: "By Sector" })).not.toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });

  // The chart itself doesn't sort — portfolio/page.tsx sorts stockSlices/
  // sectorSlices descending by value before passing them in (confirmed by
  // reading PortfolioAllocationChart.tsx: no .sort() call anywhere in it).
  // These tests verify the chart faithfully preserves and displays
  // caller-provided order rather than silently re-ordering it — descending
  // "sorted-looking" input in, same order out.
  it("renders stock slices in the order given (descending, as the caller pre-sorts)", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "BIG", value: 500, signal: "BUY" },
          { symbol: "MID", value: 100, signal: "HOLD" },
          { symbol: "SMALL", value: 10, signal: "BUY" },
        ]}
        sectorSlices={[]}
      />,
    );
    const legendLabels = screen.getAllByText(/^(SMALL|BIG|MID)$/).map((el) => el.textContent);
    expect(legendLabels).toEqual(["BIG", "MID", "SMALL"]);
  });

  it("renders sector slices in the order given (descending, as the caller pre-sorts) when By Sector is active", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "AAPL", value: 100, signal: "BUY" }]}
        sectorSlices={[
          { sector: "Big Sector", value: 500 },
          { sector: "Mid Sector", value: 100 },
          { sector: "Small Sector", value: 10 },
        ]}
      />,
    );
    const legendLabels = screen
      .getAllByText(/Sector$/)
      .filter((el) => el.tagName !== "BUTTON")
      .map((el) => el.textContent);
    expect(legendLabels).toEqual(["Big Sector", "Mid Sector", "Small Sector"]);
  });

  it("does not crash when sector data is partial/missing sector field", () => {
    expect(() =>
      render(
        <ControlledChart
          stockSlices={[{ symbol: "AAPL", value: 100, signal: null }]}
          sectorSlices={[]}
        />,
      ),
    ).not.toThrow();
  });

  it("renders nothing when there is no stock value at all", () => {
    const { container } = render(<ControlledChart stockSlices={[]} sectorSlices={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("switching to By Stock via the toggle shows stock symbols, not sector names", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "AAPL", value: 100, signal: "BUY" }]}
        sectorSlices={[{ sector: "Technology", value: 100 }]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "By Stock" }));
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.queryByText("Technology")).not.toBeInTheDocument();
  });

  // ── Portfolio sector-allocation hotfix ────────────────────────────────
  // Root cause of the reported "Loading… 100%" bug: an earlier version
  // smuggled a synthetic "still loading" entry into the `sectorSlices`
  // array itself, so this component's own hasSectorData check counted it
  // as real sector data and rendered a single fake 100% slice. The fix
  // keeps `sectorSlices` strictly real (only genuinely-resolved sectors,
  // including "Other") and tracks unresolved holdings via the separate
  // unresolvedSectorValue/Count props instead — these tests lock that
  // contract in.

  it("never renders a single 'Loading…'/placeholder slice as 100% of the bar when stock data exists", () => {
    // Simulates the exact bug scenario: stock values (prices) have
    // resolved, but zero real sectors have resolved yet — sectorSlices is
    // empty, all value is still unresolved.
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "TCS", value: 3000, signal: "BUY" },
          { symbol: "INFY", value: 1500, signal: "HOLD" },
        ]}
        sectorSlices={[]}
        unresolvedSectorValue={4500}
        unresolvedSectorCount={2}
      />,
    );
    // No fake 100% slice — nothing in the DOM claims "Loading" is a
    // complete allocation, and the By Sector toggle isn't offered since
    // there's no real sector data to switch to.
    expect(screen.queryByText(/Loading.*100/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "By Sector" })).not.toBeInTheDocument();
  });

  it("shows By Stock allocation immediately when stock values exist but no sector has resolved", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "TCS", value: 1000, signal: "BUY" }]}
        sectorSlices={[]}
        unresolvedSectorValue={1000}
        unresolvedSectorCount={1}
      />,
    );
    expect(screen.getByText("TCS")).toBeInTheDocument();
  });

  it("shows real sector slices (not a loading placeholder) once sector data exists", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "TCS", value: 1000, signal: "BUY" },
          { symbol: "ONGC", value: 500, signal: "HOLD" },
        ]}
        sectorSlices={[
          { sector: "IT", value: 1000 },
          { sector: "Energy", value: 500 },
        ]}
      />,
    );
    expect(screen.getByText("IT")).toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
  });

  it("shows a distinct 'Resolving sector…' state, separate from real sector allocation, when some holdings are still unresolved", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "TCS", value: 1000, signal: "BUY" },
          { symbol: "PENDING", value: 500, signal: null },
        ]}
        sectorSlices={[{ sector: "IT", value: 1000 }]}
        unresolvedSectorValue={500}
        unresolvedSectorCount={1}
      />,
    );
    // Real sector still shown normally.
    expect(screen.getByText("IT")).toBeInTheDocument();
    // Unresolved state is visible and textually distinct from "Other".
    expect(screen.getByText(/Resolving sector/)).toBeInTheDocument();
    expect(screen.queryByText("Other")).not.toBeInTheDocument();
  });

  it("a single-sector portfolio (100% one sector) still renders the sector view correctly", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "TCS", value: 1000, signal: "BUY" }]}
        sectorSlices={[{ sector: "IT", value: 1000 }]}
      />,
    );
    expect(screen.getByText("IT")).toBeInTheDocument();
    expect(screen.getByText("100.0%")).toBeInTheDocument();
  });

  it("does not show the resolving-sector state in By Stock mode (sector isn't displayed there anyway)", () => {
    render(
      <PortfolioAllocationChart
        stockSlices={[{ symbol: "TCS", value: 1000, signal: "BUY" }]}
        sectorSlices={[{ sector: "IT", value: 500 }]}
        unresolvedSectorValue={500}
        unresolvedSectorCount={1}
        mode="stock"
        onModeChange={() => {}}
      />,
    );
    expect(screen.queryByText(/Resolving sector/)).not.toBeInTheDocument();
  });

  // ── Signal Distribution moved into the Portfolio Allocation header ────
  // Previously its own full-width section below the chart/legend, with a
  // "Signal Distribution" label of its own — now compact chips inline with
  // the "Portfolio Allocation" title and the By Sector/By Stock toggle, in
  // one organized header row instead of a separate vertical block.

  it("renders BUY/HOLD/SELL chips in the same header area as the Portfolio Allocation title", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "TCS", value: 100, signal: "BUY" },
          { symbol: "INFY", value: 100, signal: "HOLD" },
          { symbol: "WIPRO", value: 100, signal: "SELL" },
        ]}
        sectorSlices={[]}
      />,
    );
    const title = screen.getByText("Portfolio Allocation");
    const buyChip = screen.getByText("1 BUY");
    const holdChip = screen.getByText("1 HOLD");
    const sellChip = screen.getByText("1 SELL");
    // All three live in the same header row as the title, not a separate
    // section further down the card (no longer under its own "Signal
    // Distribution" label).
    expect(screen.queryByText("Signal Distribution")).not.toBeInTheDocument();
    expect(title.closest("div")).toBe(buyChip.closest("div")!.parentElement!.parentElement);
    expect(holdChip).toBeInTheDocument();
    expect(sellChip).toBeInTheDocument();
  });

  it("counts are correct and preserve BUY=green/HOLD=amber/SELL=red color classes", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "A", value: 100, signal: "BUY" },
          { symbol: "B", value: 100, signal: "BUY" },
          { symbol: "C", value: 100, signal: "HOLD" },
          { symbol: "D", value: 100, signal: "SELL" },
        ]}
        sectorSlices={[]}
      />,
    );
    const buyChip = screen.getByText("2 BUY");
    const holdChip = screen.getByText("1 HOLD");
    const sellChip = screen.getByText("1 SELL");
    expect(buyChip.className).toContain("text-bull");
    expect(holdChip.className).toContain("text-neutral");
    expect(sellChip.className).toContain("text-bear");
  });

  it("does not count holdings with no signal (null) into BUY/HOLD/SELL, and shows an honest coverage note", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "A", value: 100, signal: "BUY" },
          { symbol: "B", value: 100, signal: null },
          { symbol: "C", value: 100, signal: null },
        ]}
        sectorSlices={[]}
      />,
    );
    expect(screen.getByText("1 BUY")).toBeInTheDocument();
    expect(screen.queryByText(/HOLD/)).not.toBeInTheDocument();
    expect(screen.queryByText(/SELL/)).not.toBeInTheDocument();
    // 1 of 3 holdings has a resolved signal — never silently implied as
    // "0 HOLD" or omitted entirely.
    expect(screen.getByText("Signals available for 1/3")).toBeInTheDocument();
  });

  it("shows no coverage note once every holding has a resolved signal", () => {
    render(
      <ControlledChart
        stockSlices={[
          { symbol: "A", value: 100, signal: "BUY" },
          { symbol: "B", value: 100, signal: "HOLD" },
        ]}
        sectorSlices={[]}
      />,
    );
    expect(screen.queryByText(/Signals available for/)).not.toBeInTheDocument();
  });

  it("renders nothing signal-related when no holding has a resolved signal, without hiding the chart itself", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "A", value: 100, signal: null }]}
        sectorSlices={[]}
      />,
    );
    expect(screen.getByText("Portfolio Allocation")).toBeInTheDocument();
    expect(screen.queryByText(/BUY|HOLD|SELL/)).not.toBeInTheDocument();
  });

  it("the By Sector/By Stock toggle still renders correctly alongside Signal Distribution chips", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "TCS", value: 100, signal: "BUY" }]}
        sectorSlices={[{ sector: "IT", value: 100 }]}
      />,
    );
    expect(screen.getByText("1 BUY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "By Sector" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "By Stock" })).toBeInTheDocument();
  });

  it("header row uses flex-wrap (mobile-safe), not a fixed desktop-only width", () => {
    render(
      <ControlledChart
        stockSlices={[{ symbol: "TCS", value: 100, signal: "BUY" }]}
        sectorSlices={[{ sector: "IT", value: 100 }]}
      />,
    );
    const headerRow = screen.getByText("Portfolio Allocation").closest("div")!;
    expect(headerRow.className).toContain("flex-wrap");
    expect(headerRow.className).not.toMatch(/\bw-\[\d/); // no fixed pixel width
  });
});
