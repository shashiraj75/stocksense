import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Screener",
  description: "Screen US and Indian stocks by AI signal, sector, and momentum. Find top movers and BUY candidates fast.",
};

export default function ScreenerLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
