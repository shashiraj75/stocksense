"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, TrendingUp, TrendingDown, Minus, AlertCircle, Loader2 } from "lucide-react";
import clsx from "clsx";
import { placePaperBuy, closePaperTrade, fetchPrediction, type Market, type Horizon } from "@/utils/api";
import { useSessionId } from "@/hooks/useSessionId";

interface Props {
  symbol: string;
  market: Market;
  currentPrice: number;
  signal: string;
  horizon: string;
  currency: string;
  onClose: () => void;
  existingTradeId?: number;
  existingQuantity?: number;
  existingEntryPrice?: number;
}

const HORIZONS: { key: Horizon; label: string; desc: string }[] = [
  { key: "short",  label: "Short",  desc: "1–2 weeks" },
  { key: "medium", label: "Medium", desc: "3–4 weeks" },
  { key: "long",   label: "Long",   desc: "2–3 months" },
];

export function PaperTradeModal({
  symbol, market, currentPrice, signal: initialSignal, horizon: initialHorizon, currency,
  onClose, existingTradeId, existingQuantity, existingEntryPrice,
}: Props) {
  const sessionId = useSessionId();
  const queryClient = useQueryClient();
  const isSell = existingTradeId != null;

  const [selectedHorizon, setSelectedHorizon] = useState<Horizon>(initialHorizon as Horizon);
  const [quantity, setQuantity] = useState(existingQuantity ?? 1);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Fetch prediction for selected horizon (uses cached result if already loaded)
  const { data: prediction, isLoading: predLoading } = useQuery({
    queryKey: ["prediction", symbol, market, selectedHorizon],
    queryFn: () => fetchPrediction(symbol, market, selectedHorizon),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: !isSell,
  });

  const activeSignal = prediction?.signal ?? initialSignal;
  const cost = currentPrice * quantity;

  const pnl = isSell && existingEntryPrice
    ? (currentPrice - existingEntryPrice) * (existingQuantity ?? quantity)
    : null;

  const buyMutation = useMutation({
    mutationFn: () =>
      placePaperBuy({
        session_id: sessionId, symbol, market, quantity,
        price: currentPrice, signal: activeSignal, horizon: selectedHorizon,
      }),
    onSuccess: () => {
      setSuccess(`Bought ${quantity} × ${symbol} @ ${currency}${currentPrice.toLocaleString()}`);
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => setError(e.response?.data?.detail ?? "Failed to place trade"),
  });

  const sellMutation = useMutation({
    mutationFn: () => closePaperTrade(existingTradeId!, sessionId, currentPrice),
    onSuccess: () => {
      setSuccess(`Sold ${existingQuantity} × ${symbol} @ ${currency}${currentPrice.toLocaleString()}`);
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => setError(e.response?.data?.detail ?? "Failed to close trade"),
  });

  const SignalIcon = activeSignal === "BUY" ? TrendingUp : activeSignal === "SELL" ? TrendingDown : Minus;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <div className="bg-dark-card border border-dark-border rounded-2xl w-full max-w-sm p-6 shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-lg font-bold">{isSell ? "Close Position" : "Paper Trade"}</h2>
            <p className="text-xs text-gray-400 mt-0.5">{symbol} · {market}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Horizon selector — buy only */}
        {!isSell && (
          <div className="mb-4">
            <p className="text-xs text-gray-400 mb-2">Horizon</p>
            <div className="grid grid-cols-3 gap-2">
              {HORIZONS.map(({ key, label, desc }) => (
                <button
                  key={key}
                  onClick={() => { setSelectedHorizon(key); setError(null); }}
                  className={clsx(
                    "rounded-xl px-3 py-2.5 text-center border transition-colors",
                    selectedHorizon === key
                      ? "bg-brand-500/20 border-brand-500 text-white"
                      : "bg-dark-bg border-dark-border text-gray-400 hover:border-white/30 hover:text-white"
                  )}
                >
                  <p className="text-xs font-semibold">{label}</p>
                  <p className="text-[10px] opacity-60 mt-0.5">{desc}</p>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* AI Signal pill */}
        {!isSell && (
          <div className={clsx(
            "flex items-center gap-2 rounded-xl px-3 py-2 mb-4 text-sm font-medium transition-colors",
            activeSignal === "BUY"  ? "bg-bull/10 border border-bull/30 text-bull" :
            activeSignal === "SELL" ? "bg-bear/10 border border-bear/30 text-bear" :
            "bg-white/5 border border-white/10 text-gray-400"
          )}>
            {predLoading
              ? <Loader2 size={14} className="animate-spin opacity-60" />
              : <SignalIcon size={15} />
            }
            <span>AI Signal:</span>
            {predLoading
              ? <span className="opacity-50">Loading…</span>
              : <span className="font-bold">{activeSignal}</span>
            }
            {prediction?.confidence && !predLoading && (
              <span className="ml-auto text-xs opacity-70">{prediction.confidence}% confidence</span>
            )}
          </div>
        )}

        {/* Price display */}
        <div className="bg-dark-bg rounded-xl px-4 py-3 mb-4 flex items-center justify-between">
          <span className="text-xs text-gray-400">Current Price</span>
          <span className="font-mono font-bold text-white">
            {currency}{currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>

        {/* Sell: entry price + P&L */}
        {isSell && existingEntryPrice && (
          <div className="bg-dark-bg rounded-xl px-4 py-3 mb-4 flex items-center justify-between">
            <span className="text-xs text-gray-400">Entry Price</span>
            <div className="text-right">
              <span className="font-mono font-bold text-white">
                {currency}{existingEntryPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
              {pnl !== null && (
                <p className={clsx("text-xs font-semibold", pnl >= 0 ? "text-bull" : "text-bear")}>
                  {pnl >= 0 ? "+" : ""}{currency}{Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  {" "}({pnl >= 0 ? "+" : ""}{((currentPrice - existingEntryPrice) / existingEntryPrice * 100).toFixed(2)}%)
                </p>
              )}
            </div>
          </div>
        )}

        {/* Quantity — buy only */}
        {!isSell && (
          <div className="mb-4">
            <label className="text-xs text-gray-400 mb-1.5 block">Quantity</label>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setQuantity(q => Math.max(1, q - 1))}
                className="w-9 h-9 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold"
              >−</button>
              <input
                type="number" min={1} value={quantity}
                onChange={e => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                className="flex-1 bg-dark-bg border border-dark-border rounded-lg px-3 py-2 text-center font-mono font-bold text-white focus:outline-none focus:border-brand-500"
              />
              <button
                onClick={() => setQuantity(q => q + 1)}
                className="w-9 h-9 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold"
              >+</button>
            </div>
          </div>
        )}

        {/* Cost summary */}
        {!isSell && (
          <div className="bg-dark-bg rounded-xl px-4 py-3 mb-5 flex items-center justify-between">
            <span className="text-xs text-gray-400">Total Cost</span>
            <span className="font-mono font-bold text-brand-400">
              {currency}{cost.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
          </div>
        )}

        {/* Error / success */}
        {error && (
          <div className="flex items-center gap-2 text-red-400 text-xs mb-4 bg-red-500/10 border border-red-500/30 rounded-xl px-3 py-2">
            <AlertCircle size={14} /> {error}
          </div>
        )}
        {success && (
          <div className="text-bull text-xs mb-4 bg-bull/10 border border-bull/30 rounded-xl px-3 py-2 text-center font-medium">
            {success}
          </div>
        )}

        <p className="text-xs text-gray-600 text-center mb-4">
          Paper trading uses virtual money. No real funds involved.
        </p>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2.5 rounded-xl border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors text-sm"
          >
            Cancel
          </button>
          <button
            onClick={() => { setError(null); isSell ? sellMutation.mutate() : buyMutation.mutate(); }}
            disabled={buyMutation.isPending || sellMutation.isPending || predLoading}
            className={clsx(
              "flex-1 px-4 py-2.5 rounded-xl font-semibold text-sm transition-colors",
              isSell
                ? "bg-bear hover:bg-red-600 text-white disabled:opacity-50"
                : "bg-bull hover:bg-green-600 text-white disabled:opacity-50"
            )}
          >
            {buyMutation.isPending || sellMutation.isPending
              ? "Placing…"
              : predLoading
              ? "Loading signal…"
              : isSell ? `Sell ${existingQuantity} shares` : `Buy ${quantity} shares`}
          </button>
        </div>
      </div>
    </div>
  );
}
