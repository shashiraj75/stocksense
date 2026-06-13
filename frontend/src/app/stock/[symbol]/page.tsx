"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "next/navigation";
import { fetchQuote, fetchPrediction, fetchNews, Market, Horizon } from "@/utils/api";
import { TradingViewWidget } from "@/components/TradingViewWidget";
import { SignalBadge } from "@/components/SignalBadge";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";
import { NewsCard } from "@/components/NewsCard";
import clsx from "clsx";
import { ArrowUpRight, ArrowDownRight } from "lucide-react";

export default function StockPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const searchParams = useSearchParams();
  const market = (searchParams.get("market") as Market) || "US";
  const currency = market === "US" ? "$" : "₹";

  const [horizon, setHorizon] = useState<Horizon>("short");

  const { data: quote } = useQuery({
    queryKey: ["quote", symbol, market],
    queryFn: () => fetchQuote(symbol, market),
  });

  const { data: prediction, isLoading: predLoading } = useQuery({
    queryKey: ["prediction", symbol, market, horizon],
    queryFn: () => fetchPrediction(symbol, market, horizon),
  });

  const { data: news } = useQuery({
    queryKey: ["news", symbol, market],
    queryFn: () => fetchNews(symbol, market),
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold font-mono">{symbol}</h1>
            <span className="text-xs bg-dark-card border border-dark-border px-2 py-0.5 rounded text-gray-400">
              {market === "US" ? "🇺🇸 NYSE / NASDAQ" : "🇮🇳 NSE India"}
            </span>
          </div>
          {quote && (
            <div className="flex items-center gap-3 mt-2">
              <span className="text-4xl font-bold">
                {currency}{quote.price.toLocaleString()}
              </span>
              <span className={clsx("flex items-center gap-1 text-lg font-semibold",
                quote.change >= 0 ? "text-bull" : "text-bear")}>
                {quote.change >= 0 ? <ArrowUpRight /> : <ArrowDownRight />}
                {quote.change >= 0 ? "+" : ""}{quote.change} ({quote.change_pct}%)
              </span>
            </div>
          )}
        </div>
        {prediction && !predLoading && (
          <SignalBadge signal={prediction.signal} size="lg" />
        )}
      </div>

      {/* Horizon Tabs */}
      <div className="flex gap-2">
        {(["short", "medium", "long"] as Horizon[]).map((h) => (
          <button
            key={h}
            onClick={() => setHorizon(h)}
            className={clsx(
              "px-4 py-2 rounded-lg text-sm font-medium capitalize transition-colors",
              horizon === h
                ? "bg-brand-500 text-white"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            )}
          >
            {h} Term
          </button>
        ))}
      </div>

      {/* TradingView Chart — free embed */}
      <div className="rounded-2xl overflow-hidden border border-dark-border">
        <TradingViewWidget symbol={symbol} market={market} height={480} />
      </div>

      {/* Prediction + Stats */}
      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-5">
          <h2 className="font-bold text-lg">AI Prediction — {horizon} term</h2>
          {predLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-5 bg-dark-border rounded animate-pulse" />
              ))}
            </div>
          ) : prediction ? (
            <>
              <div className="flex items-center justify-between">
                <span className="text-gray-400 text-sm">Signal</span>
                <SignalBadge signal={prediction.signal} />
              </div>
              <ConfidenceMeter value={prediction.confidence} label="Confidence" />
              <div className="flex items-center justify-between">
                <span className="text-gray-400 text-sm">Target Price</span>
                <span className="font-mono font-bold">
                  {currency}{prediction.target_price.toLocaleString()}
                </span>
              </div>
              <div>
                <p className="text-gray-400 text-sm mb-2">Key Reasons</p>
                <ul className="space-y-1.5">
                  {prediction.reasoning.slice(0, 4).map((r, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className={clsx(
                        "shrink-0 mt-1 w-2 h-2 rounded-full",
                        r.signal === "BUY" || r.signal === "BULLISH" ? "bg-bull" :
                        r.signal === "SELL" || r.signal === "BEARISH" ? "bg-bear" : "bg-neutral"
                      )} />
                      <span className="text-gray-300">{r.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </>
          ) : null}
        </div>

        <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-4">
          <h2 className="font-bold text-lg">Key Stats</h2>
          {quote && (
            <dl className="space-y-3">
              {[
                ["52W High", `${currency}${quote.fifty_two_week_high?.toLocaleString()}`],
                ["52W Low", `${currency}${quote.fifty_two_week_low?.toLocaleString()}`],
                ["Market Cap", quote.market_cap ? `${currency}${(quote.market_cap / 1e9).toFixed(2)}B` : "—"],
                ["Avg Volume", quote.volume?.toLocaleString() ?? "—"],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between text-sm">
                  <dt className="text-gray-400">{label}</dt>
                  <dd className="font-mono font-bold">{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {prediction && (
            <div className="border-t border-dark-border pt-4 space-y-2">
              <p className="text-gray-400 text-sm mb-2">Score Breakdown</p>
              <ConfidenceMeter value={prediction.fundamental_score.score} label="Fundamental Score" />
              <ConfidenceMeter value={prediction.sentiment_score.score} label="News Sentiment Score" />
              <ConfidenceMeter
                value={prediction.technical?.rsi ? Math.min(100, Math.round(prediction.technical.rsi)) : 50}
                label="RSI"
              />
            </div>
          )}
        </div>
      </div>

      {/* News */}
      <section>
        <h2 className="text-lg font-semibold mb-3">News & Sentiment</h2>
        <div className="grid md:grid-cols-2 gap-3">
          {news?.articles.slice(0, 8).map((a, i) => (
            <NewsCard key={i} article={a} />
          ))}
          {!news?.articles.length && (
            <p className="text-gray-500 text-sm col-span-2">Loading news from free RSS feeds…</p>
          )}
        </div>
      </section>
    </div>
  );
}
