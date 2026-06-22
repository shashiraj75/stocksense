"use client";
import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, TrendingUp, TrendingDown, Minus, AlertCircle, Loader2, ShieldAlert, Target } from "lucide-react";
import clsx from "clsx";
import { placePaperBuy, closePaperTrade, fetchPrediction, type Market, type Horizon } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";

interface Props {
  symbol: string;
  market: Market;
  currentPrice: number;
  signal: string;
  horizon: string;
  currency: string;
  suggestedStopLoss?: number | null;
  suggestedTargetPrice?: number | null;
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
  suggestedStopLoss, suggestedTargetPrice, onClose, existingTradeId, existingQuantity, existingEntryPrice,
}: Props) {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const queryClient = useQueryClient();
  const isSell = existingTradeId != null;

  const [selectedHorizon, setSelectedHorizon] = useState<Horizon>(initialHorizon as Horizon);
  const [quantity, setQuantity] = useState(existingQuantity ?? 1);
  // Always pre-fill with AI suggestions — visible and editable regardless of signal
  const [stopLoss, setStopLoss] = useState<string>(
    suggestedStopLoss ? suggestedStopLoss.toFixed(2) : ""
  );
  const [targetPrice, setTargetPrice] = useState<string>(
    suggestedTargetPrice ? suggestedTargetPrice.toFixed(2) : ""
  );
  // Track whether the user manually edited the fields so we don't overwrite their changes
  const stopLossEdited = useRef(false);
  const targetPriceEdited = useRef(false);
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

  // When the prediction for the selected horizon loads, sync AI-suggested values
  // unless the user has already manually edited those fields.
  useEffect(() => {
    if (!prediction) return;
    const levels = (prediction as any).trade_levels;
    if (levels?.stop_loss != null && !stopLossEdited.current) {
      setStopLoss(parseFloat(levels.stop_loss).toFixed(2));
    }
    if (levels?.take_profit != null && !targetPriceEdited.current) {
      setTargetPrice(parseFloat(levels.take_profit).toFixed(2));
    }
  }, [prediction]);

  const activeSignal = prediction?.signal ?? initialSignal;
  const cost = currentPrice * quantity;

  const pnl = isSell && existingEntryPrice
    ? (currentPrice - existingEntryPrice) * (existingQuantity ?? quantity)
    : null;

  const stopLossValue = stopLoss ? parseFloat(stopLoss) : null;
  const stopLossPct = stopLossValue && stopLossValue > 0
    ? ((stopLossValue - currentPrice) / currentPrice * 100)
    : null;

  const targetPriceValue = targetPrice ? parseFloat(targetPrice) : null;
  const targetPricePct = targetPriceValue && targetPriceValue > 0
    ? ((targetPriceValue - currentPrice) / currentPrice * 100)
    : null;

  const buyMutation = useMutation({
    mutationFn: () =>
      placePaperBuy({
        user_id: userId, symbol, market, quantity,
        price: currentPrice, signal: activeSignal, horizon: selectedHorizon,
        stop_loss: stopLossValue && stopLossValue > 0 ? stopLossValue : null,
        target_price: targetPriceValue && targetPriceValue > 0 ? targetPriceValue : null,
        email: user?.email,
      }),
    onSuccess: () => {
      setSuccess(`Bought ${quantity} × ${symbol} @ ${currency}${currentPrice.toLocaleString()}`);
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => setError(e.response?.data?.detail ?? "Failed to place trade"),
  });

  const sellMutation = useMutation({
    mutationFn: () => closePaperTrade(existingTradeId!, userId, currentPrice),
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
      <div className="bg-dark-card border border-dark-border rounded-2xl w-full max-w-sm shadow-2xl flex flex-col max-h-[92dvh]">

        {/* Header — fixed, never scrolls */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-dark-border shrink-0">
          <div>
            <h2 className="text-base font-bold">{isSell ? "Close Position" : "Paper Trade"}</h2>
            <p className="text-xs text-gray-400 mt-0.5">{symbol} · {market}</p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1 px-5 py-3 space-y-2.5">

          {/* Horizon selector */}
          {!isSell && (
            <div>
              <p className="text-xs text-gray-400 mb-1.5">Horizon</p>
              <div className="grid grid-cols-3 gap-1.5">
                {HORIZONS.map(({ key, label, desc }) => (
                  <button key={key}
                    onClick={() => { setSelectedHorizon(key); setError(null); stopLossEdited.current = false; targetPriceEdited.current = false; }}
                    className={clsx("rounded-lg px-2 py-2 text-center border transition-colors",
                      selectedHorizon === key ? "bg-brand-500/20 border-brand-500 text-white" : "bg-dark-bg border-dark-border text-gray-400 hover:border-white/30 hover:text-white")}>
                    <p className="text-xs font-semibold">{label}</p>
                    <p className="text-[10px] opacity-60">{desc}</p>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Signal warning (compact single line) */}
          {!isSell && (activeSignal === "SELL" || activeSignal === "HOLD") && (
            <div className={clsx("flex items-center gap-2 rounded-lg px-3 py-2 text-xs border",
              activeSignal === "SELL" ? "bg-bear/10 border-bear/30 text-red-300" : "bg-yellow-500/10 border-yellow-500/30 text-yellow-300")}>
              <AlertCircle size={13} className="shrink-0" />
              <span><strong>AI signal is {activeSignal}</strong> — {activeSignal === "SELL" ? "model expects decline, proceed carefully." : "no strong entry yet, consider waiting."}</span>
            </div>
          )}

          {/* AI Signal pill + price — combined row */}
          {!isSell && (
            <div className="flex items-center gap-2">
              <div className={clsx("flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium border flex-1",
                activeSignal === "BUY" ? "bg-bull/10 border-bull/30 text-bull" :
                activeSignal === "SELL" ? "bg-bear/10 border-bear/30 text-bear" :
                "bg-white/5 border-white/10 text-gray-400")}>
                {predLoading ? <Loader2 size={12} className="animate-spin opacity-60" /> : <SignalIcon size={13} />}
                <span className="font-bold">{predLoading ? "Loading…" : activeSignal}</span>
                {prediction?.confidence && !predLoading && <span className="opacity-60 ml-auto">{prediction.confidence}%</span>}
              </div>
              <div className="bg-dark-bg rounded-lg px-3 py-1.5 flex items-center gap-2 flex-1">
                <span className="text-xs text-gray-400">Price</span>
                <span className="font-mono font-bold text-white text-sm ml-auto">{currency}{currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
              </div>
            </div>
          )}

          {/* Sell: entry price + P&L */}
          {isSell && existingEntryPrice && (
            <div className="bg-dark-bg rounded-lg px-3 py-2 flex items-center justify-between">
              <span className="text-xs text-gray-400">Entry Price</span>
              <div className="text-right">
                <span className="font-mono font-bold text-white text-sm">{currency}{existingEntryPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                {pnl !== null && (
                  <p className={clsx("text-xs font-semibold", pnl >= 0 ? "text-bull" : "text-bear")}>
                    {pnl >= 0 ? "+" : ""}{currency}{Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    {" "}({pnl >= 0 ? "+" : ""}{((currentPrice - existingEntryPrice) / existingEntryPrice * 100).toFixed(2)}%)
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Quantity */}
          {!isSell && (
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Quantity</label>
              <div className="flex items-center gap-2">
                <button onClick={() => setQuantity(q => Math.max(1, q - 1))}
                  className="w-8 h-8 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold text-sm shrink-0">−</button>
                <input type="number" min={1} value={quantity}
                  onChange={e => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                  className="flex-1 bg-dark-bg border border-dark-border rounded-lg px-3 py-1.5 text-center font-mono font-bold text-white focus:outline-none focus:border-brand-500 text-sm" />
                <button onClick={() => setQuantity(q => q + 1)}
                  className="w-8 h-8 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold text-sm shrink-0">+</button>
              </div>
            </div>
          )}

          {/* Stop Loss + Target Price — side by side */}
          {!isSell && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-gray-400 flex items-center gap-1 mb-1">
                  <ShieldAlert size={11} className="text-yellow-400" /> Stop Loss
                </label>
                <input type="number" min={0} step="0.01"
                  placeholder={(currentPrice * 0.95).toFixed(2)}
                  value={stopLoss}
                  onChange={e => { stopLossEdited.current = true; setStopLoss(e.target.value); }}
                  className="w-full bg-dark-bg border border-yellow-500/40 rounded-lg px-2.5 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-yellow-500/80 placeholder:text-gray-600" />
                {stopLossPct !== null && (
                  <p className={clsx("text-[10px] mt-0.5", stopLossPct < 0 ? "text-yellow-400" : "text-red-400")}>
                    {stopLossPct.toFixed(1)}% {stopLossPct < 0 ? "below entry" : "⚠ above entry"}
                  </p>
                )}
              </div>
              <div>
                <label className="text-xs text-gray-400 flex items-center gap-1 mb-1">
                  <Target size={11} className="text-green-400" /> Target Price
                </label>
                <input type="number" min={0} step="0.01"
                  placeholder={(currentPrice * 1.1).toFixed(2)}
                  value={targetPrice}
                  onChange={e => { targetPriceEdited.current = true; setTargetPrice(e.target.value); }}
                  className="w-full bg-dark-bg border border-green-500/40 rounded-lg px-2.5 py-1.5 font-mono text-sm text-white focus:outline-none focus:border-green-500/80 placeholder:text-gray-600" />
                {targetPricePct !== null && (
                  <p className={clsx("text-[10px] mt-0.5", targetPricePct > 0 ? "text-green-400" : "text-red-400")}>
                    {targetPricePct > 0 ? "+" : ""}{targetPricePct.toFixed(1)}% {targetPricePct > 0 ? "above entry" : "⚠ below entry"}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Total cost */}
          {!isSell && (
            <div className="bg-dark-bg rounded-lg px-3 py-2 flex items-center justify-between">
              <span className="text-xs text-gray-400">Total Cost</span>
              <span className="font-mono font-bold text-brand-400">{currency}{cost.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
            </div>
          )}

          {/* Error / success */}
          {error && (
            <div className="flex items-center gap-2 text-red-400 text-xs bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
              <AlertCircle size={13} /> {error}
            </div>
          )}
          {success && (
            <div className="text-bull text-xs bg-bull/10 border border-bull/30 rounded-lg px-3 py-2 text-center font-medium">{success}</div>
          )}

        </div>{/* end scrollable body */}

        {/* Sticky footer */}
        <div className="px-5 py-3 border-t border-dark-border shrink-0 space-y-2">
          <p className="text-[10px] text-gray-600 text-center">AI pre-filled · editable · Virtual money only</p>
          <div className="flex gap-2">
            <button onClick={onClose}
              className="flex-1 px-4 py-2 rounded-xl border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors text-sm">
              Cancel
            </button>
            <button
              onClick={() => { setError(null); isSell ? sellMutation.mutate() : buyMutation.mutate(); }}
              disabled={buyMutation.isPending || sellMutation.isPending}
              className={clsx("flex-1 px-4 py-2 rounded-xl font-semibold text-sm transition-colors",
                isSell ? "bg-bear hover:bg-red-600 text-white disabled:opacity-50" : "bg-bull hover:bg-green-600 text-white disabled:opacity-50")}>
              {buyMutation.isPending || sellMutation.isPending ? "Placing…" : isSell ? `Sell ${existingQuantity} shares` : `Buy ${quantity} shares`}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
