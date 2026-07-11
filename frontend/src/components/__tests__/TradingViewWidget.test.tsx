import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TradingViewWidget } from "../TradingViewWidget";

// Direct verification (not assumption) found the `NSE:` exchange prefix
// hard-errors on TradingView's public widget embed for Indian symbols,
// and intraday (1m/5m/15m/1h) rendering couldn't be reliably confirmed
// for `BSE:` either in an anonymous embed — so Indian stocks stay on
// D/W/M only, with a small disclosure note, while US/crypto keep the
// full interval list. These tests lock in that split.
describe("TradingViewWidget — interval availability by market", () => {
  it("Indian stocks only offer 1D/1W/1M — no intraday buttons", () => {
    render(<TradingViewWidget symbol="KOTAKBANK" market="IN" />);
    expect(screen.getByText("1D")).toBeInTheDocument();
    expect(screen.getByText("1W")).toBeInTheDocument();
    expect(screen.getByText("1M")).toBeInTheDocument();
    expect(screen.queryByText("1m")).not.toBeInTheDocument();
    expect(screen.queryByText("5m")).not.toBeInTheDocument();
    expect(screen.queryByText("15m")).not.toBeInTheDocument();
    expect(screen.queryByText("1h")).not.toBeInTheDocument();
  });

  it("Indian stocks show the intraday-unavailable disclosure note", () => {
    render(<TradingViewWidget symbol="RELIANCE" market="IN" />);
    expect(screen.getByText(/Intraday chart intervals are unavailable for this Indian symbol/)).toBeInTheDocument();
  });

  it("US stocks offer the full interval list, including intraday", () => {
    render(<TradingViewWidget symbol="AAPL" market="US" />);
    expect(screen.getByText("1m")).toBeInTheDocument();
    expect(screen.getByText("5m")).toBeInTheDocument();
    expect(screen.getByText("15m")).toBeInTheDocument();
    expect(screen.getByText("1h")).toBeInTheDocument();
    expect(screen.getByText("1D")).toBeInTheDocument();
  });

  it("US stocks do not show the Indian-only disclosure note", () => {
    render(<TradingViewWidget symbol="AAPL" market="US" />);
    expect(screen.queryByText(/Intraday chart intervals are unavailable/)).not.toBeInTheDocument();
  });

  it("crypto symbols offer the full interval list, including intraday", () => {
    render(<TradingViewWidget symbol="BTC" market="CRYPTO" />);
    expect(screen.getByText("1m")).toBeInTheDocument();
    expect(screen.getByText("1h")).toBeInTheDocument();
  });

  it("resolves an Indian symbol to its BSE: exchange prefix in the embed iframe, never NSE:", () => {
    render(<TradingViewWidget symbol="KOTAKBANK" market="IN" />);
    const iframe = document.querySelector("iframe")!;
    const src = decodeURIComponent(iframe.getAttribute("src") ?? "");
    expect(src).toContain("symbol=BSE:KOTAKBANK");
    expect(src).not.toContain("symbol=NSE:");
  });
});
