"use client";
import { useState, useEffect } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import { fetchTopMovers, fetchQuote, api } from "@/utils/api";
import { TrendingUp, TrendingDown, RefreshCw, Wifi } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { IndexBar } from "@/components/IndexBar";
import { StockContextMenu } from "@/components/StockContextMenu";
import { useAuthGuard } from "@/hooks/useAuthGuard";
import { useAuth } from "@/lib/AuthContext";
import { useMarketPreference } from "@/hooks/useMarketPreference";
const POPULAR_US     = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "JPM", "META", "AMZN"];
const POPULAR_IN     = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "WIPRO", "BAJFINANCE", "ICICIBANK", "ADANIENT"];
const POPULAR_CRYPTO = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"];

// Static crypto "movers" — prices fetched on the stock page when clicked
const CRYPTO_CARDS = [
  { symbol: "BTC",  name: "Bitcoin" },
  { symbol: "ETH",  name: "Ethereum" },
  { symbol: "BNB",  name: "BNB" },
  { symbol: "SOL",  name: "Solana" },
  { symbol: "XRP",  name: "XRP" },
  { symbol: "DOGE", name: "Dogecoin" },
  { symbol: "ADA",  name: "Cardano" },
  { symbol: "AVAX", name: "Avalanche" },
  { symbol: "LINK", name: "Chainlink" },
  { symbol: "DOT",  name: "Polkadot" },
];

type DashMarket = "US" | "IN" | "CRYPTO" | "COMMODITY";

const MARKET_TABS: { key: DashMarket; label: string }[] = [
  { key: "IN",        label: "🇮🇳 IN" },
  { key: "US",        label: "🇺🇸 US" },
  { key: "CRYPTO",    label: "₿ Crypto" },
  { key: "COMMODITY", label: "🥇 Commodities" },
];

// Tracking-only commodity ETFs — no AI signal, just live price (see
// prediction_engine.py's TRACKING_ONLY_SYMBOLS for why).
const COMMODITY_CARDS: { symbol: string; name: string; market: "US" | "IN" }[] = [
  { symbol: "GLD",        name: "Gold (SPDR ETF)",          market: "US" },
  { symbol: "SLV",        name: "Silver (iShares ETF)",     market: "US" },
  { symbol: "GOLDBEES",   name: "Gold (Nippon India ETF)",  market: "IN" },
  { symbol: "SILVERBEES", name: "Silver (Nippon India ETF)", market: "IN" },
];

