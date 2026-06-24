import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Daily Picks",
  description: "AI-selected top BUY signals screened from the full NSE and US universes — refreshed every market day. Short, medium, and long-term horizons.",
};

export default function PicksLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
