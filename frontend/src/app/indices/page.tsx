import type { Metadata } from "next";
import { LayoutGrid } from "lucide-react";
import { IndicesDashboard } from "@/components/IndicesDashboard";

export const metadata: Metadata = {
  title: "Market Indices",
  description: "Live index levels across NSE India, NYSE/NASDAQ, Crypto, and Commodities.",
};

export default function IndicesPage() {
  return (
    <div className="space-y-5">
      {/* Header — matches the title/subtitle row style used on
          Dashboard/Heatmap (icon + h1 + inline subtitle, wraps on mobile). */}
      <div className="flex items-center gap-3 flex-wrap">
        <LayoutGrid size={22} className="text-brand-500 shrink-0" />
        <div className="flex items-baseline gap-2 flex-wrap">
          <h1 className="text-2xl font-bold text-white">Market Indices</h1>
          <p className="text-sm text-gray-400">· Live tracking across every covered market</p>
        </div>
      </div>

      <IndicesDashboard />
    </div>
  );
}
