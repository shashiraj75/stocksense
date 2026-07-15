import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ValidationIntegrityHold, INTEGRITY_HOLD_ACTIVE } from "../ValidationIntegrityHold";

describe("ValidationIntegrityHold", () => {
  it("renders the required primary and secondary wording exactly", () => {
    render(<ValidationIntegrityHold />);
    expect(
      screen.getByText(
        "Historical validation and live performance statistics are temporarily withheld while market routing, entry-price basis, and benchmark-return calculations are being verified. Current Daily Picks signals are not recalculated or changed by this notice.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The underlying signal cards remain available for research. Accuracy, alpha, hit-rate and benchmark comparisons should not be relied upon until verification is complete.",
      ),
    ).toBeInTheDocument();
  });

  it("does not render any withheld-statistic claim itself", () => {
    render(<ValidationIntegrityHold />);
    for (const label of ["Real data", "Real P&L", "Hit Rate", "Sharpe", "Alpha vs", "Beat benchmark"]) {
      expect(screen.queryByText(new RegExp(label, "i"))).not.toBeInTheDocument();
    }
  });

  it("the hold flag is on (Phase GPI-0 active)", () => {
    expect(INTEGRITY_HOLD_ACTIVE).toBe(true);
  });
});
