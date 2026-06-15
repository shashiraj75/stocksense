"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "next/navigation";
import { api, fetchQuote, fetchPrediction, fetchNews, Market, Horizon } from "@/utils/api";
import { TradingViewWidget } from "@/components/TradingViewWidget";
import { SignalBadge } from "@/components/SignalBadge";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";
import { NewsCard } from "@/components/NewsCard";
import clsx from "clsx";
import { ArrowUpRight, ArrowDownRight, FlaskConical, CheckCircle, XCircle } from "lucide-react";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";

type Tab = Horizon | "backtest";

const HORIZON_TABS: { key: Tab; label: string }[] = [
  { key: "short", label: "Short Term" },
  { key: "medium", label: "Medium Term" },
  { key: "long", label: "Long Term" },
  { key: "backtest", label: "Backtest" },
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
  const market = isCrypto ? "US" : (rawMarket as Market); // backend only knows US/IN
  const currency = market === "IN" ? "₹" : "$";

  const [tab, setTab] = useState<Tab>("short");
  const [btHorizon, setBtHorizon] = useState<Horizon>("short");
  const [btRunning, setBtRunning] = useState(false);
  const [btData, setBtData] = useState<BacktestResult | null>(null);
  const [btError, setBtError] = useState("");

  const horizon = tab === "backtest" ? "short" : (tab as Horizon);

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

  const { data: prediction, isLoading: predLoading, refetch: refetchPrediction, isError: predError } = useQuery({
    queryKey: ["prediction", symbol, isCrypto ? "CRYPTO" : market, horizon],
    queryFn: () => fetchPrediction(symbol, isCrypto ? "CRYPTO" as any : market, horizon),
    enabled: tab !== "backtest",
    retry: 3,
    retryDelay: (attempt) => Math.min(attempt * 8000, 24000),
    placeholderData: (prev) => prev,  // show previous horizon data while new one loads
    staleTime: 14 * 60_000,  // match backend 15-min cache — no redundant refetches
    refetchOnWindowFocus: false,
  });

  const { data: news } = useQuery({
    queryKey: ["news", symbol, isCrypto ? "US" : market],
    queryFn: () => fetchNews(symbol, isCrypto ? "US" : market),
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
    <div className="space-y-6">
      {!isCrypto && <MarketDisclaimer market={market} />}
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-3xl font-bold font-mono">{symbol}</h1>
            <span className="text-xs bg-dark-card border border-dark-border px-2 py-0.5 rounded text-gray-400">
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
          {(quote || cryptoQuote || (isCrypto && prediction?.current_price)) && (
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <span className="text-4xl font-bold">
                {isCrypto
                  ? `$${(cryptoQuote?.price ?? prediction?.current_price ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                  : `${currency}${quote?.price.toLocaleString()}`}
              </span>
              {isCrypto && cryptoQuote?.change_pct != null && (
                <span className={clsx("flex items-center gap-1 text-lg font-semibold",
                  cryptoQuote.change_pct >= 0 ? "text-bull" : "text-bear")}>
                  {cryptoQuote.change_pct >= 0 ? <ArrowUpRight /> : <ArrowDownRight />}
                  {cryptoQuote.change_pct >= 0 ? "+" : ""}{cryptoQuote.change_pct}%
                </span>
              )}
              {!isCrypto && (() => {
                const chg = quote?.change;
                const pct = quote?.change_pct;
                if (chg == null || pct == null) return null;
                return (
                  <span className={clsx("flex items-center gap-1 text-lg font-semibold",
                    chg >= 0 ? "text-bull" : "text-bear")}>
                    {chg >= 0 ? <ArrowUpRight /> : <ArrowDownRight />}
                    {chg >= 0 ? "+" : ""}{chg} ({pct}%)
                  </span>
                );
              })()}
              {/* Live indicator */}
              <div className="flex items-center gap-1.5 ml-1">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                </span>
                <span className="text-xs text-gray-500">
                  Live · {new Date(isCrypto ? cryptoUpdatedAt : quoteUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true })}
                </span>
              </div>
            </div>
          )}
        </div>
        {tab !== "backtest" && prediction && !predLoading && (
          <SignalBadge signal={prediction.signal} size="lg" />
        )}
      </div>

      {/* Tabs — Short / Medium / Long / Backtest for all markets */}
      <div className="flex gap-2 flex-wrap">
        {HORIZON_TABS.map(({ key, label }) => (
          <button key={key} onClick={() => setTab(key)}
            className={clsx(
              "flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
              tab === key ? "bg-brand-500 text-white"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            )}>
            {key === "backtest" && <FlaskConical size={14} />}
            {label}
          </button>
        ))}
      </div>

      {isCrypto && (
        <div className="flex items-center justify-between bg-dark-card border border-dark-border rounded-xl px-4 py-2.5 text-xs text-gray-500">
          <span>Chart: <span className="text-white">TradingView</span> · Binance USDT · Predictions: technicals + volume + sentiment</span>
        </div>
      )}

      {/* ── PREDICTION VIEW ── */}
      {tab !== "backtest" && (
        <>
          {/* Trade Levels — shown above prediction panels */}
          {prediction?.signal && (prediction as any).trade_levels && (
            <div className="bg-dark-card border border-dark-border rounded-2xl p-6">
              <h2 className="font-bold text-lg mb-4">Trade Levels <span className="text-xs font-normal text-gray-500 ml-2">({tab} term)</span></h2>
              {(() => {
                  const tl = (prediction as any).trade_levels;
                  const sig = prediction.signal;
                  const cp: number | null = prediction.current_price ?? null;
                  const pctFrom = (price: number) => cp ? ((price - cp) / cp * 100).toFixed(1) : null;
                  const entryLabel = sig === "BUY" ? "Buy Zone" : sig === "SELL" ? "Sell Zone" : "Watch Zone";
                  const entryColor = sig === "SELL" ? "text-bear" : sig === "HOLD" ? "text-gray-300" : "text-bull";
                  const entryBg    = sig === "SELL" ? "bg-bear/10 border-bear/30" : sig === "HOLD" ? "bg-dark-border/60 border-dark-border" : "bg-bull/10 border-bull/30";
                  const rrGood     = tl.risk_reward_ratio >= 1.5;
                  const trailPct: number | null = tl.trailing_stop_pct ?? null;
                  const gridCols = trailPct
                    ? "grid-cols-2 md:grid-cols-3 lg:grid-cols-5"
                    : "grid-cols-2 md:grid-cols-4";
                  return (
                    <div className={`grid ${gridCols} gap-4`}>
                      <div className={`rounded-xl border p-4 ${entryBg}`}>
                        <p className="text-xs text-gray-400 mb-1">{entryLabel}</p>
                        <p className={`font-mono font-bold text-sm ${entryColor}`}>
                          {currency}{tl.entry_low.toLocaleString()} – {currency}{tl.entry_high.toLocaleString()}
                        </p>
                      </div>
                      <div className="rounded-xl border p-4 bg-bull/10 border-bull/30">
                        <p className="text-xs text-gray-400 mb-1">Take Profit</p>
                        <p className="font-mono font-bold text-sm text-bull">
                          {currency}{tl.take_profit.toLocaleString()}
                          {pctFrom(tl.take_profit) && <span className="ml-2 text-xs font-normal">+{pctFrom(tl.take_profit)}%</span>}
                        </p>
                      </div>
                      <div className="rounded-xl border p-4 bg-bear/10 border-bear/30">
                        <p className="text-xs text-gray-400 mb-1">Stop Loss</p>
                        <p className="font-mono font-bold text-sm text-bear">
                          {currency}{tl.stop_loss.toLocaleString()}
                          {pctFrom(tl.stop_loss) && <span className="ml-2 text-xs font-normal">{pctFrom(tl.stop_loss)}%</span>}
                        </p>
                        <p className="text-xs text-gray-500 mt-0.5">Fixed entry stop</p>
                      </div>
                      {trailPct && (
                        <div className="rounded-xl border p-4 bg-orange-500/10 border-orange-500/30">
                          <p className="text-xs text-gray-400 mb-1">Trailing Stop</p>
                          <p className="font-mono font-bold text-sm text-orange-400">
                            {trailPct}% below peak
                          </p>
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
                  );
                })()}
              {prediction.signal === "HOLD" && (
                <p className="text-xs text-yellow-600/70 mt-3">
                  HOLD signal — no strong directional edge on this horizon. Try switching to Medium or Long Term for a clearer signal.
                </p>
              )}
              <p className="text-xs text-gray-500 mt-1">
                Based on 14-day ATR · Not financial advice — always set your own risk limits.
              </p>
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-6">
            <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-3">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-bold text-lg">AI Prediction — {tab} term</h2>
                {prediction?.target_price && prediction?.current_price && (() => {
                  const pct = ((prediction.target_price - prediction.current_price) / prediction.current_price) * 100;
                  const up = pct >= 0;
                  return (
                    <span className="shrink-0 text-right">
                      <span className="text-gray-400 text-sm mr-1">Target Price:</span>
                      <span className="font-mono font-bold text-base">{currency}{prediction.target_price.toLocaleString()}</span>
                      <span className={`ml-2 text-sm font-medium ${up ? "text-bull" : "text-bear"}`}>
                        {up ? "+" : ""}{pct.toFixed(1)}%
                      </span>
                    </span>
                  );
                })()}
              </div>
              {predLoading ? (
                <div className="space-y-3">
                  <p className="text-xs text-gray-500 animate-pulse">Fetching prediction — backend may be waking up, please wait…</p>
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-5 bg-dark-border rounded animate-pulse" />
                  ))}
                </div>
              ) : (prediction as any)?.error ? (
                <p className="text-red-400 text-sm">{(prediction as any).error}</p>
              ) : prediction?.signal ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-400 text-sm">Signal</span>
                    <SignalBadge signal={prediction.signal} />
                  </div>
                  <ConfidenceMeter value={prediction.confidence} label="Confidence" />
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
            </div>

            <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-4">
              <h2 className="font-bold text-lg">{isCrypto ? "Signal Breakdown" : "Key Stats"}</h2>
              {!isCrypto && quote && (
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
                <div className={!isCrypto ? "border-t border-dark-border pt-4 space-y-2" : "space-y-2"}>
                  <p className="text-gray-400 text-sm mb-2">Score Breakdown</p>
                  {isCrypto ? (
                    <>
                      <ConfidenceMeter value={(prediction.technical as any)?.score ?? 50} label="Technical Score" />
                      <ConfidenceMeter value={(prediction as any).fear_greed?.score ?? 50} label="Market Sentiment (Fear/Greed)" />
                      <ConfidenceMeter value={prediction.sentiment_score?.score ?? 50} label="News Sentiment" />
                      <ConfidenceMeter value={(prediction as any).on_chain_proxy?.score ?? 50} label="Volume Analysis" />
                    </>
                  ) : (
                    <>
                      <ConfidenceMeter value={prediction.fundamental_score.score} label="Fundamental Score" />
                      <ConfidenceMeter value={prediction.sentiment_score.score} label="News Sentiment Score" />
                      <ConfidenceMeter
                        value={prediction.technical?.rsi ? Math.min(100, Math.round(prediction.technical.rsi)) : 50}
                        label="RSI"
                      />
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* TradingView Chart */}
          <div className="rounded-2xl border border-dark-border">
            <TradingViewWidget symbol={symbol} market={isCrypto ? "CRYPTO" : market} height={480} />
          </div>

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
    </div>
  );
}
