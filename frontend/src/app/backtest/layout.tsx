import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Backtest",
  description: "Test StockSense360 prediction accuracy against historical data for any US or Indian stock across short, medium, and long-term horizons.",
};

export default function BacktestLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
