"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { TrendingUp, Clock, AlertCircle } from "lucide-react";

type Pick = {
  symbol: string;
  name: string;
  price: number;
  target: number;
  confidence: number;
  reasoning: string[];
  horizon: string;
};

type DailyPicksResponse = {
  generated_at: string | null;
  picks: { short: Pick[]; medium: Pick[]; long: Pick[] };
  message?: string;
};

const HORIZONS = [
  { key: "short",  label: "Short Term",  sub: "1–5 days"   },
  { key: "medium", label: "Medium Term", sub: "2–4 weeks"  },
  { key: "long",   label: "Long Term",   sub: "3–6 months" },
] as const;

function PickCard({ pick }: { pick: Pick }) {
  const router = useRouter();
  const upside = pick.price && pick.target
    ? (((pick.target - pick.price) / pick.price) * 100).toFixed(1)
    : null;

  return (
    <div
      onClick={() => router.push(`/stock/${pick.symbol}?market=IN`)}
      className="bg-dark-card border border-dark-border rounded-xl p-4 cursor-pointer hover:border-green-500/50 transition-all hover:shadow-lg hover:shadow-green-500/5 group"
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-white text-lg group-hover:text-green-400 transition-colors">
              {pick.symbol}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 font-semibold border border-green-500/30">
              BUY
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5 truncate max-w-[180px]">{pick.name}</p>
        </div>
        <div className="text-right">
          <div className="text-sm font-semibold text-white">
            ₹{pick.price?.toLocaleString("en-IN")}
          </div>
          {upside && (
            <div className="text-xs text-green-400 font-medium">+{upside}% upside</div>
          )}
        </div>
      </div>

      {/* Confidence bar */}
      <div className="mb-3">
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>AI Confidence</span>
          <span className="text-white font-medium">{pick.confidence}%</span>
        </div>
        <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full"
            style={{ width: `${pick.confidence}%` }}
          />
        </div>
      </div>

      {/* Target */}
      {pick.target && (
        <div className="flex items-center justify-between text-xs mb-3">
          <span className="text-gray-500">Target Price</span>
          <span className="text-green-400 font-semibold">₹{pick.target?.toLocaleString("en-IN")}</span>
        </div>
      )}

      {/* Top reasoning */}
      {pick.reasoning?.length > 0 && (
        <div className="space-y-1">
          {pick.reasoning.map((r, i) => (
            <p key={i} className="text-xs text-gray-400 flex items-start gap-1.5">
              <span className="text-green-500 mt-0.5 flex-shrink-0">•</span>
              {r}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DailyPicksPage() {
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("short");

  const { data, isLoading } = useQuery<DailyPicksResponse>({
    queryKey: ["daily-picks"],
    queryFn: () => api.get("/api/picks/daily").then(r => r.data),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  const picks = data?.picks?.[horizon] ?? [];
  const generatedAt = data?.generated_at
    ? new Date(data.generated_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: true,
      })
    : null;

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <TrendingUp size={24} className="text-green-400" />
            <h1 className="text-2xl font-bold">Daily Stock Picks</h1>
            <span className="text-xs bg-green-500/15 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full font-semibold">
              🇮🇳 NSE India
            </span>
          </div>
          <p className="text-sm text-gray-400">
            Top 5 AI-selected BUY calls from Nifty 100 — refreshed every market day at 9 AM IST
          </p>
        </div>
        {generatedAt && (
          <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-dark-card border border-dark-border rounded-lg px-3 py-2 flex-shrink-0">
            <Clock size={12} />
            <span>Updated {generatedAt}</span>
          </div>
        )}
      </div>

      {/* Horizon tabs */}
      <div className="flex gap-2">
        {HORIZONS.map(({ key, label, sub }) => (
          <button
            key={key}
            onClick={() => setHorizon(key)}
            className={`px-4 py-2.5 rounded-xl text-sm font-medium transition-all ${
              horizon === key
                ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            }`}
          >
            {label}
            <span className={`ml-1.5 text-xs ${horizon === key ? "text-blue-200" : "text-gray-600"}`}>
              ({sub})
            </span>
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-48" />
          ))}
        </div>
      ) : picks.length > 0 ? (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {picks.map((pick) => (
            <PickCard key={pick.symbol} pick={pick} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <AlertCircle size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">
            {data?.generated_at ? "No BUY signals found today" : "Picks not yet generated"}
          </h3>
          <p className="text-sm text-gray-500 max-w-sm">
            {data?.generated_at
              ? "The AI didn't find strong BUY signals in Nifty 100 today. Market conditions may be weak — check back tomorrow."
              : "Daily picks are generated at 9 AM IST on market days. Check back after the market opens."}
          </p>
        </div>
      )}

      <p className="text-xs text-gray-600 text-center">
        These are AI-generated signals for educational purposes only — not financial advice. Always do your own research.
      </p>
    </div>
  );
}
