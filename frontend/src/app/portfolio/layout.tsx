import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Portfolio",
  description: "Track your US and Indian stock holdings with live P&L in the correct currency — ₹ for NSE, $ for NYSE/NASDAQ.",
};

export default function PortfolioLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
