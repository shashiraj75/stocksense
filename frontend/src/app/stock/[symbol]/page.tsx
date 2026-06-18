"use client";
import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearchParams } from "next/navigation";
import { api, fetchQuote, fetchPrediction, fetchNews, fetchFactorAttribution, fetchScoreHistory, Market, Horizon } from "@/utils/api";
import { TradingViewWidget } from "@/components/TradingViewWidget";
import { SignalBadge } from "@/components/SignalBadge";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";
import { NewsCard } from "@/components/NewsCard";
import { FactorAttributionWaterfall } from "@/components/FactorAttributionWaterfall";
import { ConfidenceBreakdown } from "@/components/ConfidenceBreakdown";
import { BullBearCase } from "@/components/BullBearCase";
import { ScoreHistoryChart } from "@/components/ScoreHistoryChart";
import clsx from "clsx";
import { ArrowUpRight, ArrowDownRight, FlaskConical, CheckCircle, XCircle, Loader2, Beaker } from "lucide-react";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { TradeLevelVisualizer } from "@/components/TradeLevelVisualizer";
import { getMarketStatus } from "@/utils/marketHours";

type Tab = Horizon | "backtest" | "history";

const HORIZON_TABS: { key: Tab; label: string }[] = [
  { key: "short", label: "Short Term" },
  { key: "medium", label: "Medium Term" },
  { key: "long", label: "Long Term" },
  { key: "backtest", label: "Backtest" },
  { key: "history", label: "History" },
];

const HORIZON_LABEL: Record<string, string> = {
  short: "7 trading days",
  medium: "3 months",
  long: "12 months",
};

interface BacktestResult {
  symbol: string; market: Market; horizon: Horizon;
  total_tests: number; correct_predictions: number; accuracy_pct: number;
  buy_signals_tested: number; sell_signals_tested: number; hold_signals_tested: number;
  avg_return_on_buy_pct: number; avg_return_on_sell_pct: number;
  profitable_buy_calls: number; profitable_sell_calls: number;
  forward_window_days: number;
  results: {
    date: string; entry_price: number; exit_price: number;
    actual_return_pct: number; predicted_signal: string;
    actual_signal: string; correct: boolean;
  }[];
}

function getCapCategory(marketCap: number | null | undefined, market: string) {
  if (!marketCap || marketCap <= 0) return null;
  if (market === "IN") {
    const cr = marketCap / 1e7; // raw INR → crores
    if (cr < 100)    return { label: "Penny",      color: "text-gray-400",   bg: "bg-gray-500/10 border-gray-500/30" };
    if (cr < 5000)   return { label: "Small Cap",  color: "text-blue-400",   bg: "bg-blue-500/10 border-blue-500/30" };
    if (cr < 20000)  return { label: "Mid Cap",    color: "text-yellow-400", bg: "bg-yellow-500/10 border-yellow-500/30" };
    return             { label: "Large Cap",  color: "text-green-400",  bg: "bg-green-500/10 border-green-500/30" };
  }
  // USD
  if (marketCap < 300e6)  return { label: "Penny",      color: "text-gray-400",   bg: "bg-gray-500/10 border-gray-500/30" };
  if (marketCap < 2e9)    return { label: "Small Cap",  color: "text-blue-400",   bg: "bg-blue-500/10 border-blue-500/30" };
  if (marketCap < 10e9)   return { label: "Mid Cap",    color: "text-yellow-400", bg: "bg-yellow-500/10 border-yellow-500/30" };
  if (marketCap < 200e9)  return { label: "Large Cap",  color: "text-green-400",  bg: "bg-green-500/10 border-green-500/30" };
  return                    { label: "Mega Cap",   color: "text-purple-400", bg: "bg-purple-500/10 border-purple-500/30" };
}

