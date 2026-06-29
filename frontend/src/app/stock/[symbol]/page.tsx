"use client";
import { useState, useEffect } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
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
import { ArrowUpRight, ArrowDownRight, FlaskConical, CheckCircle, XCircle, Loader2, Beaker, BarChart2, TrendingUp, TrendingDown } from "lucide-react";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { useAuth } from "@/lib/AuthContext";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { TradeLevelVisualizer } from "@/components/TradeLevelVisualizer";
import { EvidenceSummary } from "@/components/EvidenceSummary";

// Kill switch — set back to true to restore the "Was this signal useful?"
// thumbs up/down prompt. Hidden per user feedback that it looked annoying.
const SHOW_SIGNAL_FEEDBACK = false;

type Tab = Horizon | "backtest" | "history" | "fundamentals";

const HORIZON_TABS: { key: Tab; label: string }[] = [
  { key: "short", label: "Short Term" },
  { key: "medium", label: "Medium Term" },
  { key: "long", label: "Long Term" },
  { key: "fundamentals", label: "Fundamentals" },
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
  const symbol = decodeURIComponent(params?.symbol ?? "");
  const searchParams = useSearchParams();
  const rawMarket = searchParams?.get("market") || "US";
  const isCrypto = rawMarket === "CRYPTO";
  // Malformed/unrecognized ?market= values (wrong case, typos, stale links)
  // fall back to "US" instead of being cast through unvalidated — a bad
  // value here would silently mismatch currency display against the data
  // actually returned by API calls built from the same string.
  const market: Market = isCrypto ? "US" : (rawMarket === "IN" || rawMarket === "US" ? rawMarket : "US");
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
  const { user } = useAuth();

  // Sticky ticker bar — only shown once the user has scrolled past the
  // real ticker header, so it never appears as a duplicate on first load.
  const [showStickyTicker, setShowStickyTicker] = useState(false);
  useEffect(() => {
    const onScroll = () => setShowStickyTicker(window.scrollY > 160);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const horizon = (tab === "backtest" || tab === "history" || tab === "fundamentals") ? "medium" : (tab as Horizon);

  const { data: quote, dataUpdatedAt: quoteUpdatedAt } = useQuery({
    queryKey: ["quote", symbol, market],
    queryFn: () => fetchQuote(symbol, market),
    enabled: !isCrypto,
    refetchInterval: 60_000,   // backend Finnhub cache is 60s — no point polling faster
    staleTime: 55_000,
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
    staleTime: 10 * 60_000,   // news doesn't change every second
    refetchOnWindowFocus: false,
  });

  const { data: stockAccuracy } = useQuery({
    queryKey: ["stock-accuracy", symbol],
    queryFn: () => api.get<{ symbol: string; accuracy: Record<string, { total: number; correct: number; avg_ret: number; buy_ret: number }> }>(
      `/api/validation/results/stock/${symbol}`
    ).then(r => r.data),
    staleTime: 30 * 60_000,
    enabled: !isCrypto,
    retry: false,
  });

  const { data: attribution } = useQuery({
    queryKey: ["factor-attribution", symbol, market, horizon],
    queryFn: () => fetchFactorAttribution(symbol, market, horizon),
    enabled: tab !== "backtest" && tab !== "history" && tab !== "fundamentals" && !isCrypto && !!prediction?.signal,
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

  const { data: screenerFund, isLoading: screenerLoading } = useQuery({
    queryKey: ["screener-fundamentals", symbol],
    queryFn: () => api.get(`/api/stocks/${symbol}/screener-fundamentals?market=IN`).then(r => r.data),
    enabled: tab === "fundamentals" && market === "IN",
    staleTime: 4 * 60 * 60_000, // 4h — matches screener cache TTL
    refetchOnWindowFocus: false,
  });

  const { data: usFund, isLoading: usFundLoading } = useQuery({
    queryKey: ["us-fundamentals", symbol],
    queryFn: () => api.get(`/api/stocks/${symbol}/us-fundamentals`).then(r => r.data),
    enabled: tab === "fundamentals" && market === "US",
    staleTime: 4 * 60 * 60_000, // 4h — matches backend cache TTL
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

  // Signal feedback — existing vote for this user
  const { data: existingVote, refetch: refetchVote } = useQuery({
    queryKey: ["signal-feedback", symbol, market, horizon, user?.id],
    queryFn: async () => {
      if (!user?.id) return { vote: null };
      const res = await api.get(`/api/feedback/signal/${encodeURIComponent(symbol)}`, {
        params: { user_id: user.id, market, horizon },
      });
      return res.data as { vote: number | null };
    },
    enabled: !!user?.id && !!prediction,
  });

  const voteMutation = useMutation({
    mutationFn: async (vote: 1 | -1) => {
      if (!user?.id || !prediction) return;
      await api.post("/api/feedback/signal", {
        user_id: user.id,
        symbol,
        market,
        horizon,
        signal: prediction.signal,
        vote,
      });
    },
    onSuccess: () => refetchVote(),
  });

  // Show "not found" only if BOTH quote and prediction failed with no data
  const notFound = !isCrypto && !predLoading && !quote && predError;

  if (notFound) {
    return (
      <div className="flex flex-col items-center justify-center py-32 text-center space-y-4">
        <div className="text-5xl">🔍</div>
        <h1 className="text-2xl font-bold text-white">{symbol} not found</h1>
        <p className="text-gray-400 text-sm max-w-sm">
          The server may still be starting up — wait a moment and try refreshing.
          If the issue persists, this symbol may be delisted or unsupported.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="mt-2 px-5 py-2 rounded-xl bg-dark-card border border-dark-border text-white text-sm font-medium hover:bg-dark-border transition-colors"
        >
          Retry
        </button>
        <a href="/" className="px-5 py-2 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors">
          Back to Dashboard
        </a>
      </div>
    );
  }

  return (
    // Negative top margin pulls just this page closer to the nav row above —
    // scoped here rather than on the shared <main> padding in layout.tsx,
    // which every other page also relies on.
    <div className="space-y-5 -mt-2 sm:-mt-3">
      {/* Sticky ticker bar — keeps the symbol/price visible once you've
          scrolled past the real header below. Fixed (not CSS sticky) since
          visibility is already scroll-gated in JS, so there's no risk of it
          ever appearing twice. top offset comes from --nav-h, set by
          NavHeightObserver in layout.tsx, since the navbar's height varies
          across breakpoints and as its async content (index strip etc.) loads. */}
      {showStickyTicker && (
        <div
          className="fixed left-0 right-0 z-[5] bg-dark-bg/95 backdrop-blur-md border-b border-dark-border"
          style={{ top: "var(--nav-h, 0px)" }}
        >
          <div className="max-w-7xl mx-auto px-3 sm:px-4 py-2.5 flex items-center gap-3 overflow-x-auto scrollbar-hide">
            <span className="font-mono font-bold text-lg shrink-0">{symbol}</span>
            {(screenerFund?.company_name || usFund?.company_name || quote?.company_name) && (
              <span className="text-base text-gray-400 truncate shrink-0 max-w-[200px]">
                {screenerFund?.company_name || usFund?.company_name || quote?.company_name}
              </span>
            )}
            {!isCrypto && quote?.price != null && (
              <span className="font-mono font-bold text-lg ml-auto shrink-0">
                {currency}{quote.price.toLocaleString()}
              </span>
            )}
            {!isCrypto && quote?.change_pct != null && (
              <span className={clsx("text-sm font-semibold shrink-0", quote.change_pct >= 0 ? "text-bull" : "text-bear")}>
                {quote.change_pct >= 0 ? "+" : ""}{quote.change_pct.toFixed(2)}%
              </span>
            )}
          </div>
        </div>
      )}

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
              <div className="flex flex-col sm:flex-row gap-4 sm:gap-6">
                {/* ── Left column: all stock info ── */}
                <div className="flex-1 min-w-0">

                  {/* Page label — matches the icon+label convention every
                      other page (Market Overview, Daily Picks, Portfolio…)
                      uses, just smaller since the ticker below is the real title */}
                  <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-2">
                    <BarChart2 size={14} className="text-brand-500" />
                    <span className="uppercase tracking-wide font-semibold">Stock Analysis</span>
                  </div>

                  {/* Row 1: symbol + badges + full name */}
                  <div className="mb-2.5">
                    <div className="flex flex-wrap items-center gap-2">
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
                      {(screenerFund?.company_name || quote?.company_name) && (
                        <span className="text-2xl font-bold text-gray-400">
                          {screenerFund?.company_name || quote?.company_name}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Row 2: price + change */}
                  <div className="flex flex-wrap items-baseline gap-3 mb-3">
                    <span className="text-4xl font-black font-mono tracking-tighter">
                      {isCrypto
                        ? `$${(cryptoQuote?.price ?? prediction?.current_price ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                        : quote ? `${currency}${quote.price?.toLocaleString() ?? "—"}` : <span className="text-gray-600 text-2xl">Loading…</span>}
                    </span>
                    {!isCrypto && quote?.change != null && (
                      <span className={clsx(
                        "inline-flex items-center gap-1 text-sm font-bold px-2 py-0.5 rounded-lg",
                        priceUp ? "bg-bull/10 text-bull" : "bg-bear/10 text-bear"
                      )}>
                        {priceUp ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                        {(quote.change ?? 0) >= 0 ? "+" : ""}{quote.change} ({quote.change_pct ?? 0}%)
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
                        ...(quote.high != null ? [["Day High", `${currency}${quote.high.toLocaleString()}`, "text-gray-200"]] : []),
                        ...(quote.low != null ? [["Day Low", `${currency}${quote.low.toLocaleString()}`, "text-gray-200"]] : []),
                        ["52W High", `${currency}${quote.fifty_two_week_high?.toLocaleString()}`, "text-gray-200"],
                        ["52W Low",  `${currency}${quote.fifty_two_week_low?.toLocaleString()}`,  "text-gray-200"],
                        ["Mkt Cap",  (() => {
                          const v = quote.market_cap;
                          if (!v) return "—";
                          if (v >= 1e12) return `${currency}${(v/1e12).toFixed(2)}T`;
                          if (v >= 1e9)  return `${currency}${(v/1e9).toFixed(2)}B`;
                          return `${currency}${(v/1e6).toFixed(0)}M`;
                        })(), "text-gray-200"],
                        ["Volume", quote.volume?.toLocaleString() ?? "—", "text-gray-200"],
                      ].map(([label, value, valueColor]) => (
                        <div key={label} className="flex items-center gap-2 bg-white/[0.05] border border-white/[0.09] rounded-lg px-3 py-1.5">
                          <span className="text-xs text-gray-500">{label}</span>
                          <span className={clsx("text-sm font-mono font-bold", valueColor)}>{value}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Row 4: score indicator pills */}
                  {prediction && !predLoading && !isCrypto && (() => {
                    const scoreItems: { label: string; value: number; bg: string; bar: string; text: string }[] = [];
                    const fundScore = (prediction as any).fundamental_score?.score ?? null;
                    const sentScore = (prediction as any).sentiment_score?.score ?? null;
                    const rsi = prediction.technical?.rsi ? Math.round(prediction.technical.rsi) : null;

                    if (fundScore !== null) {
                      const fundTier = fundScore >= 65 ? { bg: "bg-bull/10 border-bull/20", bar: "bg-bull", text: "text-bull" }
                                     : fundScore >= 45 ? { bg: "bg-yellow-500/10 border-yellow-500/20", bar: "bg-yellow-400", text: "text-yellow-400" }
                                     : { bg: "bg-bear/10 border-bear/20", bar: "bg-bear", text: "text-bear" };
                      scoreItems.push({ label: "Fundamentals", value: fundScore, ...fundTier });
                    }
                    if (sentScore !== null) {
                      const sentTier = sentScore >= 60 ? { bg: "bg-bull/10 border-bull/20", bar: "bg-bull", text: "text-bull" }
                                     : sentScore >= 40 ? { bg: "bg-yellow-500/10 border-yellow-500/20", bar: "bg-yellow-400", text: "text-yellow-400" }
                                     : { bg: "bg-bear/10 border-bear/20", bar: "bg-bear", text: "text-bear" };
                      scoreItems.push({ label: "Sentiment", value: sentScore, ...sentTier });
                    }

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
                          <div key={label} className={clsx("flex items-center gap-3 rounded-lg border px-3 py-2", bg)}>
                            <span className="text-xs text-gray-300 font-medium">{label}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-20 h-1.5 bg-black/30 rounded-full overflow-hidden">
                                <div className={clsx("h-full rounded-full", bar)} style={{ width: `${Math.min(100, value)}%` }} />
                              </div>
                              <span className={clsx("text-sm font-black tabular-nums font-mono", text)}>{value}%</span>
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
                  <div className="shrink-0 flex flex-col items-center justify-center gap-3 w-full sm:w-auto sm:min-w-[140px]">
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
                          <p className="text-[11px] text-gray-400 mt-0.5">confidence</p>

                          {/* Per-stock historical accuracy */}
                          {(() => {
                            const acc = stockAccuracy?.accuracy?.[horizon];
                            if (!acc || !acc.total || acc.total < 5) return null;
                            const pct = Math.round((acc.correct / acc.total) * 100);
                            if (Number.isNaN(pct)) return null;
                            const color = pct >= 65 ? "text-bull" : pct >= 50 ? "text-yellow-400" : "text-gray-400";
                            return (
                              <div className="mt-2 px-2 py-1 rounded-lg bg-white/[0.04] border border-white/[0.07]">
                                <p className={clsx("text-xs font-bold tabular-nums", color)}>✓ {pct}% accurate</p>
                                <p className="text-[11px] text-gray-400 mt-0.5">{acc.total} past predictions</p>
                              </div>
                            );
                          })()}
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
                                <p className={clsx("text-[11px] text-center leading-tight",
                                  isSell ? "text-red-400/80" : "text-gray-400")}>
                                  {isSell ? "AI signals exit — caution" : "No strong entry signal"}
                                </p>
                              )}
                            </div>
                          );
                        })()}

                        {/* Signal feedback thumbs — hidden for now per feedback that
                            it looked annoying; flip SHOW_SIGNAL_FEEDBACK back on to
                            restore (full prompt only before voting, collapses to a
                            one-line confirmation after). */}
                        {SHOW_SIGNAL_FEEDBACK && !isCrypto && user && (
                          existingVote?.vote ? (
                            <p className="text-[11px] text-gray-500 text-center flex items-center justify-center gap-1.5">
                              Feedback recorded
                              <span className={existingVote.vote === 1 ? "text-bull" : "text-red-400"}>
                                {existingVote.vote === 1 ? "👍" : "👎"}
                              </span>
                              <button
                                onClick={() => voteMutation.mutate(existingVote.vote === 1 ? -1 : 1)}
                                disabled={voteMutation.isPending}
                                className="text-gray-600 hover:text-gray-400 underline ml-1"
                              >
                                change
                              </button>
                            </p>
                          ) : (
                            <div className="w-full">
                              <p className="text-[11px] text-gray-400 text-center mb-1 uppercase tracking-widest">Was this signal useful?</p>
                              <div className="flex gap-2 justify-center">
                                {([1, -1] as const).map((v) => (
                                  <button
                                    key={v}
                                    onClick={() => voteMutation.mutate(v)}
                                    disabled={voteMutation.isPending}
                                    className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs border transition-all bg-white/5 border-white/10 text-gray-500 hover:text-white hover:border-white/20"
                                  >
                                    {v === 1 ? "👍" : "👎"}
                                  </button>
                                ))}
                              </div>
                            </div>
                          )
                        )}
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

              {/* Evidence Summary — Epic 005 RCI. Renders nothing while
                  RCI_LIVE_STOCK_ANALYSIS_ENABLED is disabled in Railway,
                  since the backend then never includes
                  recommendation_consolidation on the prediction object. */}
              {!predLoading && <EvidenceSummary prediction={prediction} />}

              {/* Tabs row */}
              <div className="flex gap-2 flex-wrap mt-4 pt-4 border-t border-white/[0.06]">
                {HORIZON_TABS.filter(({ key }) => key !== "fundamentals" || !isCrypto).map(({ key, label }) => (
                  <button key={key} onClick={() => setTab(key)}
                    className={clsx(
                      "flex items-center gap-1.5 px-3.5 py-1.5 text-xs sm:text-sm rounded-lg font-medium transition-all",
                      tab === key
                        ? "bg-brand-500 text-white shadow-lg shadow-brand-500/20"
                        : "bg-white/[0.04] border border-white/[0.07] text-gray-400 hover:text-white hover:bg-white/[0.08]"
                    )}>
                    {key === "backtest" && <FlaskConical size={13} />}
                    {key === "fundamentals" && <BarChart2 size={13} />}
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

      {/* ── PREDICTION VIEW ── */}
      {tab !== "backtest" && tab !== "history" && tab !== "fundamentals" && (
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
              ) : (prediction as any)?.signal === "REJECTED" ? (
                // A hard quality-gate rejection returns a minimal payload —
                // only signal/rejection_reasons/confidence/current_price, no
                // reasoning/trade_levels/technical/etc. "REJECTED" is itself
                // a truthy string, so this must be checked before the
                // generic prediction?.signal branch below, which assumes a
                // full payload and would crash trying to read fields (e.g.
                // .reasoning.slice(...)) that don't exist on this shape.
                <div className="space-y-2 py-2">
                  <p className="text-gray-300 text-sm font-medium">No signal for this horizon</p>
                  <p className="text-gray-500 text-xs">
                    This stock didn&apos;t pass our hard quality screen, so it was never scored at this horizon:
                  </p>
                  <ul className="space-y-1">
                    {((prediction as any).rejection_reasons ?? []).map((r: string, i: number) => (
                      <li key={i} className="text-xs text-gray-400 flex items-start gap-1.5">
                        <span className="shrink-0 mt-1 w-1.5 h-1.5 rounded-full bg-bear" />
                        {r}
                      </li>
                    ))}
                  </ul>
                </div>
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
                      <p className="text-xs text-gray-400 mb-0.5">Confidence</p>
                      <p className={clsx("text-2xl font-black tabular-nums",
                        prediction.signal === "BUY"  ? "text-bull" :
                        prediction.signal === "SELL" ? "text-bear" : "text-neutral"
                      )}>{prediction.confidence}<span className="text-sm font-medium">%</span></p>
                    </div>
                  </div>
                  <ConfidenceMeter
                    value={prediction.confidence}
                    label="Confidence"
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
                          {(prediction as any).fundamental_score?.score != null && (
                            <ConfidenceMeter value={(prediction as any).fundamental_score.score} label="Fundamental Score" />
                          )}
                          {(prediction as any).sentiment_score?.score != null && (
                            <ConfidenceMeter value={(prediction as any).sentiment_score.score} label="News Sentiment Score" />
                          )}
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

          {(prediction as any)?.tracking_only && (
            <div className="bg-blue-500/10 border border-blue-500/30 rounded-xl px-4 py-3 text-sm text-blue-300">
              {(prediction as any).tracking_only_note ?? "Price-tracking instrument — no company fundamentals available, signal reflects technical analysis only."}
            </div>
          )}

          {!(prediction as any)?.tracking_only && ((prediction as any)?.bull_case?.length > 0 || (prediction as any)?.bear_case?.length > 0) && (
            <BullBearCase
              bull={(prediction as any).bull_case ?? []}
              bear={(prediction as any).bear_case ?? []}
            />
          )}

          {attribution && !isCrypto && !(prediction as any)?.tracking_only && (
            <FactorAttributionWaterfall data={attribution} prediction={prediction} />
          )}

          {/* ── Academic Quality Signals card ── */}
          {prediction && !isCrypto && (() => {
            const qf = (prediction as any).quality_factors;
            if (!qf) return null;

            const piotroski   = qf.piotroski;
            const altmanZ     = qf.altman_z;
            const altmanZone  = qf.altman_zone;
            const accruals    = qf.accruals_ratio;
            const bPassed     = qf.buffett_passed;
            const bTotal      = qf.buffett_total ?? 8;
            const bChecklist  = qf.buffett_checklist ?? [];

            const zoneColor = altmanZone === "safe" ? "text-bull" : altmanZone === "grey" ? "text-yellow-400" : altmanZone === "distress" ? "text-bear" : "text-gray-500";
            const zoneLabel = altmanZone === "safe" ? "Safe Zone" : altmanZone === "grey" ? "Grey Zone" : altmanZone === "distress" ? "Distress Zone" : "N/A";
            const accColor  = accruals == null ? "text-gray-500" : accruals < -5 ? "text-bull" : accruals <= 5 ? "text-yellow-400" : "text-bear";
            const accLabel  = accruals == null ? "N/A" : accruals < -5 ? "Excellent" : accruals <= 5 ? "Neutral" : accruals <= 10 ? "Elevated" : "High Risk";

            return (
              <section className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-5">
                <h2 className="text-base font-bold text-white">Academic Quality Signals</h2>
                <p className="text-xs text-gray-500 -mt-3">Piotroski (2000) · Sloan (1996) · Altman (1968) · Buffett/Munger framework</p>

                {/* Top 3 scores */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  {/* Piotroski */}
                  <div className="bg-dark-bg/60 border border-dark-border rounded-xl p-4">
                    <p className="text-[11px] text-gray-500 mb-1">Piotroski F-Score</p>
                    <div className="flex items-end gap-1.5">
                      <span className={clsx("text-2xl font-black tabular-nums",
                        piotroski == null ? "text-gray-600" :
                        piotroski >= 7 ? "text-bull" : piotroski >= 4 ? "text-yellow-400" : "text-bear")}>
                        {piotroski ?? "—"}
                      </span>
                      <span className="text-gray-500 text-sm mb-0.5">/ 9</span>
                    </div>
                    <p className="text-[10px] text-gray-500 mt-1">
                      {piotroski == null ? "Awaiting data" : piotroski >= 7 ? "Strong financial health" : piotroski >= 4 ? "Mixed signals" : "Multiple red flags"}
                    </p>
                  </div>

                  {/* Altman Z */}
                  <div className="bg-dark-bg/60 border border-dark-border rounded-xl p-4">
                    <p className="text-[11px] text-gray-500 mb-1">Altman Z-Score</p>
                    <div className="flex items-end gap-1.5">
                      <span className={clsx("text-2xl font-black tabular-nums", zoneColor)}>
                        {altmanZ ?? "—"}
                      </span>
                    </div>
                    <p className={clsx("text-[10px] mt-1", zoneColor)}>{zoneLabel}</p>
                  </div>

                  {/* Accruals */}
                  <div className="bg-dark-bg/60 border border-dark-border rounded-xl p-4">
                    <p className="text-[11px] text-gray-500 mb-1">Accruals Ratio <span className="text-gray-600">(Sloan)</span></p>
                    <div className="flex items-end gap-1.5">
                      <span className={clsx("text-2xl font-black tabular-nums", accColor)}>
                        {accruals != null ? `${accruals}%` : "—"}
                      </span>
                    </div>
                    <p className={clsx("text-[10px] mt-1", accColor)}>{accLabel}</p>
                  </div>
                </div>

                {/* Buffett / Munger checklist */}
                {bChecklist.length > 0 && (
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <p className="text-sm font-semibold text-white">Buffett / Munger Checklist</p>
                      <span className={clsx("text-sm font-bold tabular-nums",
                        (bPassed ?? 0) >= 6 ? "text-bull" : (bPassed ?? 0) >= 4 ? "text-yellow-400" : "text-bear")}>
                        {bPassed ?? 0}/{bTotal} passed
                      </span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {bChecklist.map((item: any) => (
                        <div key={item.criterion}
                          className={clsx("flex items-start gap-2.5 rounded-lg px-3 py-2.5 border text-xs",
                            item.passed
                              ? "bg-bull/5 border-bull/20 text-gray-200"
                              : "bg-bear/5 border-bear/20 text-gray-400")}>
                          <span className={clsx("mt-0.5 shrink-0 text-sm", item.passed ? "text-bull" : "text-bear")}>
                            {item.passed ? "✓" : "✗"}
                          </span>
                          <div>
                            <p className={clsx("font-semibold leading-tight", item.passed ? "text-white" : "text-gray-400")}>
                              {item.criterion}
                            </p>
                            <p className="text-gray-500 mt-0.5 leading-snug">{item.note}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            );
          })()}

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

          {/* TradingView Chart — reference tool, at the bottom */}
          <div>
            <h2 className="text-lg font-semibold mb-3 text-gray-400">Price Chart <span className="text-sm font-normal text-gray-600">(TradingView · reference only)</span></h2>
            <div className="rounded-2xl border border-dark-border overflow-hidden">
              <TradingViewWidget symbol={symbol} market={isCrypto ? "CRYPTO" : market} height={460} />
            </div>
          </div>
        </>
      )}

      {/* ── FUNDAMENTALS VIEW (screener.in data) ── */}
      {tab === "fundamentals" && market === "US" && (
        <div className="space-y-5">
          {usFundLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-dark-card border border-dark-border rounded-2xl p-5 animate-pulse h-40" />
              ))}
            </div>
          ) : !usFund?.available ? (
            <div className="bg-dark-card border border-dark-border rounded-2xl p-8 text-center text-gray-500 text-sm">
              Fundamental data not available for {symbol}.
              {usFund?.reason && <p className="text-xs text-gray-400 mt-1">{usFund.reason}</p>}
            </div>
          ) : (
            <>
              {/* Key Ratios */}
              <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                <div className="mb-4">
                  <h3 className="font-bold text-white">Key Ratios</h3>
                  {(usFund.sector || usFund.industry) && (
                    <p className="text-xs text-gray-500 mt-0.5">
                      {[usFund.sector, usFund.industry].filter((v, i, arr) => v && arr.indexOf(v) === i).join(" · ")}
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
                  {[
                    { label: "P/E Ratio",       val: usFund.pe_ratio,            fmt: (v: number) => v.toFixed(1) + "×" },
                    { label: "Forward P/E",     val: usFund.forward_pe,          fmt: (v: number) => v.toFixed(1) + "×" },
                    { label: "P/B Ratio",       val: usFund.price_to_book,       fmt: (v: number) => v.toFixed(1) + "×" },
                    { label: "ROE",             val: usFund.roe_pct,             fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "ROA",             val: usFund.roa_pct,             fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "Profit Margin",   val: usFund.profit_margin_pct,   fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "Book Value",      val: usFund.book_value,          fmt: (v: number) => "$" + v.toLocaleString() },
                    { label: "Dividend Yield",  val: usFund.dividend_yield_pct,  fmt: (v: number) => v.toFixed(2) + "%" },
                    { label: "Market Cap",      val: usFund.market_cap,          fmt: (v: number) => "$" + (v / 1e9).toFixed(1) + "B" },
                    { label: "Debt/Equity",     val: usFund.debt_to_equity,      fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "Revenue Growth",  val: usFund.revenue_growth_pct,  fmt: (v: number) => (v >= 0 ? "+" : "") + v.toFixed(1) + "%" },
                    { label: "Earnings Growth", val: usFund.earnings_growth_pct, fmt: (v: number) => (v >= 0 ? "+" : "") + v.toFixed(1) + "%" },
                  ].filter(r => r.val != null).map(({ label, val, fmt }) => (
                    <div key={label} className="bg-dark-bg rounded-xl p-3">
                      <p className="text-[11px] text-gray-500 mb-1">{label}</p>
                      <p className="text-white font-bold text-sm tabular-nums">{fmt(val as number)}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Analyst View */}
              {(usFund.analyst_recommendation || usFund.analyst_target_price != null) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Analyst View</h3>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    {usFund.analyst_recommendation && (
                      <div className="bg-dark-bg rounded-xl p-3">
                        <p className="text-[11px] text-gray-500 mb-1">Consensus</p>
                        <p className="text-white font-bold text-sm capitalize">{usFund.analyst_recommendation.replace("_", " ")}</p>
                      </div>
                    )}
                    {usFund.analyst_target_price != null && (
                      <div className="bg-dark-bg rounded-xl p-3">
                        <p className="text-[11px] text-gray-500 mb-1">Mean Target Price</p>
                        <p className="text-white font-bold text-sm tabular-nums">${usFund.analyst_target_price.toFixed(2)}</p>
                      </div>
                    )}
                    {usFund.analyst_count != null && (
                      <div className="bg-dark-bg rounded-xl p-3">
                        <p className="text-[11px] text-gray-500 mb-1">Analysts Covering</p>
                        <p className="text-white font-bold text-sm tabular-nums">{usFund.analyst_count}</p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Growth */}
              {(usFund.revenue_3y_cagr_pct != null || usFund.profit_3y_cagr_pct != null) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">3-Year CAGR</h3>
                  <div className="grid grid-cols-2 gap-4">
                    {[
                      { label: "Revenue", val: usFund.revenue_3y_cagr_pct },
                      { label: "Net Income", val: usFund.profit_3y_cagr_pct },
                    ].filter(r => r.val != null).map(({ label, val }) => (
                      <div key={label} className="bg-dark-bg rounded-xl p-3">
                        <p className="text-[11px] text-gray-500 mb-1">{label}</p>
                        <p className={clsx("font-bold text-sm tabular-nums", val! >= 0 ? "text-green-400" : "text-red-400")}>
                          {val! >= 0 ? "+" : ""}{val!.toFixed(1)}%
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Ownership */}
              {(usFund.insider_holding_pct != null || usFund.institution_holding_pct != null) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Ownership</h3>
                  <div className="grid grid-cols-2 gap-4">
                    {[
                      { label: "Insider Holding", val: usFund.insider_holding_pct },
                      { label: "Institutional Holding", val: usFund.institution_holding_pct },
                    ].filter(r => r.val != null).map(({ label, val }) => (
                      <div key={label} className="bg-dark-bg rounded-xl p-3">
                        <p className="text-[11px] text-gray-500 mb-1">{label}</p>
                        <p className="text-white font-bold text-sm tabular-nums">{val!.toFixed(1)}%</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Balance Sheet */}
              {(usFund.total_debt_annual_m || usFund.stockholders_equity_annual_m) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Balance Sheet <span className="text-xs text-gray-500 font-normal ml-1">($M · newest → oldest)</span></h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-500 text-xs">
                          <th className="text-left pb-2 font-medium">Type</th>
                          {(usFund.balance_sheet_labels ?? []).map((l: string, i: number) => (
                            <th key={i} className="text-right pb-2 font-medium whitespace-nowrap">{l}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-dark-border">
                        {[
                          { label: "Total Debt", vals: usFund.total_debt_annual_m },
                          { label: "Stockholders Equity", vals: usFund.stockholders_equity_annual_m },
                          { label: "Total Assets", vals: usFund.total_assets_annual_m },
                        ].filter(r => r.vals).map(({ label, vals }) => (
                          <tr key={label}>
                            <td className="py-2 text-gray-400">{label}</td>
                            {vals.map((v: number | null, i: number) => (
                              <td key={i} className="py-2 text-right font-mono tabular-nums font-bold text-gray-200">
                                {v != null ? v.toLocaleString() : "—"}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Cash Flow */}
              {usFund.operating_cf_annual_m && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Annual Cash Flow <span className="text-xs text-gray-500 font-normal ml-1">($M · newest → oldest)</span></h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-500 text-xs">
                          <th className="text-left pb-2 font-medium">Type</th>
                          {(usFund.cashflow_labels ?? []).map((l: string, i: number) => (
                            <th key={i} className="text-right pb-2 font-medium whitespace-nowrap">{l}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-dark-border">
                        <tr>
                          <td className="py-2 text-gray-400">Operating CF</td>
                          {usFund.operating_cf_annual_m.map((v: number | null, i: number) => (
                            <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold", v != null && v >= 0 ? "text-green-400" : "text-red-400")}>
                              {v != null ? v.toLocaleString() : "—"}
                            </td>
                          ))}
                        </tr>
                        {usFund.investing_cf_annual_m && (
                          <tr>
                            <td className="py-2 text-gray-400">Investing CF</td>
                            {usFund.investing_cf_annual_m.map((v: number | null, i: number) => (
                              <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold", v != null && v >= 0 ? "text-green-400" : "text-red-400")}>
                                {v != null ? v.toLocaleString() : "—"}
                              </td>
                            ))}
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <p className="text-xs text-gray-600 text-center">
                Data sourced from Yahoo Finance · Cached 4 hours · All figures in USD
              </p>
            </>
          )}
        </div>
      )}

      {tab === "fundamentals" && market === "IN" && (
        <div className="space-y-5">
          {screenerLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-dark-card border border-dark-border rounded-2xl p-5 animate-pulse h-40" />
              ))}
            </div>
          ) : !screenerFund?.available ? (
            <div className="bg-dark-card border border-dark-border rounded-2xl p-8 text-center text-gray-500 text-sm">
              Fundamental data not available for {symbol} on screener.in.
              {screenerFund?.reason && <p className="text-xs text-gray-400 mt-1">{screenerFund.reason}</p>}
            </div>
          ) : (
            <>
              {/* Key Ratios */}
              <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                <div className="mb-4">
                  <h3 className="font-bold text-white">Key Ratios</h3>
                  {(screenerFund.broad_sector || screenerFund.sector_name) && (
                    <p className="text-xs text-gray-500 mt-0.5">
                      {[screenerFund.broad_sector, screenerFund.sector_name, screenerFund.industry_name]
                        .filter((v, i, arr) => v && arr.indexOf(v) === i)
                        .join(" · ")}
                    </p>
                  )}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
                  {[
                    { label: "P/E Ratio",      val: screenerFund.pe_ratio,           fmt: (v: number) => v.toFixed(1) + "×" },
                    { label: "ROE",            val: screenerFund.roe_pct,            fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "ROCE",           val: screenerFund.roce_pct,           fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "Book Value",     val: screenerFund.book_value,         fmt: (v: number) => "₹" + v.toLocaleString() },
                    { label: "Dividend Yield", val: screenerFund.dividend_yield_pct, fmt: (v: number) => v.toFixed(2) + "%" },
                    { label: "Market Cap",     val: screenerFund.market_cap_cr,      fmt: (v: number) => "₹" + v.toLocaleString() + " Cr" },
                    { label: "Face Value",     val: screenerFund.face_value,         fmt: (v: number) => "₹" + v },
                    // Banking-specific
                    { label: "Net NPA",        val: screenerFund.net_npa_pct,        fmt: (v: number) => v.toFixed(2) + "%" },
                    { label: "Gross NPA",      val: screenerFund.gross_npa_pct,      fmt: (v: number) => v.toFixed(2) + "%" },
                    { label: "NIM",            val: screenerFund.nim_pct,            fmt: (v: number) => v.toFixed(2) + "%" },
                    { label: "CASA Ratio",     val: screenerFund.casa_ratio_pct,     fmt: (v: number) => v.toFixed(1) + "%" },
                    { label: "CAR",            val: screenerFund.capital_adequacy_ratio_pct, fmt: (v: number) => v.toFixed(1) + "%" },
                  ].filter(r => r.val != null).map(({ label, val, fmt }) => (
                    <div key={label} className="bg-dark-bg rounded-xl p-3">
                      <p className="text-[11px] text-gray-500 mb-1">{label}</p>
                      <p className="text-white font-bold text-sm tabular-nums">{fmt(val as number)}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Compounded Growth Rates */}
              {(screenerFund.sales_growth_3y_pct != null || screenerFund.profit_growth_3y_pct != null) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Compounded Growth Rates</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-500 text-xs">
                          <th className="text-left pb-2 font-medium">Metric</th>
                          <th className="text-right pb-2 font-medium">10Y CAGR</th>
                          <th className="text-right pb-2 font-medium">5Y CAGR</th>
                          <th className="text-right pb-2 font-medium">3Y CAGR</th>
                          <th className="text-right pb-2 font-medium">TTM / Latest</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-dark-border">
                        {[
                          { label: "Sales Growth",     k10: "sales_growth_10y_pct",  k5: "sales_growth_5y_pct",  k3: "sales_growth_3y_pct",  klast: "sales_growth_ttm_pct" },
                          { label: "Profit Growth",    k10: "profit_growth_10y_pct", k5: "profit_growth_5y_pct", k3: "profit_growth_3y_pct", klast: "profit_growth_ttm_pct" },
                          { label: "Price CAGR",       k10: "price_cagr_10y_pct",    k5: "price_cagr_5y_pct",    k3: "price_cagr_3y_pct",    klast: "price_cagr_1y_pct" },
                          { label: "Return on Equity", k10: "roe_10y_pct",           k5: "roe_5y_pct",           k3: "roe_3y_pct",           klast: "roe_1y_pct" },
                        ].map(({ label, k10, k5, k3, klast }) => {
                          const v10 = screenerFund[k10];
                          const v5 = screenerFund[k5];
                          const v3 = screenerFund[k3];
                          const vlast = screenerFund[klast];
                          if (v10 == null && v5 == null && v3 == null && vlast == null) return null;
                          const color = (v: number | null) => v == null ? "text-gray-600" : v >= 0 ? "text-green-400" : "text-red-400";
                          const fmt = (v: number | null) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
                          return (
                            <tr key={label}>
                              <td className="py-2 text-gray-300 font-medium">{label}</td>
                              <td className={clsx("py-2 text-right font-mono font-bold tabular-nums", color(v10))}>{fmt(v10)}</td>
                              <td className={clsx("py-2 text-right font-mono font-bold tabular-nums", color(v5))}>{fmt(v5)}</td>
                              <td className={clsx("py-2 text-right font-mono font-bold tabular-nums", color(v3))}>{fmt(v3)}</td>
                              <td className={clsx("py-2 text-right font-mono font-bold tabular-nums", color(vlast))}>{fmt(vlast)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Pros & Cons */}
              {((screenerFund.screener_pros?.length ?? 0) > 0 || (screenerFund.screener_cons?.length ?? 0) > 0) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-1">Pros &amp; Cons</h3>
                  <p className="text-[11px] text-gray-500 mb-4">Machine-generated checklist highlights from screener.in — exercise caution, do your own analysis.</p>
                  <div className="grid sm:grid-cols-2 gap-4">
                    {(screenerFund.screener_pros?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-green-400 mb-2">Pros</p>
                        <ul className="space-y-1.5">
                          {screenerFund.screener_pros.map((p: string, i: number) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-gray-300">
                              <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-green-400" />
                              {p}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {(screenerFund.screener_cons?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-red-400 mb-2">Cons</p>
                        <ul className="space-y-1.5">
                          {screenerFund.screener_cons.map((c: string, i: number) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-gray-300">
                              <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-red-400" />
                              {c}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Shareholding Pattern */}
              {(screenerFund.promoter_holding_pct != null || screenerFund.fii_holding_pct != null) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Shareholding Pattern <span className="text-xs text-gray-500 font-normal ml-1">(Latest Quarter)</span></h3>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    {[
                      { label: "Promoters",   val: screenerFund.promoter_holding_pct, warn: (v: number) => v < 25, warnMsg: "Low" },
                      { label: "FII",         val: screenerFund.fii_holding_pct },
                      { label: "DII / MF",    val: screenerFund.dii_holding_pct },
                      { label: "Public",      val: screenerFund.public_holding_pct },
                      { label: "Pledge %",    val: screenerFund.promoter_pledge_pct, warn: (v: number) => v > 10, warnMsg: "High" },
                    ].filter(r => r.val != null).map(({ label, val, warn, warnMsg }) => {
                      const isWarn = warn && val != null && warn(val as number);
                      return (
                        <div key={label} className="bg-dark-bg rounded-xl p-3">
                          <p className="text-[11px] text-gray-500 mb-1">{label}</p>
                          <p className={clsx("font-bold text-sm tabular-nums", isWarn ? "text-yellow-400" : "text-white")}>
                            {(val as number).toFixed(1)}%
                            {isWarn && <span className="text-[10px] ml-1 text-yellow-400">⚠ {warnMsg}</span>}
                          </p>
                          {/* Mini bar */}
                          <div className="mt-1.5 h-1 rounded-full bg-dark-border overflow-hidden">
                            <div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.min(val as number, 100)}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Quarterly Results */}
              {screenerFund.quarterly_revenue_cr && screenerFund.quarterly_revenue_cr.length > 0 && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Quarterly Results <span className="text-xs text-gray-500 font-normal ml-1">(₹ Crore · newest → oldest)</span></h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-500 text-xs">
                          <th className="text-left pb-2 font-medium">Quarter</th>
                          {[...(screenerFund.quarterly_revenue_cr ?? [])].reverse().map((_: number, i: number) => {
                            const labels: string[] = screenerFund.quarterly_labels ?? [];
                            const reversed = [...labels].reverse();
                            const label = reversed[i] ?? `Q${i + 1}`;
                            return (
                              <th key={i} className="text-right pb-2 font-medium whitespace-nowrap">{label}</th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-dark-border">
                        <tr>
                          <td className="py-2 text-gray-400">Revenue</td>
                          {[...(screenerFund.quarterly_revenue_cr ?? [])].reverse().map((v: number, i: number) => (
                            <td key={i} className="py-2 text-right font-mono text-white tabular-nums">
                              {v != null ? v.toLocaleString() : "—"}
                            </td>
                          ))}
                        </tr>
                        {screenerFund.quarterly_pat_cr && (
                          <tr>
                            <td className="py-2 text-gray-400">Net Profit</td>
                            {[...(screenerFund.quarterly_pat_cr ?? [])].reverse().map((v: number, i: number) => (
                              <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold", v >= 0 ? "text-green-400" : "text-red-400")}>
                                {v != null ? v.toLocaleString() : "—"}
                              </td>
                            ))}
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Cash Flow */}
              {(screenerFund.reserves_annual_cr || screenerFund.borrowings_annual_cr) && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Balance Sheet <span className="text-xs text-gray-500 font-normal ml-1">(₹ Crore · newest → oldest)</span></h3>
                  {(() => {
                    const bsLabels = [...(screenerFund.balance_sheet_labels ?? [])].reverse();
                    const reserves = screenerFund.reserves_annual_cr ? [...screenerFund.reserves_annual_cr].reverse() : null;
                    const borrowings = screenerFund.borrowings_annual_cr ? [...screenerFund.borrowings_annual_cr].reverse() : null;
                    const totalLiab = screenerFund.total_liabilities_annual_cr ? [...screenerFund.total_liabilities_annual_cr].reverse() : null;
                    const colCount = (reserves ?? borrowings ?? totalLiab ?? []).length;
                    const toFY = (raw: string) => {
                      const m = raw.match(/(\d{4})$/);
                      if (!m) return raw;
                      const y = parseInt(m[1]);
                      return `FY${String(y - 1).slice(2)}-${String(y).slice(2)}`;
                    };
                    const row = (label: string, vals: number[] | null, color = false) => vals && (
                      <tr>
                        <td className="py-2 text-gray-400">{label}</td>
                        {vals.map((v, i) => (
                          <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold",
                            color ? (v >= 0 ? "text-green-400" : "text-red-400") : "text-gray-200")}>
                            {v != null ? v.toLocaleString() : "—"}
                          </td>
                        ))}
                      </tr>
                    );
                    return (
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-gray-500 text-xs">
                              <th className="text-left pb-2 font-medium">Type</th>
                              {Array.from({ length: colCount }).map((_, i) => (
                                <th key={i} className="text-right pb-2 font-medium whitespace-nowrap">{toFY(bsLabels[i] ?? `FY${i + 1}`)}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-dark-border">
                            {row("Reserves", reserves)}
                            {row("Borrowings", borrowings)}
                            {row("Total Liabilities", totalLiab)}
                          </tbody>
                        </table>
                        {screenerFund.debt_to_equity_pct != null && (
                          <p className="text-[11px] text-gray-500 mt-3">
                            Debt-to-Equity (latest): <span className="text-gray-300 font-mono font-medium">{screenerFund.debt_to_equity_pct.toFixed(1)}%</span>
                          </p>
                        )}
                      </div>
                    );
                  })()}
                </div>
              )}

              {screenerFund.operating_cf_annual_cr && screenerFund.operating_cf_annual_cr.length > 0 && (
                <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
                  <h3 className="font-bold text-white mb-4">Annual Cash Flow <span className="text-xs text-gray-500 font-normal ml-1">(₹ Crore · newest → oldest)</span></h3>
                  {(() => {
                    const cfLabels = [...(screenerFund.cashflow_labels ?? [])].reverse();
                    const opCf = [...(screenerFund.operating_cf_annual_cr ?? [])].reverse();
                    const invCf = screenerFund.investing_cf_annual_cr ? [...screenerFund.investing_cf_annual_cr].reverse() : null;
                    // Convert "Mar 2026" → "FY25-26", "Mar 2025" → "FY24-25", etc.
                    const toFY = (raw: string) => {
                      const m = raw.match(/(\d{4})$/);
                      if (!m) return raw;
                      const y = parseInt(m[1]);
                      return `FY${String(y - 1).slice(2)}-${String(y).slice(2)}`;
                    };
                    return (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-500 text-xs">
                          <th className="text-left pb-2 font-medium">Type</th>
                          {opCf.map((_: number, i: number) => {
                            const label = toFY(cfLabels[i] ?? `FY${i + 1}`);
                            return <th key={i} className="text-right pb-2 font-medium whitespace-nowrap">{label}</th>;
                          })}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-dark-border">
                        <tr>
                          <td className="py-2 text-gray-400">Operating CF</td>
                          {opCf.map((v: number, i: number) => (
                            <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold", v >= 0 ? "text-green-400" : "text-red-400")}>
                              {v != null ? v.toLocaleString() : "—"}
                            </td>
                          ))}
                        </tr>
                        {invCf && (
                          <tr>
                            <td className="py-2 text-gray-400">Investing CF</td>
                            {invCf.map((v: number, i: number) => (
                              <td key={i} className={clsx("py-2 text-right font-mono tabular-nums font-bold", v >= 0 ? "text-green-400" : "text-red-400")}>
                                {v != null ? v.toLocaleString() : "—"}
                              </td>
                            ))}
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                    );
                  })()}
                </div>
              )}

              <p className="text-xs text-gray-600 text-center">
                Data sourced from screener.in · Cached 4 hours · All figures in ₹ Crore unless noted
              </p>
            </>
          )}
        </div>
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
          currentPrice={quote?.price ?? prediction.current_price ?? 0}
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
