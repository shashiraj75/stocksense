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
      {(() => {
        const sig = (tab !== "backtest" && prediction && !predLoading) ? prediction.signal : null;
        const accentClass = sig === "BUY" ? "border-bull/40" : sig === "SELL" ? "border-bear/40" : "border-dark-border";
        const priceChange = !isCrypto ? quote?.change ?? null : cryptoQuote?.change_pct ?? null;
        const priceUp = priceChange != null && priceChange >= 0;

        return (
          <div className={clsx("bg-dark-card border rounded-2xl overflow-hidden", accentClass)}>
            {/* Top accent strip based on signal */}
            {sig && (
              <div className={clsx("h-0.5 w-full",
                sig === "BUY" ? "bg-gradient-to-r from-bull/60 via-bull/30 to-transparent"
                : sig === "SELL" ? "bg-gradient-to-r from-bear/60 via-bear/30 to-transparent"
                : "bg-gradient-to-r from-neutral/40 via-neutral/20 to-transparent"
              )} />
            )}

            <div className="p-5">
              <div className="flex gap-6">
                {/* ── Left column: all stock info ── */}
                <div className="flex-1 min-w-0">

                  {/* Row 1: symbol + badges */}
                  <div className="flex flex-wrap items-center gap-2 mb-2.5">
                    <h1 className="text-2xl font-black font-mono tracking-tight">{symbol}</h1>
                    <span className="text-[11px] bg-white/5 border border-white/10 px-2 py-0.5 rounded-md text-gray-400">
                      {isCrypto ? `CRYPTO · ${CRYPTO_NAMES[symbol] ?? symbol}` : market === "US" ? "🇺🇸 NYSE / NASDAQ" : "🇮🇳 NSE India"}
                    </span>
                    {!isCrypto && (() => {
                      const cap = getCapCategory(quote?.market_cap, market);
                      if (!cap) return null;
                      return (
                        <span className={`text-[11px] border px-2 py-0.5 rounded-md font-medium ${cap.color} ${cap.bg}`}>
                          {cap.label}
                        </span>
                      );
                    })()}
                    {/* Market status inline */}
                    <div className="flex items-center gap-1.5 ml-auto">
                      <span className="relative flex h-2 w-2">
                        {marketStatus.isOpen && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />}
                        <span className={clsx("relative inline-flex rounded-full h-2 w-2", marketStatus.isOpen ? "bg-green-500" : "bg-red-500")} />
                      </span>
                      <span className="text-[11px] text-gray-500">
                        {marketStatus.isOpen ? "Live" : marketStatus.label}
                        {marketStatus.nextEventLabel && <span className="text-gray-600"> · {marketStatus.nextEventLabel}</span>}
                      </span>
                    </div>
                  </div>

                  {/* Row 2: price + change */}
                  <div className="flex flex-wrap items-baseline gap-3 mb-3">
                    <span className="text-4xl font-black font-mono tracking-tighter">
                      {isCrypto
                        ? `$${(cryptoQuote?.price ?? prediction?.current_price ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                        : quote ? `${currency}${quote.price.toLocaleString()}` : <span className="text-gray-600 text-2xl">Loading…</span>}
                    </span>
                    {!isCrypto && quote?.change != null && (
                      <span className={clsx(
                        "inline-flex items-center gap-1 text-sm font-bold px-2 py-0.5 rounded-lg",
                        priceUp ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"
                      )}>
                        {priceUp ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                        {quote.change >= 0 ? "+" : ""}{quote.change} ({quote.change_pct}%)
                      </span>
                    )}
                    {isCrypto && cryptoQuote?.change_pct != null && (
                      <span className={clsx(
                        "inline-flex items-center gap-1 text-sm font-bold px-2 py-0.5 rounded-lg",
                        priceUp ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"
                      )}>
                        {priceUp ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                        {cryptoQuote.change_pct >= 0 ? "+" : ""}{cryptoQuote.change_pct}%
                      </span>
                    )}
                  </div>

                  {/* Row 3: stats chips */}
                  {!isCrypto && quote && (
                    <div className="flex flex-wrap gap-2 mb-3">
                      {[
                        ["52W High", `${currency}${quote.fifty_two_week_high?.toLocaleString()}`, "text-gray-300"],
                        ["52W Low",  `${currency}${quote.fifty_two_week_low?.toLocaleString()}`,  "text-gray-300"],
                        ["Mkt Cap",  (() => {
                          const v = quote.market_cap;
                          if (!v) return "—";
                          if (v >= 1e12) return `${currency}${(v/1e12).toFixed(2)}T`;
                          if (v >= 1e9)  return `${currency}${(v/1e9).toFixed(2)}B`;
                          return `${currency}${(v/1e6).toFixed(0)}M`;
                        })(), "text-gray-300"],
                        ["Volume", quote.volume?.toLocaleString() ?? "—", "text-gray-300"],
                      ].map(([label, value, valueColor]) => (
                        <div key={label} className="flex items-center gap-1.5 bg-white/[0.04] border border-white/[0.07] rounded-lg px-2.5 py-1">
                          <span className="text-[11px] text-gray-500">{label}</span>
                          <span className={clsx("text-[11px] font-mono font-bold", valueColor)}>{value}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Row 4: score indicator pills */}
                  {prediction && !predLoading && !isCrypto && (() => {
                    const scoreItems: { label: string; value: number; bg: string; bar: string; text: string }[] = [];
                    const fundScore = prediction.fundamental_score.score;
                    const sentScore = prediction.sentiment_score.score;
                    const rsi = prediction.technical?.rsi ? Math.round(prediction.technical.rsi) : null;

                    const fundTier = fundScore >= 65 ? { bg: "bg-bull/10 border-bull/20", bar: "bg-bull", text: "text-bull" }
                                   : fundScore >= 45 ? { bg: "bg-yellow-500/10 border-yellow-500/20", bar: "bg-yellow-400", text: "text-yellow-400" }
                                   : { bg: "bg-bear/10 border-bear/20", bar: "bg-bear", text: "text-bear" };
                    const sentTier = sentScore >= 60 ? { bg: "bg-bull/10 border-bull/20", bar: "bg-bull", text: "text-bull" }
                                   : sentScore >= 40 ? { bg: "bg-yellow-500/10 border-yellow-500/20", bar: "bg-yellow-400", text: "text-yellow-400" }
                                   : { bg: "bg-bear/10 border-bear/20", bar: "bg-bear", text: "text-bear" };

                    scoreItems.push({ label: "Fundamentals", value: fundScore, ...fundTier });
                    scoreItems.push({ label: "Sentiment", value: sentScore, ...sentTier });

                    if (rsi !== null) {
                      const rsiLabel = rsi >= 70 ? "Overbought" : rsi <= 30 ? "Oversold" : rsi >= 55 ? "Bullish" : rsi <= 45 ? "Bearish" : "Neutral";
                      const rsiTier = rsi >= 70 || rsi <= 30
                        ? { bg: "bg-yellow-500/10 border-yellow-500/20", bar: "bg-yellow-400", text: "text-yellow-400" }
                        : rsi >= 55 ? { bg: "bg-bull/10 border-bull/20", bar: "bg-bull", text: "text-bull" }
                        : rsi <= 45 ? { bg: "bg-bear/10 border-bear/20", bar: "bg-bear", text: "text-bear" }
                        : { bg: "bg-white/5 border-white/10", bar: "bg-gray-400", text: "text-gray-400" };
                      scoreItems.push({ label: `RSI ${rsi} · ${rsiLabel}`, value: rsi, ...rsiTier });
                    }

                    return (
                      <div className="flex flex-wrap gap-2">
                        {scoreItems.map(({ label, value, bg, bar, text }) => (
                          <div key={label} className={clsx("flex items-center gap-2 rounded-lg border px-2.5 py-1.5", bg)}>
                            <span className="text-[11px] text-gray-400 font-medium">{label}</span>
                            <div className="flex items-center gap-1.5">
                              <div className="w-14 h-1 bg-black/30 rounded-full overflow-hidden">
                                <div className={clsx("h-full rounded-full", bar)} style={{ width: `${Math.min(100, value)}%` }} />
                              </div>
                              <span className={clsx("text-[11px] font-black tabular-nums font-mono", text)}>{value}%</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                  {isCrypto && (
                    <p className="text-xs text-gray-500 mt-1">TradingView · Binance USDT · Predictions: technicals + volume + sentiment</p>
                  )}
                </div>

                {/* ── Right column: signal panel ── */}
                {tab !== "backtest" && (
                  <div className="shrink-0 flex flex-col items-center justify-center gap-3 min-w-[140px]">
                    {prediction && !predLoading ? (
                      <>
                        {/* Big signal display */}
                        <div className={clsx(
                          "w-full rounded-xl border px-4 py-3 text-center",
                          prediction.signal === "BUY"  ? "bg-bull/10 border-bull/30"
                          : prediction.signal === "SELL" ? "bg-bear/10 border-bear/30"
                          : "bg-white/5 border-white/10"
                        )}>
                          <p className="text-[10px] text-gray-500 uppercase tracking-widest mb-0.5">AI Signal</p>
                          <p className={clsx("text-2xl font-black tracking-wider",
                            prediction.signal === "BUY" ? "text-bull" : prediction.signal === "SELL" ? "text-bear" : "text-neutral"
                          )}>
                            {prediction.signal === "BUY" ? "▲ BUY" : prediction.signal === "SELL" ? "▼ SELL" : "— HOLD"}
                          </p>
                          <div className="mt-1.5 flex items-center justify-center gap-1.5">
                            <div className="flex-1 h-1 bg-black/30 rounded-full overflow-hidden">
                              <div
                                className={clsx("h-full rounded-full",
                                  prediction.signal === "BUY" ? "bg-bull" : prediction.signal === "SELL" ? "bg-bear" : "bg-neutral"
                                )}
                                style={{ width: `${prediction.confidence}%` }}
                              />
                            </div>
                            <span className={clsx("text-xs font-bold tabular-nums",
                              prediction.signal === "BUY" ? "text-bull" : prediction.signal === "SELL" ? "text-bear" : "text-neutral"
                            )}>{prediction.confidence}%</span>
                          </div>
                          <p className="text-[10px] text-gray-600 mt-0.5">confidence</p>
                        </div>

                        {/* Paper Trade button */}
                        {!isCrypto && (() => {
                          const isBuy  = prediction.signal === "BUY";
                          const isSell = prediction.signal === "SELL";
                          return (
                            <div className="w-full flex flex-col gap-1">
                              <button
                                onClick={() => setShowPaperModal(true)}
                                className={clsx(
                                  "w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border transition-all",
                                  isBuy  ? "bg-bull/15 border-bull/40 text-bull hover:bg-bull/25"
                                  : isSell ? "bg-bear/15 border-bear/40 text-red-400 hover:bg-bear/25"
                                  : "bg-white/5 border-white/10 text-gray-400 hover:text-white hover:border-white/20"
                                )}
                              >
                                <Beaker size={12} />
                                {isBuy ? "Paper Buy" : isSell ? "Paper Sell / Short" : "Paper Trade"}
                              </button>
                              {(isSell || prediction.signal === "HOLD") && (
                                <p className={clsx("text-[10px] text-center leading-tight",
                                  isSell ? "text-red-400/60" : "text-gray-600")}>
                                  {isSell ? "AI signals exit — caution" : "No strong entry signal"}
                                </p>
                              )}
                            </div>
                          );
                        })()}
                      </>
                    ) : predLoading ? (
                      <div className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-5 text-center">
                        <Loader2 size={20} className="animate-spin text-brand-500 mx-auto mb-2" />
                        <p className="text-[11px] text-gray-500">Computing…</p>
                      </div>
                    ) : null}
                  </div>
                )}
              </div>

              {/* Market Regime inline row — compact, above tabs */}
              {prediction?.market_regime && !predLoading && (() => {
                const label: string = prediction.market_regime?.trend ?? "";
                if (!label || label === "SIDEWAYS") return null;
                const isHighRisk = label === "BEAR_VOLATILE" || label === "BULL_VOLATILE";
                const isBearRegime = label.startsWith("BEAR");
                const stockSig = prediction.signal;
                const conflicting = (!isBearRegime && !isHighRisk && (stockSig === "SELL" || stockSig === "HOLD"))
                                 || (isBearRegime && !isHighRisk && stockSig === "BUY");

                const regimeDisplay = label.replace(/_/g, " ");
                const emoji = isHighRisk ? "⚠️" : isBearRegime ? "🐻" : "🐂";

                let note: string;
                if (isHighRisk) {
                  note = "High volatility — model accuracy drops ~5–8%. Size conservatively.";
                } else if (isBearRegime && stockSig === "BUY") {
                  note = "Market downtrend conflicts with stock-level BUY — use tighter stop loss.";
                } else if (isBearRegime) {
                  note = "Broad market downtrend aligns with this stock's bearish signal.";
                } else if (conflicting) {
                  note = "Market is broadly bullish, but this stock's own signals are weak — SELL is stock-specific, not market-wide.";
                } else {
                  note = "Broad market is supportive — aligns with this stock's signal.";
                }

                return (
                  <div className={clsx(
                    "mt-3 flex items-start gap-2.5 rounded-xl px-3.5 py-2.5 border text-xs",
                    isHighRisk   ? "bg-yellow-500/8 border-yellow-500/20 text-yellow-300"
                    : conflicting ? "bg-blue-500/8 border-blue-500/20 text-blue-300"
                    : isBearRegime ? "bg-bear/8 border-bear/20 text-red-300"
                    : "bg-bull/8 border-bull/20 text-green-300"
                  )}>
                    <span className="text-sm leading-none mt-0.5 shrink-0">{emoji}</span>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                      <span>
                        <span className="font-semibold">Market: {regimeDisplay}</span>
                        <span className="opacity-50 ml-1 text-[10px]">macro</span>
                      </span>
                      <span className="opacity-30 hidden sm:inline">·</span>
                      <span>
                        <span className="font-semibold">Signal: {stockSig}</span>
                        <span className="opacity-50 ml-1 text-[10px]">stock-specific</span>
                      </span>
                      <span className="opacity-30 hidden sm:inline">·</span>
                      <span className="opacity-75">{note}</span>
                    </div>
                  </div>
                );
              })()}

              {/* Tabs row */}
              <div className="flex gap-2 flex-wrap mt-4 pt-4 border-t border-white/[0.06]">
                {HORIZON_TABS.map(({ key, label }) => (
                  <button key={key} onClick={() => setTab(key)}
                    className={clsx(
                      "flex items-center gap-1.5 px-3.5 py-1.5 text-xs sm:text-sm rounded-lg font-medium transition-all",
                      tab === key
                        ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                        : "bg-white/[0.04] border border-white/[0.07] text-gray-400 hover:text-white hover:bg-white/[0.08]"
                    )}>
                    {key === "backtest" && <FlaskConical size={13} />}
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

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
