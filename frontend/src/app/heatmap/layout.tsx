import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Heatmap",
  description: "Visual heatmap of Nifty 100 stocks by sector, showing AI signal strength and price momentum at a glance.",
};

export default function HeatmapLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
