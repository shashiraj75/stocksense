"use client";
import { useState } from "react";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Market, fetchQuote } from "@/utils/api";
import { Trash2, Plus, TrendingUp, TrendingDown, Minus } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";

const USER_ID = "default";

interface WatchlistItem { symbol: string; market: Market; notes: string; }

const getWatchlist = () => api.get<{ items: WatchlistItem[] }>(`/api/watchlist/${USER_ID}`).then(r => r.data);
const addToWatchlist = (item: WatchlistItem) => api.post(`/api/watchlist/${USER_ID}`, item).then(r => r.data);
const removeFromWatchlist = (symbol: string) => api.delete(`/api/watchlist/${USER_ID}/${symbol}`).then(r => r.data);

export default function WatchlistPage() {
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const [market, setMarket] = useState<Market>("IN");

  const { data, isLoading } = useQuery({
    queryKey: ["watchlist"],
    queryFn: getWatchlist,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const items = data?.items ?? [];

  const quoteQueries = useQueries({
    queries: items.map(item => ({
      queryKey: ["quote", item.symbol, item.market],
      queryFn: () => fetchQuote(item.symbol, item.market),
      staleTime: 60_000,      // refresh every 60s during market hours
      retry: 1,
    })),
  });

  const add = useMutation({
    mutationFn: addToWatchlist,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["watchlist"] }); setSymbol(""); },
  });

  const remove = useMutation({
    mutationFn: removeFromWatchlist,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });

  const currency = (market: Market) => market === "IN" ? "₹" : "$";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Watchlist</h1>
        <p className="text-gray-400 text-sm mt-1">Track your favourite stocks</p>
      </div>

      {/* Add stock */}
      <div className="flex gap-3">
        <input
          className="flex-1 bg-dark-card border border-dark-border rounded-xl px-4 py-2.5 text-white text-sm outline-none focus:border-brand-500 placeholder:text-gray-500"
          placeholder="Enter symbol e.g. AAPL or RELIANCE"
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === "Enter" && symbol && add.mutate({ symbol, market, notes: "" })}
        />
        {(["IN", "US"] as Market[]).map(m => (
          <button key={m} onClick={() => setMarket(m)}
            className={clsx("px-4 py-2 rounded-xl text-sm font-medium border transition-colors",
              market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
            {m === "US" ? "🇺🇸" : "🇮🇳"} {m}
          </button>
        ))}
        <button
          onClick={() => symbol && add.mutate({ symbol, market, notes: "" })}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors"
        >
          <Plus size={16} /> Add
        </button>
      </div>

      {/* List */}
      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-6 text-gray-400 text-sm">Loading…</div>
        ) : !items.length ? (
          <div className="p-10 text-center text-gray-500 text-sm">
            No stocks in your watchlist yet. Add one above!
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-dark-border text-gray-400 text-left">
                <th className="px-6 py-4 font-medium">Symbol</th>
                <th className="px-6 py-4 font-medium">Market</th>
                <th className="px-6 py-4 font-medium text-right">Price</th>
                <th className="px-6 py-4 font-medium text-right">Change</th>
                <th className="px-6 py-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => {
                const q = quoteQueries[i]?.data;
                const loading = quoteQueries[i]?.isLoading;
                const price = q?.price ?? null;
                const changePct = q?.change_pct ?? null;
                const changeAmt = q?.change ?? null;
                const up = (changePct ?? 0) >= 0;

                return (
                  <tr key={item.symbol} className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                    <td className="px-6 py-4 font-mono font-bold text-white">
                      <Link href={`/stock/${item.symbol}?market=${item.market}`} className="hover:text-brand-400 transition-colors">
                        {item.symbol}
                      </Link>
                    </td>
                    <td className="px-6 py-4 text-gray-400">{item.market === "US" ? "🇺🇸 USA" : "🇮🇳 India"}</td>
                    <td className="px-6 py-4 text-right font-mono font-bold text-white">
                      {loading ? (
                        <span className="text-gray-600 animate-pulse">—</span>
                      ) : price != null ? (
                        `${currency(item.market)}${price.toLocaleString()}`
                      ) : (
                        <span className="text-gray-600">N/A</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      {loading ? (
                        <span className="text-gray-600 animate-pulse">—</span>
                      ) : changePct != null ? (
                        <div className={clsx("flex items-center justify-end gap-1 font-medium", up ? "text-bull" : "text-bear")}>
                          {up ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                          <span>{up ? "+" : ""}{changeAmt != null ? changeAmt.toFixed(2) : ""}</span>
                          <span className="text-xs">({up ? "+" : ""}{changePct.toFixed(2)}%)</span>
                        </div>
                      ) : (
                        <span className="text-gray-600 flex items-center justify-end gap-1"><Minus size={12} /> N/A</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link href={`/stock/${item.symbol}?market=${item.market}`}
                          className="px-3 py-1 rounded-lg bg-brand-500/20 text-brand-500 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium transition-colors">
                          Analyse →
                        </Link>
                        <button onClick={() => remove.mutate(item.symbol)}
                          className="p-1.5 rounded-lg text-gray-500 hover:text-bear hover:bg-bear/10 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