const CRYPTO_NAMES: Record<string, string> = {
  BTC: "Bitcoin", ETH: "Ethereum", BNB: "BNB", SOL: "Solana",
  XRP: "XRP", DOGE: "Dogecoin", ADA: "Cardano", AVAX: "Avalanche",
  LINK: "Chainlink", DOT: "Polkadot", MATIC: "Polygon", UNI: "Uniswap",
  LTC: "Litecoin", ATOM: "Cosmos",
};

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params?.symbol ?? "";
  const searchParams = useSearchParams();
  const rawMarket = searchParams?.get("market") || "US";
  const isCrypto = rawMarket === "CRYPTO";
  const market = isCrypto ? "US" : (rawMarket as Market);
  const currency = market === "IN" ? "₹" : "$";

  const [tab, setTab] = useState<Tab>("short");
  const [btHorizon, setBtHorizon] = useState<Horizon>("short");
  const [historyHorizon, setHistoryHorizon] = useState<Horizon>("medium");
  const [btRunning, setBtRunning] = useState(false);
  const [btData, setBtData] = useState<BacktestResult | null>(null);
  const [btError, setBtError] = useState("");
  const [isComputing, setIsComputing] = useState(false);
  const [computeSeconds, setComputeSeconds] = useState(0);
  const [showPaperModal, setShowPaperModal] = useState(false);

  const horizon = tab === "backtest" ? "short" : tab === "history" ? "medium" : (tab as Horizon);

  const { data: quote, dataUpdatedAt: quoteUpdatedAt } = useQuery({
    queryKey: ["quote", symbol, market],
    queryFn: () => fetchQuote(symbol, market),
    enabled: !isCrypto,
    refetchInterval: 30_000,   // matches backend quote cache TTL (30s)
    staleTime: 25_000,
    refetchOnWindowFocus: false,
  });

  // For crypto, fetch price via screener/crypto-movers (already returns live prices)
  const { data: cryptoMovers, dataUpdatedAt: cryptoUpdatedAt } = useQuery({
    queryKey: ["crypto-movers"],
    queryFn: () => api.get<{ movers: { symbol: string; name: string; price: number | null; change_pct: number }[] }>(
      "/api/screener/crypto-movers"
    ).then(r => r.data),
    enabled: isCrypto,
    refetchInterval: 60_000,   // matches backend 60s crypto cache
    staleTime: 55_000,
    refetchOnWindowFocus: false,
  });
  const cryptoQuote = isCrypto
    ? cryptoMovers?.movers.find(m => m.symbol === symbol) ?? null
    : null;

  const { data: prediction, isLoading: predLoading, isFetching: predFetching, refetch: refetchPrediction, isError: predError } = useQuery({
    queryKey: ["prediction", symbol, isCrypto ? "CRYPTO" : market, horizon],
    queryFn: () => fetchPrediction(symbol, isCrypto ? "CRYPTO" as any : market, horizon, () => {
      setIsComputing(true);
      setComputeSeconds(0);
    }),
    enabled: tab !== "backtest",
    retry: 2,           // fetchPrediction already polls for 120s; only retry on hard errors
    retryDelay: 3000,
    placeholderData: (prev) => prev,
    staleTime: 14 * 60_000,
    refetchOnWindowFocus: false,
  });

  // Elapsed timer while background prediction is running
  useEffect(() => {
    if (!isComputing || !predLoading) {
      setIsComputing(false);
      setComputeSeconds(0);
      return;
    }
    const id = setInterval(() => setComputeSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [isComputing, predLoading]);

  const [marketStatus, setMarketStatus] = useState(() => getMarketStatus(isCrypto ? "CRYPTO" : market));
  useEffect(() => {
    const update = () => setMarketStatus(getMarketStatus(isCrypto ? "CRYPTO" : market));
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, [market, isCrypto]);

  // Prefetch the other two horizons in parallel as soon as the page loads,
  // instead of fetching one-at-a-time as the user clicks tabs — each
  // prediction call takes 5-15s, so sequential per-click fetching made
  // switching horizons feel stuck/stale (placeholderData kept showing the
  // previous tab's numbers while the new one was still in flight).
  const queryClient = useQueryClient();
  useEffect(() => {
    if (isCrypto) return;
    const allHorizons: Horizon[] = ["short", "medium", "long"];
    for (const h of allHorizons) {
      if (h === horizon) continue;
      queryClient.prefetchQuery({
        queryKey: ["prediction", symbol, market, h],
        queryFn: () => fetchPrediction(symbol, market, h),  // no onComputing — prefetch is silent
        staleTime: 14 * 60_000,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, market, isCrypto]);

  const { data: news } = useQuery({
    queryKey: ["news", symbol, isCrypto ? "US" : market],
    queryFn: () => fetchNews(symbol, isCrypto ? "US" : market),
  });

  const { data: attribution } = useQuery({
    queryKey: ["factor-attribution", symbol, market, horizon],
    queryFn: () => fetchFactorAttribution(symbol, market, horizon),
    enabled: tab !== "backtest" && tab !== "history" && !isCrypto && !!prediction?.signal,
    staleTime: 14 * 60_000,
    refetchOnWindowFocus: false,
  });

  const { data: scoreHistory } = useQuery({
    queryKey: ["score-history", symbol, historyHorizon],
    queryFn: () => fetchScoreHistory(symbol, historyHorizon, 90),
    enabled: tab === "history" && !isCrypto,
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
  });

  const runBacktest = async () => {
    setBtRunning(true); setBtError(""); setBtData(null);
    try {
      const res = await api.get<BacktestResult>(`/api/backtest/${symbol}`, {
        params: { market: isCrypto ? "CRYPTO" : market, horizon: btHorizon },
      });
      setBtData(res.data);
    } catch {
      setBtError("Backtest failed. Render may still be deploying — try again in a moment.");
    } finally {
      setBtRunning(false);
    }
  };

  // Show "not found" only if BOTH quote and prediction failed with no data
  const notFound = !isCrypto && !predLoading && !quote && predError;

  if (notFound) {
    return (
      <div className="flex flex-col items-center justify-center py-32 text-center space-y-4">
        <div className="text-5xl">🔍</div>
        <h1 className="text-2xl font-bold text-white">{symbol} not found</h1>
        <p className="text-gray-400 text-sm max-w-sm">
          This symbol may be delisted, invalid, or not supported by our data provider.
          Try searching for a different stock.
        </p>
        <a href="/" className="mt-4 px-5 py-2 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors">
          Back to Dashboard
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {!isCrypto && <MarketDisclaimer market={market} />}

      {/* ── Hero Header Card ── */}
      <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          {/* Left: symbol + price block */}
          <div className="min-w-0">
            {/* Symbol row */}
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <h1 className="text-xl font-bold font-mono tracking-wide">{symbol}</h1>
              <span className="text-xs bg-dark-bg border border-dark-border px-2 py-0.5 rounded text-gray-400">
                {isCrypto ? `CRYPTO · ${CRYPTO_NAMES[symbol] ?? symbol}` : market === "US" ? "🇺🇸 NYSE / NASDAQ" : "🇮🇳 NSE India"}
              </span>
              {!isCrypto && (() => {
                const cap = getCapCategory(quote?.market_cap, market);
                if (!cap) return null;
                return (
                  <span className={`text-xs border px-2 py-0.5 rounded font-medium ${cap.color} ${cap.bg}`}>
                    {cap.label}
                  </span>
                );
              })()}
            </div>
            {/* Price + change */}
            <div className="flex flex-wrap items-baseline gap-3 mb-3">
              <span className="text-3xl font-black font-mono tracking-tight">
                {isCrypto
                  ? `$${(cryptoQuote?.price ?? prediction?.current_price ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                  : quote ? `${currency}${quote.price.toLocaleString()}` : <span className="text-gray-600">—</span>}
              </span>
              {isCrypto && cryptoQuote?.change_pct != null && (
                <span className={clsx("flex items-center gap-1 text-sm font-semibold",
                  cryptoQuote.change_pct >= 0 ? "text-bull" : "text-bear")}>
                  {cryptoQuote.change_pct >= 0 ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
                  {cryptoQuote.change_pct >= 0 ? "+" : ""}{cryptoQuote.change_pct}%
                </span>
              )}
              {!isCrypto && quote?.change != null && (
                <span className={clsx("flex items-center gap-1 text-sm font-semibold",
                  quote.change >= 0 ? "text-bull" : "text-bear")}>
                  {quote.change >= 0 ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
                  {quote.change >= 0 ? "+" : ""}{quote.change} ({quote.change_pct}%)
                </span>
              )}
              {/* Market status pill */}
              <div className="flex items-center gap-1.5 text-xs text-gray-500">
                <span className="relative flex h-2 w-2">
                  {marketStatus.isOpen && (
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  )}
                  <span className={clsx("relative inline-flex rounded-full h-2 w-2", marketStatus.isOpen ? "bg-green-500" : "bg-red-500")}></span>
                </span>
                {marketStatus.isOpen ? "Live" : marketStatus.label}
                {marketStatus.nextEventLabel && (
                  <span className="text-gray-600">· {marketStatus.nextEventLabel}</span>
                )}
              </div>
            </div>
            {/* Quick stats bar */}
            {!isCrypto && quote && (
              <div className="flex flex-wrap gap-x-5 gap-y-1 mb-3">
                {[
                  ["52W High", `${currency}${quote.fifty_two_week_high?.toLocaleString()}`],
                  ["52W Low",  `${currency}${quote.fifty_two_week_low?.toLocaleString()}`],
                  ["Mkt Cap",  (() => {
                    const v = quote.market_cap;
                    if (!v) return "—";
                    if (v >= 1e12) return `${currency}${(v/1e12).toFixed(2)}T`;
                    if (v >= 1e9)  return `${currency}${(v/1e9).toFixed(2)}B`;
                    return `${currency}${(v/1e6).toFixed(0)}M`;
                  })()],
                  ["Volume", quote.volume?.toLocaleString() ?? "—"],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-baseline gap-1.5">
                    <span className="text-xs text-gray-500">{label}</span>
                    <span className="text-xs font-mono font-semibold text-gray-200">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {/* Inline score pills — shown once prediction loads */}
            {prediction && !predLoading && !isCrypto && (() => {
              const items: { label: string; value: number; color: string }[] = [];
              items.push({
                label: "Fundamentals",
                value: prediction.fundamental_score.score,
                color: prediction.fundamental_score.score >= 65 ? "text-bull" : prediction.fundamental_score.score >= 45 ? "text-yellow-400" : "text-bear",
              });
              items.push({
                label: "Sentiment",
                value: prediction.sentiment_score.score,
                color: prediction.sentiment_score.score >= 60 ? "text-bull" : prediction.sentiment_score.score >= 40 ? "text-yellow-400" : "text-bear",
              });
              const rsi = prediction.technical?.rsi;
              if (rsi) {
                const r = Math.round(rsi);
                const rsiLabel = r >= 70 ? "Overbought" : r <= 30 ? "Oversold" : r >= 55 ? "Bullish" : r <= 45 ? "Bearish" : "Neutral";
                const rsiColor = r >= 70 ? "text-yellow-400" : r <= 30 ? "text-yellow-400" : r >= 55 ? "text-bull" : r <= 45 ? "text-bear" : "text-gray-400";
                items.push({ label: `RSI ${r} · ${rsiLabel}`, value: r, color: rsiColor });
              }
              return (
                <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                  {items.map(({ label, value, color }) => (
                    <div key={label} className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">{label}</span>
                      <div className="flex items-center gap-1.5">
                        <div className="w-16 h-1.5 bg-dark-border rounded-full overflow-hidden">
                          <div
                            className={clsx("h-full rounded-full transition-all", color === "text-bull" ? "bg-bull" : color === "text-bear" ? "bg-bear" : "bg-yellow-400")}
                            style={{ width: `${Math.min(100, value)}%` }}
                          />
                        </div>
                        <span className={clsx("text-xs font-mono font-semibold tabular-nums", color)}>{value}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}
            {isCrypto && (
              <p className="text-xs text-gray-500">Chart: TradingView · Binance USDT · Predictions: technicals + volume + sentiment</p>
            )}
          </div>

          {/* Right: Signal + Paper Trade */}
          <div className="flex flex-col items-end gap-2.5 shrink-0">
            {tab !== "backtest" && prediction && !predLoading && (
              <SignalBadge signal={prediction.signal} confidence={prediction.confidence} size="lg" />
            )}
            {prediction?.signal && !predLoading && !isCrypto && (() => {
              const sig = prediction.signal;
              const isBuy  = sig === "BUY";
              const isSell = sig === "SELL";
              const isHold = sig === "HOLD";
              return (
                <div className="flex flex-col items-end gap-1">
                  <button
                    onClick={() => setShowPaperModal(true)}
                    className={clsx(
                      "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                      isBuy  ? "bg-bull/10 border-bull/30 text-bull hover:bg-bull/20"
                      : isSell ? "bg-bear/10 border-bear/30 text-red-400 hover:bg-bear/20"
                      : "bg-dark-card border-dark-border text-gray-400 hover:text-white hover:border-white/30"
                    )}
                  >
                    <Beaker size={13} />
                    {isBuy ? "Paper Buy" : isSell ? "Paper Sell / Short" : "Paper Trade"}
                  </button>
                  {(isSell || isHold) && (
                    <p className={clsx("text-[10px] max-w-[160px] text-right leading-tight",
                      isSell ? "text-red-400/70" : "text-gray-500")}>
                      {isSell ? "AI signals exit/short — proceed with caution" : "No strong entry signal from AI"}
                    </p>
                  )}
                </div>
              );
            })()}
          </div>
        </div>

        {/* Tabs row — inside the card, below price */}
        <div className="flex gap-2 flex-wrap mt-4 pt-4 border-t border-dark-border">
          {HORIZON_TABS.map(({ key, label }) => (
            <button key={key} onClick={() => setTab(key)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs sm:px-4 sm:py-2 sm:text-sm rounded-lg font-medium transition-colors",
                tab === key ? "bg-brand-500 text-white"
                  : "bg-dark-bg border border-dark-border text-gray-400 hover:text-white"
              )}>
              {key === "backtest" && <FlaskConical size={14} />}
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* ── PREDICTION VIEW ── */}
      {tab !== "backtest" && tab !== "history" && (
        <>
          {/* Trade Levels — shown above prediction panels */}
          {prediction?.signal && (() => {
            const tl = (prediction as any).trade_levels;
            if (!tl || tl.entry_low == null || tl.entry_high == null || tl.stop_loss == null || tl.take_profit == null) return null;
            const sig = prediction.signal;
            const cp: number | null = prediction.current_price ?? null;
            const fmt = (n: number) => n.toLocaleString();
            const pctFrom = (price: number) => cp ? ((price - cp) / cp * 100).toFixed(1) : null;
            const entryLabel = sig === "BUY" ? "Buy Zone" : sig === "SELL" ? "Sell Zone" : "Watch Zone";
            const entryColor = sig === "SELL" ? "text-bear" : "text-bull";
            const entryBg    = sig === "SELL" ? "bg-bear/10 border-bear/30" : "bg-bull/10 border-bull/30";
            const rrGood     = tl.risk_reward_ratio >= 1.5;
            const trailPct: number | null = tl.trailing_stop_pct ?? null;
            const gridCols = trailPct
              ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-5"
              : "grid-cols-2 md:grid-cols-4";
            return (
              <div className="bg-dark-card border border-dark-border rounded-2xl p-6">
                <h2 className="font-bold text-lg mb-4">Trade Levels <span className="text-xs font-normal text-gray-500 ml-2">({tab} term)</span></h2>
                <div className={`grid ${gridCols} gap-4`}>
                  <div className={`rounded-xl border p-4 ${entryBg}`}>
                    <p className="text-xs text-gray-400 mb-1">{entryLabel}</p>
                    <p className={`font-mono font-bold text-sm ${entryColor}`}>
                      {currency}{fmt(tl.entry_low)} – {currency}{fmt(tl.entry_high)}
                    </p>
                  </div>
                  {(() => {
                    const tpPct = pctFrom(tl.take_profit);
                    const tpUp = tpPct === null || parseFloat(tpPct) >= 0;
                    return (
                      <div className={`rounded-xl border p-4 ${tpUp ? "bg-bull/10 border-bull/30" : "bg-bear/10 border-bear/30"}`}>
                        <p className="text-xs text-gray-400 mb-1">Take Profit</p>
                        <p className={`font-mono font-bold text-sm ${tpUp ? "text-bull" : "text-bear"}`}>
                          {currency}{fmt(tl.take_profit)}
                          {tpPct && <span className="ml-2 text-xs font-normal">{parseFloat(tpPct) >= 0 ? "+" : ""}{tpPct}%</span>}
                        </p>
                      </div>
                    );
                  })()}
                  <div className="rounded-xl border p-4 bg-bear/10 border-bear/30">
                    <p className="text-xs text-gray-400 mb-1">Stop Loss</p>
                    <p className="font-mono font-bold text-sm text-bear">
                      {currency}{fmt(tl.stop_loss)}
                      {pctFrom(tl.stop_loss) && <span className="ml-2 text-xs font-normal">{pctFrom(tl.stop_loss)}%</span>}
                    </p>
                    <p className="text-xs text-gray-500 mt-0.5">Fixed entry stop</p>
                  </div>
                  {trailPct && (
                    <div className="rounded-xl border p-4 bg-orange-500/10 border-orange-500/30">
                      <p className="text-xs text-gray-400 mb-1">Trailing Stop</p>
                      <p className="font-mono font-bold text-sm text-orange-400">{trailPct}% below peak</p>
                      <p className="text-xs text-gray-500 mt-0.5">Moves up as price rises</p>
                    </div>
                  )}
                  <div className={`rounded-xl border p-4 ${rrGood ? "bg-bull/10 border-bull/30" : "bg-neutral/10 border-neutral/30"}`}>
                    <p className="text-xs text-gray-400 mb-1">Risk / Reward</p>
                    <p className={`font-mono font-bold text-sm ${rrGood ? "text-bull" : "text-neutral"}`}>
                      1 : {tl.risk_reward_ratio}
                    </p>
                  </div>
                </div>

                <TradeLevelVisualizer
                  entryLow={tl.entry_low}
                  entryHigh={tl.entry_high}
                  stopLoss={tl.stop_loss}
                  takeProfit={tl.take_profit}
                  currentPrice={cp}
                  signal={sig as "BUY" | "SELL" | "HOLD"}
                  currency={currency}
                />
                <p className="text-xs text-gray-500 mt-1">
                  Based on 14-day ATR · Not financial advice — always set your own risk limits.
                </p>
              </div>
            );
          })()}

          {/* Regime warning — shown when model reliability is lower */}
          {prediction?.market_regime && (() => {
            const regime = prediction.market_regime;
            const label: string = regime?.trend ?? "";
            const isHighRisk = label === "BEAR_VOLATILE" || label === "BULL_VOLATILE";
            const isBear = label.startsWith("BEAR");
            if (!label || label === "SIDEWAYS") return null;
            return (
              <div className={clsx(
                "flex items-start gap-3 rounded-xl px-4 py-3 border text-xs",
                isHighRisk ? "bg-yellow-500/10 border-yellow-500/30 text-yellow-300"
                  : isBear  ? "bg-bear/10 border-bear/30 text-red-300"
                  : "bg-bull/10 border-bull/30 text-green-300"
              )}>
                <span className="text-lg leading-none mt-0.5">
                  {isHighRisk ? "⚠️" : isBear ? "🐻" : "🐂"}
                </span>
                <div>
                  <strong>Market Regime: {label.replace("_", " ")}</strong>
                  {isHighRisk && <span className="ml-2 opacity-80">— Model hit rate historically drops ~5–8% in high-volatility regimes. Apply extra caution.</span>}
                  {!isHighRisk && isBear && <span className="ml-2 opacity-80">— Bear market detected. BUY signals carry higher risk; favour shorter horizons.</span>}
                  {!isHighRisk && !isBear && <span className="ml-2 opacity-80">— Favourable conditions for BUY signals based on historical validation.</span>}
                </div>
              </div>
            );
          })()}

          {/* TradingView Chart — high up for visual weight */}
          <div className="rounded-2xl border border-dark-border overflow-hidden">
            <TradingViewWidget symbol={symbol} market={isCrypto ? "CRYPTO" : market} height={460} />
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-3">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-bold text-lg flex items-center gap-2">
                  AI Prediction — {tab} term
                  {predFetching && !predLoading && (
                    <span className="flex items-center gap-1 text-xs font-normal text-yellow-500/80">
                      <Loader2 size={12} className="animate-spin" />
                      Updating…
                    </span>
                  )}
                </h2>
                {prediction?.target_price && prediction?.current_price && (() => {
                  const pct = ((prediction.target_price - prediction.current_price) / prediction.current_price) * 100;
                  const up = pct >= 0;
                  const pctColor = up ? "text-bull" : "text-bear";
                  return (
                    <span className="hidden sm:flex shrink-0 text-right items-baseline gap-1">
                      <span className="text-gray-400 text-sm">Target Price:</span>
                      <span className="font-mono font-bold text-base">{currency}{prediction.target_price.toLocaleString()}</span>
                      <span className={`text-sm font-medium ${pctColor}`}>
                        {up ? "+" : ""}{pct.toFixed(1)}%
                      </span>
                    </span>
                  );
                })()}
              </div>
              {predLoading ? (
                <div className="space-y-4 py-2">
                  <div className="flex items-center gap-3">
                    <Loader2 size={18} className="animate-spin text-brand-500 shrink-0" />
                    <div>
                      <p className="text-sm text-white font-medium">
                        {isComputing
                          ? `Computing prediction… ${computeSeconds}s`
                          : "Running AI analysis…"}
                      </p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {isComputing
                          ? "Server is crunching technicals, fundamentals & news in the background"
                          : "Analysing technicals, fundamentals & news sentiment"}
                      </p>
                    </div>
                  </div>
                  <div className="w-full h-1.5 bg-dark-border rounded-full overflow-hidden">
                    <div
                      className="h-full bg-brand-500 rounded-full transition-all duration-1000"
                      style={{ width: isComputing ? `${Math.min(95, (computeSeconds / 120) * 100)}%` : "15%" }}
                    />
                  </div>
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-4 bg-dark-border rounded animate-pulse" />
                  ))}
                </div>
              ) : predError && !prediction ? (
                <div className="space-y-3 py-2">
                  <p className="text-red-400 text-sm font-medium">Failed to load prediction</p>
                  <p className="text-gray-500 text-xs">The server isn&apos;t responding. Try again in a moment.</p>
                  <button onClick={() => { setIsComputing(false); refetchPrediction(); }}
                    className="px-4 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors">
                    Retry Now
                  </button>
                </div>
              ) : (prediction as any)?.error ? (
                <p className="text-red-400 text-sm">{(prediction as any).error}</p>
              ) : prediction?.signal ? (
                <>
                  {/* Signal strip */}
                  <div className={clsx(
                    "flex items-center justify-between rounded-xl px-4 py-3 border",
                    prediction.signal === "BUY"  ? "bg-bull/10 border-bull/30" :
                    prediction.signal === "SELL" ? "bg-bear/10 border-bear/30" :
                    "bg-neutral/10 border-neutral/30"
                  )}>
                    <div>
                      <p className="text-xs text-gray-400 mb-0.5">AI Signal</p>
                      <SignalBadge signal={prediction.signal} confidence={prediction.confidence} size="md" />
                    </div>
                    <div className="text-right">
                      <p className="text-xs text-gray-400 mb-0.5">Conviction</p>
                      <p className={clsx("text-2xl font-black tabular-nums",
                        prediction.signal === "BUY"  ? "text-bull" :
                        prediction.signal === "SELL" ? "text-bear" : "text-neutral"
                      )}>{prediction.confidence}<span className="text-sm font-medium">%</span></p>
                    </div>
                  </div>
                  <ConfidenceMeter
                    value={prediction.confidence}
                    label="Signal Conviction"
                  />
                  <div>
                    <p className="text-gray-400 text-sm mb-2">Key Reasons</p>
                    <ul className="space-y-1.5">
                      {prediction.reasoning.slice(0, 4).map((r: any, i: number) => (
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
              ) : (
                <div className="space-y-3">
                  <p className="text-gray-500 text-sm">
                    Prediction timed out — the backend is waking up (free tier sleeps after inactivity). Retrying automatically…
                  </p>
                  <button
                    onClick={() => refetchPrediction()}
                    className="px-4 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors"
                  >
                    Retry Now
                  </button>
                </div>
              )}
              {/* Data source footer */}
              {prediction?.signal && (
                <div className="border-t border-dark-border pt-3 mt-2">
                  <p className="text-xs text-gray-600 leading-relaxed">
                    <span className="text-gray-500 font-medium">Sources: </span>
                    {isCrypto
                      ? "Price · Binance USDT · Technicals · News sentiment"
                      : market === "IN"
                      ? `Price · Yahoo Finance · Fundamentals · screener.in${prediction.market_regime ? " · NSE regime" : ""} · News`
                      : `Price · Yahoo Finance · Fundamentals · SEC filings${prediction.market_regime ? " · Market regime" : ""} · News`
                    }
                    {" · "}
                    <span title="Time when this prediction was computed">
                      Updated {prediction.generated_at
                        ? new Date(prediction.generated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })
                        : "recently"}
                    </span>
                  </p>
                </div>
              )}
            </div>

            <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-4">
              <h2 className="font-bold text-lg">{isCrypto ? "Signal Breakdown" : "Technical Signals"}</h2>
              {prediction && (
                <div className="space-y-2">
                  {isCrypto ? (
                    <>
                      <ConfidenceMeter value={(prediction.technical as any)?.score ?? 50} label="Technical Score" />
                      <ConfidenceMeter value={(prediction as any).fear_greed?.score ?? 50} label="Market Sentiment (Fear/Greed)" />
                      <ConfidenceMeter value={prediction.sentiment_score?.score ?? 50} label="News Sentiment" />
                      <ConfidenceMeter value={(prediction as any).on_chain_proxy?.score ?? 50} label="Volume Analysis" />
                    </>
                  ) : (
                    <>
                      {(prediction as any).confidence_breakdown ? (
                        <ConfidenceBreakdown
                          score={(prediction as any).confidence_score}
                          band={(prediction as any).confidence_band}
                          components={(prediction as any).confidence_breakdown}
                        />
                      ) : (
                        <>
                          <ConfidenceMeter value={prediction.fundamental_score.score} label="Fundamental Score" />
                          <ConfidenceMeter value={prediction.sentiment_score.score} label="News Sentiment Score" />
                          {(() => {
                            const rsi = prediction.technical?.rsi;
                            if (!rsi) return null;
                            const r = Math.round(rsi);
                            const label = r >= 70 ? `RSI ${r} — Overbought`
                                        : r <= 30 ? `RSI ${r} — Oversold`
                                        : r >= 55 ? `RSI ${r} — Bullish momentum`
                                        : r <= 45 ? `RSI ${r} — Bearish momentum`
                                        : `RSI ${r} — Neutral`;
                            const barVal = r >= 70 ? 85 : r <= 30 ? 15 : Math.round(50 + (r - 50) * 1.5);
                            return <ConfidenceMeter value={Math.max(0, Math.min(100, barVal))} label={label} />;
                          })()}
                        </>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {((prediction as any)?.bull_case?.length > 0 || (prediction as any)?.bear_case?.length > 0) && (
            <BullBearCase
              bull={(prediction as any).bull_case ?? []}
              bear={(prediction as any).bear_case ?? []}
            />
          )}

          {attribution && !isCrypto && (
            <FactorAttributionWaterfall data={attribution} prediction={prediction} />
          )}

          <section>
            <h2 className="text-lg font-semibold mb-3">News & Sentiment</h2>
            <div className="grid md:grid-cols-2 gap-3">
              {news?.articles.slice(0, 8).map((a: any, i: number) => (
                <NewsCard key={i} article={a} />
              ))}
              {news && !news.articles.length && (
                <p className="text-gray-500 text-sm col-span-2">No recent news found.</p>
              )}
              {!news && (
                <p className="text-gray-500 text-sm col-span-2 animate-pulse">Fetching latest news…</p>
              )}
            </div>
          </section>
        </>
      )}

      {/* ── HISTORY VIEW ── */}
      {tab === "history" && (
        <div className="space-y-4">
          {/* Horizon selector */}
          {!isCrypto && (
            <div className="flex gap-2">
              {(["short", "medium", "long"] as Horizon[]).map(h => (
                <button key={h} onClick={() => setHistoryHorizon(h)}
                  className={clsx("px-4 py-1.5 rounded-lg text-xs font-medium border transition-colors capitalize",
                    historyHorizon === h
                      ? "bg-brand-500 text-white border-brand-500"
                      : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
                  {h} term
                </button>
              ))}
            </div>
          )}
          {isCrypto ? (
            <div className="bg-dark-card border border-dark-border rounded-2xl p-6 text-center text-gray-500 text-sm">
              Score history is available for stocks only.
            </div>
          ) : (
            <ScoreHistoryChart points={scoreHistory?.points ?? []} />
          )}
        </div>
      )}

      {/* ── BACKTEST VIEW ── */}
      {tab === "backtest" && (
        <div className="space-y-6">
          {isCrypto && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl px-4 py-3 text-xs text-yellow-300">
              Crypto backtests reflect <strong>technical signal accuracy only</strong> — macro events, regulatory news, and market sentiment are not captured in historical simulation. Results may be lower than for stocks.
            </div>
          )}
          {/* Controls */}
          <div className="bg-dark-card border border-dark-border rounded-2xl p-5 flex flex-wrap gap-4 items-end">
            <div>
              <p className="text-xs text-gray-400 mb-1.5">Testing symbol</p>
              <p className="font-mono font-bold text-white text-sm">
                {symbol} · {isCrypto ? "₿ Crypto" : market === "US" ? "🇺🇸 US" : "🇮🇳 India"}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-400 mb-1.5">Horizon</p>
              <div className="flex gap-2">
                {(["short", "medium", "long"] as Horizon[]).map(h => (
                  <button key={h} onClick={() => setBtHorizon(h)}
                    className={clsx("px-3 py-2 rounded-lg text-xs font-medium capitalize border transition-colors",
                      btHorizon === h ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                    {h}
                  </button>
                ))}
              </div>
            </div>
            <button onClick={runBacktest} disabled={btRunning}
              className="ml-auto px-5 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 disabled:opacity-50 transition-colors flex items-center gap-2">
              <FlaskConical size={14} />
              {btRunning ? "Running…" : "Run Backtest"}
            </button>
          </div>

          {btRunning && (
            <div className="bg-dark-card border border-dark-border rounded-2xl p-8 text-center text-gray-400 text-sm animate-pulse">
              Analysing historical data for {symbol}… this takes 20–40 seconds
            </div>
          )}

          {btError && <p className="text-bear text-sm">{btError}</p>}

          {btData && (
            <div className="space-y-5">
              {/* Summary cards */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: "Overall Accuracy", value: `${btData.accuracy_pct}%`,
                    color: btData.accuracy_pct >= 60 ? "text-bull" : btData.accuracy_pct >= 45 ? "text-neutral" : "text-bear" },
                  { label: "Tests Run", value: btData.total_tests, color: "text-white" },
                  { label: "Correct Calls", value: btData.correct_predictions, color: "text-bull" },
                  { label: "Forward Window", value: HORIZON_LABEL[btData.horizon], color: "text-white" },
                ].map(c => (
                  <div key={c.label} className="bg-dark-card border border-dark-border rounded-2xl p-5">
                    <p className="text-xs text-gray-400 mb-1">{c.label}</p>
                    <p className={clsx("text-2xl font-bold", c.color)}>{c.value}</p>
                  </div>
                ))}
              </div>

              {/* Signal breakdown */}
              <div className="grid md:grid-cols-3 gap-4">
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <SignalBadge signal="BUY" size="sm" />
                    <span className="text-sm text-gray-400">{btData.buy_signals_tested} signals</span>
                  </div>
                  <p className="text-sm text-gray-300">
                    Avg return when BUY predicted:
                    <span className={clsx("ml-2 font-bold", btData.avg_return_on_buy_pct >= 0 ? "text-bull" : "text-bear")}>
                      {btData.avg_return_on_buy_pct >= 0 ? "+" : ""}{btData.avg_return_on_buy_pct}%
                    </span>
                  </p>
                  <p className="text-xs text-gray-500">{btData.profitable_buy_calls} of {btData.buy_signals_tested} were profitable</p>
                </div>
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <SignalBadge signal="SELL" size="sm" />
                    <span className="text-sm text-gray-400">{btData.sell_signals_tested} signals</span>
                  </div>
                  <p className="text-sm text-gray-300">
                    Avg return when SELL predicted:
                    <span className={clsx("ml-2 font-bold", btData.avg_return_on_sell_pct <= 0 ? "text-bull" : "text-bear")}>
                      {btData.avg_return_on_sell_pct >= 0 ? "+" : ""}{btData.avg_return_on_sell_pct}%
                    </span>
                  </p>
                  <p className="text-xs text-gray-500">{btData.profitable_sell_calls} of {btData.sell_signals_tested} declined as predicted</p>
                </div>
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <SignalBadge signal="HOLD" size="sm" />
                    <span className="text-sm text-gray-400">{btData.hold_signals_tested} signals</span>
                  </div>
                  <ConfidenceMeter value={btData.accuracy_pct} label="Overall signal accuracy" />
                </div>
              </div>

              {/* Results table */}
              <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
                <div className="px-6 py-4 border-b border-dark-border">
                  <h2 className="font-semibold">Recent Test Windows (last 30)</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-dark-border text-gray-400 text-left">
                        <th className="px-4 py-3 font-medium">Date</th>
                        <th className="px-4 py-3 font-medium text-right">Entry</th>
                        <th className="px-4 py-3 font-medium text-right">Exit</th>
                        <th className="px-4 py-3 font-medium text-right">Return</th>
                        <th className="px-4 py-3 font-medium">Predicted</th>
                        <th className="px-4 py-3 font-medium">Actual</th>
                        <th className="px-4 py-3 font-medium text-center">Result</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...btData.results].reverse().map((r, i) => (
                        <tr key={i} className={clsx("border-b border-dark-border",
                          r.correct ? "bg-bull/5" : "bg-bear/5")}>
                          <td className="px-4 py-3 text-gray-400 font-mono text-xs">{r.date}</td>
                          <td className="px-4 py-3 text-right font-mono">{currency}{r.entry_price}</td>
                          <td className="px-4 py-3 text-right font-mono">{currency}{r.exit_price}</td>
                          <td className={clsx("px-4 py-3 text-right font-mono font-bold",
                            r.actual_return_pct >= 0 ? "text-bull" : "text-bear")}>
                            {r.actual_return_pct >= 0 ? "+" : ""}{r.actual_return_pct}%
                          </td>
                          <td className="px-4 py-3"><SignalBadge signal={r.predicted_signal as any} size="sm" /></td>
                          <td className="px-4 py-3"><SignalBadge signal={r.actual_signal as any} size="sm" /></td>
                          <td className="px-4 py-3 text-center">
                            {r.correct
                              ? <CheckCircle size={16} className="text-bull inline" />
                              : <XCircle size={16} className="text-bear inline" />}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Paper Trade Modal */}
      {showPaperModal && prediction && (
        <PaperTradeModal
          symbol={symbol}
          market={market as "IN" | "US"}
          currentPrice={prediction.current_price ?? quote?.price ?? 0}
          signal={prediction.signal}
          horizon={horizon}
          currency={market === "IN" ? "₹" : "$"}
          suggestedStopLoss={(prediction as any).trade_levels?.stop_loss ?? null}
          suggestedTargetPrice={(prediction as any).trade_levels?.take_profit ?? null}
          onClose={() => setShowPaperModal(false)}
        />
      )}
    </div>
  );
}
