import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Daily Picks",
  description: "AI-selected top BUY signals from Nifty 100 — refreshed every market day. Short, medium, and long-term horizons.",
};

export default function PicksLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
