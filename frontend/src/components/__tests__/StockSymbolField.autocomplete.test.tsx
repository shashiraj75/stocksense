import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { StockSymbolField } from "@/components/StockSymbolField";

// 2026-09-16 user report (screenshot from Portfolio's Add Holding field):
// the browser's own native autofill dropdown (previously-typed symbols —
// IFBIND, IFCI, WIPRO, ONGC) rendered on top of the page's sticky header,
// cutting off content underneath. This field already ships its own
// matching autocomplete dropdown (the <ul> below the input); the browser's
// competing one — which ignores our z-index/styling entirely — must be
// turned off, not merely coexist with it.

// useStockSearch's loadUniverse() fetches "/stock_universe.json" in a
// useEffect on mount — irrelevant to this test (which only checks the
// input's own autoComplete attribute), but left unmocked it rejects in
// jsdom (no relative-URL fetch support) and pollutes test output.
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ US: [], IN: [], CRYPTO: [] }) } as Response)));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StockSymbolField", () => {
  it("disables the browser's own native autofill so only the app's own dropdown can render", () => {
    render(
      <StockSymbolField
        value=""
        onChange={() => {}}
        onSelect={() => {}}
      />
    );
    expect(screen.getByPlaceholderText("AAPL, RELIANCE, BTC")).toHaveAttribute("autoComplete", "off");
  });
});
