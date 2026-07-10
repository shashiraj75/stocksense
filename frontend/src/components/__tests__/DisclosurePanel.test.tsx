import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DisclosurePanel } from "../DisclosurePanel";

describe("DisclosurePanel", () => {
  it("is collapsed by default and expands on click", () => {
    render(
      <DisclosurePanel label="Show more">
        <p>hidden content</p>
      </DisclosurePanel>,
    );
    expect(screen.queryByText("hidden content")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show more" })).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    expect(screen.getByText("hidden content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show more" })).toHaveAttribute("aria-expanded", "true");
  });

  it("respects defaultOpen", () => {
    render(
      <DisclosurePanel label="Details" defaultOpen>
        <p>already visible</p>
      </DisclosurePanel>,
    );
    expect(screen.getByText("already visible")).toBeInTheDocument();
  });

  it("collapses again on a second click", () => {
    render(
      <DisclosurePanel label="Toggle" defaultOpen>
        <p>content</p>
      </DisclosurePanel>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Toggle" }));
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });
});