export default function Dashboard() {
  useAuthGuard();
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const [market, setMarket] = useMarketPreference(["IN", "US", "CRYPTO", "COMMODITY"] as const, "IN");

  const { data: movers, isLoading: moversLoading, isFetching: moversFetching, dataUpdatedAt: moversUpdatedAt } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market as any),
    enabled: market !== "CRYPTO",
    refetchInterval: 120_000,
    staleTime: 115_000,
    refetchOnWindowFocus: false,
  });

  const { data: cryptoMovers, isLoading: cryptoLoading, isFetching: cryptoFetching, dataUpdatedAt: cryptoUpdatedAt } = useQuery({
    queryKey: ["crypto-movers"],
    queryFn: () => api.get<{ movers: { symbol: string; name: string; price: number | null; change_pct: number }[] }>("/api/screener/crypto-movers").then(r => r.data),
    enabled: market === "CRYPTO",
    refetchInterval: 120_000,
    staleTime: 115_000,
    refetchOnWindowFocus: false,
  });

  const commodityQueries = useQueries({
    queries: COMMODITY_CARDS.map(c => ({
      queryKey: ["quote", c.symbol, c.market],
      queryFn: () => fetchQuote(c.symbol, c.market),
      enabled: market === "COMMODITY",
      refetchInterval: 120_000,
      staleTime: 115_000,
    })),
  });
  const commodityLoading = market === "COMMODITY" && commodityQueries.some(q => q.isLoading);
  const commodityUpdatedAt = Math.max(0, ...commodityQueries.map(q => q.data ? Date.now() : 0));

  const lastUpdated = market === "CRYPTO" ? cryptoUpdatedAt : market === "COMMODITY" ? commodityUpdatedAt : moversUpdatedAt;
  const isFetching = market === "CRYPTO" ? cryptoFetching : market === "COMMODITY" ? false : moversFetching;
  const isFirstLoad = market === "COMMODITY" ? commodityLoading : (market === "CRYPTO" ? cryptoLoading : moversLoading) && !lastUpdated;

  const { data: watchlistData } = useQuery({
    queryKey: ["watchlist-quick", userId],
    queryFn: () => api.get<{ items: { symbol: string; market: string }[] }>(`/api/watchlist/${userId}`).then(r => r.data),
    enabled: !!userId,
    staleTime: 60_000,
  });

  const currency = market === "IN" ? "₹" : "$";
  const fallbackSymbols = market === "CRYPTO" ? POPULAR_CRYPTO : market === "IN" ? POPULAR_IN : POPULAR_US;
  const watchlistForMarket = watchlistData?.items?.filter(i => i.market === market).map(i => i.symbol) ?? [];
  const quickSymbols = watchlistForMarket.length > 0 ? watchlistForMarket : fallbackSymbols;
  const isUsingWatchlist = watchlistForMarket.length > 0;

  return (
    <div className="space-y-6">

      {/* Market Overview — layout matches Market Heatmap header style */}
      <div className="space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">Market Overview</h1>
            <p className="text-sm text-gray-400 mt-1">Live indices, top movers &amp; market sentiment</p>
          </div>
          <div className="flex items-center gap-3 ml-auto flex-wrap justify-end min-w-0 max-w-full">
            {/* Live status / loading badge */}
            <div className={clsx(
              "flex items-center gap-1.5 text-xs rounded-lg px-3 py-1.5 border transition-all",
              isFirstLoad
                ? "bg-brand-500/10 border-brand-500/30 text-brand-400"
                : isFetching
                  ? "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"
                  : "bg-dark-card border-dark-border text-gray-500"
            )}>
              {isFirstLoad || isFetching
                ? <RefreshCw size={11} className="animate-spin" />
                : <Wifi size={11} className="text-green-500" />
              }
              {isFirstLoad
                ? "Fetching market data…"
                : isFetching
                  ? "Refreshing…"
                  : lastUpdated
                    ? `Updated ${new Date(lastUpdated).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })}`
                    : "Live"
              }
            </div>
            <div className="flex items-center gap-0.5 overflow-x-auto scrollbar-hide min-w-0 max-w-full bg-dark-card border border-dark-border rounded-lg p-0.5">
              {MARKET_TABS.map(({ key, label }) => (
                <button key={key} onClick={() => setMarket(key)}
                  className={clsx("shrink-0 whitespace-nowrap text-xs px-3 py-1.5 rounded-md font-medium transition-colors",
                    market === key ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white")}>
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {/* Index bar */}
        <div className="bg-dark-card border border-dark-border rounded-xl px-4 overflow-x-auto scrollbar-hide">
          {market === "CRYPTO"
            ? <p className="text-xs text-gray-500 py-3">Live index data unavailable for crypto</p>
            : market === "COMMODITY"
              ? <p className="text-xs text-gray-500 py-3">Tracking only — no AI signal for commodity ETFs (see each card&apos;s stock page for details)</p>
              : <IndexBar market={market as any} />
          }
        </div>
      </div>

      {/* Horizon info cards */}
      {market !== "COMMODITY" && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            { label: "Short Term",  period: "1–10 Days",      desc: "Technicals, momentum, volume, news sentiment",              border: "border-green-500/40",  text: "text-green-400" },
            { label: "Medium Term", period: "1–3 Months",     desc: "Earnings, sector rotation, macro trends",                   border: "border-yellow-500/40", text: "text-yellow-400" },
            { label: "Long Term",   period: "6M – 3 Years",   desc: "Fundamentals, management quality, government policy",       border: "border-purple-500/40", text: "text-purple-400" },
          ].map(({ label, period, desc, border, text }) => (
            <div key={label} className={`bg-dark-card border ${border} rounded-xl p-4`}>
              <div className="flex items-baseline gap-2 mb-1.5">
                <p className={`text-sm font-bold ${text}`}>{label}</p>
                <p className="text-xs text-gray-500">{period}</p>
              </div>
              <p className="text-xs text-gray-300">{desc}</p>
            </div>
          ))}
        </div>
      )}

      {/* Quick Access */}
      {market !== "COMMODITY" && (
      <section>
        <div className="mb-2 space-y-0.5">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Quick Access</h2>
          {isUsingWatchlist
            ? <span className="text-[10px] text-brand-500 font-medium">From your watchlist</span>
            : <span className="text-[10px] text-gray-500">Popular · Add to watchlist to personalise</span>
          }
        </div>
        <div className="flex flex-wrap gap-2">
          {quickSymbols.map((sym) => (
            <Link key={sym} href={`/stock/${sym}?market=${market}`}
              className="px-4 py-2 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/60 text-sm font-mono font-bold text-white transition-colors">
              {sym}
            </Link>
          ))}
        </div>
      </section>
      )}

      {/* Top Movers / Crypto / Commodity grid */}
      {market === "COMMODITY" ? (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Commodities</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {COMMODITY_CARDS.map((c, i) => {
              const q = commodityQueries[i]?.data;
              const cur = c.market === "IN" ? "₹" : "$";
              return (
                <Link key={c.symbol} href={`/stock/${c.symbol}?market=${c.market}`}
                  className="p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors">
                  <p className="font-mono font-bold text-white">{c.symbol}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{c.name}</p>
                  {q?.price ? (
                    <>
                      <p className="text-base font-bold mt-1">
                        {cur}{Number(q.price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </p>
                      {q.change_pct != null && (
                        <div className={clsx("flex items-center gap-1 text-xs font-medium mt-0.5",
                          q.change_pct >= 0 ? "text-bull" : "text-bear")}>
                          {q.change_pct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                          {q.change_pct >= 0 ? "+" : ""}{q.change_pct}%
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-gray-500 mt-2">Loading…</p>
                  )}
                </Link>
              );
            })}
          </div>
        </section>
      ) : market === "CRYPTO" ? (
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Top Cryptocurrencies</h2>
          {cryptoLoading ? (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="h-24 rounded-xl bg-dark-card animate-pulse" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {(cryptoMovers?.movers ?? CRYPTO_CARDS).map((c) => (
                <Link key={c.symbol} href={`/stock/${c.symbol}?market=CRYPTO`}
                  className="p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors">
                  <p className="font-mono font-bold text-white">{c.symbol}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{"name" in c ? (c as any).name : ""}</p>
                  {"price" in c && (c as any).price ? (
                    <>
                      <p className="text-base font-bold mt-1">
                        ${Number((c as any).price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </p>
                      <div className={clsx("flex items-center gap-1 text-xs font-medium mt-0.5",
                        (c as any).change_pct >= 0 ? "text-bull" : "text-bear")}>
                        {(c as any).change_pct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                        {(c as any).change_pct >= 0 ? "+" : ""}{(c as any).change_pct}%
                      </div>
                    </>
                  ) : (
                    <p className="text-xs text-gray-500 mt-2">Loading…</p>
                  )}
                </Link>
              ))}
            </div>
          )}
        </section>
      ) : moversLoading ? (
        <div className="space-y-6">
          {["Top Gainers", "Top Losers"].map((label) => (
            <section key={label}>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">{label}</h2>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className="h-24 rounded-xl bg-dark-card animate-pulse" />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Top Gainers */}
          <section>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
                Top Gainers · {market === "IN" ? "NSE (Nifty 100)" : "NYSE / NASDAQ (Large Cap)"}
              </h2>
              {movers?.market_open
                ? <span className="text-[10px] text-bull font-medium bg-bull/10 px-2 py-0.5 rounded-full">Live</span>
                : <span className="text-[10px] text-gray-400 font-medium bg-white/5 px-2 py-0.5 rounded-full">Market Closed · Last Session</span>
              }
            </div>
            <p className="text-[11px] text-gray-600 mb-3">
              Ranked by today&apos;s price change only — not an AI recommendation or signal.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[...(movers?.gainers ?? [])].map((m: any) => (
                <StockContextMenu key={m.symbol} symbol={m.symbol} market={market}>
                  <Link href={`/stock/${m.symbol}?market=${market}`}
                    className="block p-4 rounded-xl bg-dark-card border border-bull/20 hover:border-bull/50 transition-colors">
                    <p className="font-mono font-bold text-white text-sm">{m.symbol}</p>
                    {m.name && <p className="text-[10px] text-gray-500 mt-0.5 truncate">{m.name}</p>}
                    <p className="text-base font-bold mt-1.5">{currency}{m.price?.toLocaleString() ?? "—"}</p>
                    <div className="flex items-center gap-1 text-sm font-medium mt-1 text-bull">
                      <TrendingUp size={14} />+{m.change_pct ?? 0}%
                    </div>
                  </Link>
                </StockContextMenu>
              ))}
            </div>
          </section>

          {/* Top Losers */}
          <section>
            <div className="flex items-center gap-2 mb-3">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
                Top Losers · {market === "IN" ? "NSE (Nifty 100)" : "NYSE / NASDAQ (Large Cap)"}
              </h2>
              {movers?.market_open
                ? <span className="text-[10px] text-bear font-medium bg-bear/10 px-2 py-0.5 rounded-full">Live</span>
                : <span className="text-[10px] text-gray-400 font-medium bg-white/5 px-2 py-0.5 rounded-full">Market Closed · Last Session</span>
              }
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[...(movers?.losers ?? [])].map((m: any) => (
                <StockContextMenu key={m.symbol} symbol={m.symbol} market={market}>
                  <Link href={`/stock/${m.symbol}?market=${market}`}
                    className="block p-4 rounded-xl bg-dark-card border border-bear/20 hover:border-bear/50 transition-colors">
                    <p className="font-mono font-bold text-white text-sm">{m.symbol}</p>
                    {m.name && <p className="text-[10px] text-gray-500 mt-0.5 truncate">{m.name}</p>}
                    <p className="text-base font-bold mt-1.5">{currency}{m.price?.toLocaleString() ?? "—"}</p>
                    <div className="flex items-center gap-1 text-sm font-medium mt-1 text-bear">
                      <TrendingDown size={14} />{m.change_pct ?? 0}%
                    </div>
                  </Link>
                </StockContextMenu>
              ))}
            </div>
          </section>
        </div>
      )}

    </div>
  );
}
