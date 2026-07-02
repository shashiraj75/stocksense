import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Daily Picks",
  description: "AI-selected top BUY signals screened daily from quality-filtered market universes — coverage varies by market. Short, medium, and long-term horizons.",
};

export default function PicksLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
