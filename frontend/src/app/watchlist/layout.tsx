import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Watchlist",
  description: "Monitor your favourite US and Indian stocks with live prices and quick access to AI prediction signals.",
};

export default function WatchlistLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
