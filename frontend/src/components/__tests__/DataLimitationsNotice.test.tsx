import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataLimitationsNotice, DataLimitationsMark } from "../DataLimitationsNotice";

// DP-026 user-facing disclosure (product-owner authorized). These tests
// prove the two variants actually satisfy the accessibility and non-
// tooltip-only requirements, not just that some text string exists.

describe("DataLimitationsNotice (full banner)", () => {
  it("renders without any data/loading/error props — it is unconditional, not data-dependent", () => {
    // No props are threaded from an API response on purpose: this must
    // remain visible for legacy persisted validation results that predate
    // the data_limitations JSON field, since the underlying limitation is
    // identical for old and new runs alike.
    render(<DataLimitationsNotice />);
    expect(screen.getByText(/Historical validation limitation/i)).toBeInTheDocument();
  });

  it("states the core fact: a current snapshot was reused across historical signals", () => {
    render(<DataLimitationsNotice />);
    const text = screen.getByText(/Historical validation limitation/i).closest("p")!.textContent!;
    expect(text).toMatch(/not.*reconstructed as they were known on each past date/i);
    expect(text).toMatch(/current fundamental snapshot was reused across historical signals/i);
    expect(text).toMatch(/not a fully point-in-time replay/i);
  });

  it("includes the directional-evidence guidance sentence", () => {
    render(<DataLimitationsNotice />);
    expect(screen.getByText(/directional evidence, not as a precise reconstruction/i)).toBeInTheDocument();
  });

  it("does not imply production Daily Picks are defective", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!;
    expect(text.toLowerCase()).not.toMatch(/daily picks? (is|are) (defective|broken|wrong|unreliable)/);
    expect(text.toLowerCase()).not.toContain("production is broken");
  });

  it("does not claim every fundamental retrieval failed", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!.toLowerCase();
    expect(text).not.toContain("all fundamental");
    expect(text).not.toContain("every fetch failed");
    expect(text).not.toContain("100% unavailable");
  });

  it("does not claim historical results are entirely invalid", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!.toLowerCase();
    expect(text).not.toContain("entirely invalid");
    expect(text).not.toContain("completely wrong");
    expect(text).not.toContain("do not use");
  });

  it("does not claim no external provider can supply point-in-time data", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!.toLowerCase();
    expect(text).not.toContain("no provider");
    expect(text).not.toContain("no data source in the world");
    expect(text).not.toContain("impossible to obtain");
  });

  it("does not claim the methodology was repaired or fixed", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!.toLowerCase();
    expect(text).not.toContain("fixed");
    expect(text).not.toContain("resolved");
    expect(text).not.toContain("corrected");
  });

  it("does not promise accuracy, returns, or profitability", () => {
    render(<DataLimitationsNotice />);
    const text = document.body.textContent!.toLowerCase();
    expect(text).not.toMatch(/guarantee/);
    expect(text).not.toMatch(/will (earn|return|profit)/);
  });

  it("is not tooltip-only — the disclosure text is present in the rendered DOM without any hover/focus interaction", () => {
    render(<DataLimitationsNotice />);
    // getByText succeeding with no user-event interaction at all proves the
    // text is statically in the accessibility tree, not hidden behind a
    // title attribute or a hover-only affordance.
    const node = screen.getByText(/Historical validation limitation/i);
    expect(node.closest("[title]")).toBeNull();
  });

  it("accepts a className for layout composition without losing its content", () => {
    render(<DataLimitationsNotice className="mb-3" />);
    expect(screen.getByText(/Historical validation limitation/i)).toBeInTheDocument();
  });

  it("decorative icon is hidden from assistive tech (aria-hidden) so the text alone carries meaning", () => {
    const { container } = render(<DataLimitationsNotice />);
    const icon = container.querySelector("svg");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });
});

describe("DataLimitationsMark (compact inline marker)", () => {
  it("renders as a real focusable link, not a bare span/tooltip trigger", () => {
    render(<DataLimitationsMark />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/validation");
  });

  it("has a visible accessible name carrying the full disclosure, not just a title attribute", () => {
    render(<DataLimitationsMark />);
    const link = screen.getByRole("link", {
      name: /current fundamentals snapshot was reused across historical signals/i,
    });
    expect(link).toBeInTheDocument();
  });

  it("is reachable by click/keyboard, not hover-only — satisfies 'no tooltip-only disclosure'", () => {
    render(<DataLimitationsMark />);
    const link = screen.getByRole("link");
    // A real <a href> is in the tab order and activatable without hover;
    // this is the structural proof, not a simulated hover check.
    expect(link.tagName.toLowerCase()).toBe("a");
    expect(link).not.toHaveAttribute("tabindex", "-1");
  });

  it("decorative icon is aria-hidden", () => {
    const { container } = render(<DataLimitationsMark />);
    const icon = container.querySelector("svg");
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });
});
