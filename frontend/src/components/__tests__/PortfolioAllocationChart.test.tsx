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
});
