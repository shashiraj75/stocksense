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
});
