"use client";
import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X, TrendingUp, TrendingDown, Minus, AlertCircle, Loader2, ShieldAlert, Target, Clock } from "lucide-react";
import clsx from "clsx";
import Link from "next/link";
import { placePaperBuy, closePaperTrade, fetchPrediction, fetchPaperPortfolio, type Market, type Horizon, type TradeManagementMode, type EvidenceSource, type EntryEvidencePayload } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { getMarketStatus } from "@/utils/marketHours";
import { computeSuggestedQuantity, RISK_PCT_OF_CAPITAL } from "@/utils/riskBasedSizing";
import { buildEntryEvidenceFromPrediction } from "@/utils/entryEvidence";
import { generateBuyIdempotencyKey } from "@/utils/idempotencyKey";

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
  // The recommendation's generation/reference price, when it differs from
  // currentPrice — the caller is expected to have already resolved
  // currentPrice to the latest live quote (falling back to this same value
  // only when no live quote exists), so this prop is purely informational:
  // it lets the modal tell the user their execution price is not the price
  // the recommendation was generated against, without recomputing anything.
  referencePrice?: number | null;
  // Trade Postmortem Evidence Completion, Phase A1 — WHERE/WHY the user
  // opened this trade, not merely where the symbol could also be found.
  // Defaults to "MANUAL" (the safe, honest default for a caller that
  // doesn't specify — matches the backend's own default) when omitted.
  evidenceSource?: EvidenceSource;
  // When provided (even explicitly `null`), used verbatim as the entry
  // evidence sent with the Buy — this is how a Daily Pick Buy captures
  // evidence from the Daily Pick payload the user actually saw, instead of
  // this modal's own internally-fetched Prediction (which could describe a
  // different, possibly since-changed recommendation). When omitted
  // entirely (undefined), the modal builds evidence itself from its own
  // internally-fetched Prediction for the currently selected horizon — the
  // correct behavior for a Stock Detail (RESEARCH) Buy.
  entryEvidenceOverride?: EntryEvidencePayload | null;
}

const HORIZONS: { key: Horizon; label: string; desc: string }[] = [
  { key: "short",  label: "Short",  desc: "1–2 weeks" },
  { key: "medium", label: "Medium", desc: "3–4 weeks" },
  { key: "long",   label: "Long",   desc: "2–3 months" },
];

const TRADE_MANAGEMENT_OPTIONS: { key: TradeManagementMode; label: string; desc: string; disabled?: boolean }[] = [
  { key: "manual", label: "Manual", desc: "Alerts only" },
  { key: "auto",   label: "Auto",   desc: "Automatically closes trade" },
  { key: "ai_assisted", label: "AI", desc: "Coming Soon", disabled: true },
];

export function PaperTradeModal({
  symbol, market, currentPrice, signal: initialSignal, horizon: initialHorizon, currency,
  suggestedStopLoss, suggestedTargetPrice, onClose, existingTradeId, existingQuantity, existingEntryPrice,
  referencePrice, evidenceSource = "MANUAL", entryEvidenceOverride,
}: Props) {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const queryClient = useQueryClient();
  const isSell = existingTradeId != null;

  const [selectedHorizon, setSelectedHorizon] = useState<Horizon>(initialHorizon as Horizon);
  const [tradeManagementMode, setTradeManagementMode] = useState<TradeManagementMode>("manual");
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
  const quantityEdited = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Block order placement while the relevant market is closed — executing
  // instantly at a stale last-close price would be unrealistic (real
  // markets can gap on the next open) and looks unprofessional.
  const [marketStatus, setMarketStatus] = useState(() => getMarketStatus(market));
  useEffect(() => {
    const update = () => setMarketStatus(getMarketStatus(market));
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, [market]);
  const marketClosed = !marketStatus.isOpen;

  // Fetch prediction for selected horizon (uses cached result if already loaded)
  const { data: prediction, isLoading: predLoading } = useQuery({
    queryKey: ["prediction", symbol, market, selectedHorizon],
    queryFn: () => fetchPrediction(symbol, market, selectedHorizon),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: !isSell,
  });

  // Same queryKey/queryFn as the Paper Trading page's own portfolio fetch
  // (paper-trading/page.tsx) — react-query dedupes against that cache when
  // it's already loaded, and fetches fresh here otherwise (e.g. opened
  // directly from the Picks or Stock Detail page).
  const { data: portfolio } = useQuery({
    queryKey: ["paper-portfolio", userId],
    queryFn: () => fetchPaperPortfolio(userId, user?.email),
    enabled: !isSell && !!userId,
  });
  const availableCash = market === "IN" ? portfolio?.cash : portfolio?.cash_usd;

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

  // Only show the distinction when the reference price is a real, different
  // number — not for the (already-correct) case where no live quote existed
  // and currentPrice already equals the reference price.
  const usingLivePriceOverReference =
    !isSell &&
    referencePrice != null && Number.isFinite(referencePrice) && referencePrice > 0 &&
    Math.abs(currentPrice - referencePrice) > 0.01;

  const pnl = isSell && existingEntryPrice
    ? (currentPrice - existingEntryPrice) * (existingQuantity ?? quantity)
    : null;

  const stopLossValue = stopLoss ? parseFloat(stopLoss) : null;
  const stopLossPct = stopLossValue && stopLossValue > 0
    ? ((stopLossValue - currentPrice) / currentPrice * 100)
    : null;

  // Risk-based quantity suggestion — see riskBasedSizing.ts for the math
  // and rationale. perShareRisk is derived here (not inside the util) since
  // it's also used directly by the hint text below.
  const perShareRisk = stopLossValue && stopLossValue > 0 && currentPrice > 0
    ? Math.abs(currentPrice - stopLossValue)
    : null;
  const suggestedQuantity = computeSuggestedQuantity({
    currentPrice, stopLoss: stopLossValue, availableCash,
  });

  // Auto-apply the suggestion once it's available, same pattern as the AI
  // stop-loss/target auto-fill above — pre-filled but never fights a
  // manual edit, and re-applies when the horizon (and therefore the
  // stop-loss distance) changes.
  useEffect(() => {
    if (suggestedQuantity != null && !quantityEdited.current) {
      setQuantity(suggestedQuantity);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestedQuantity]);

  const targetPriceValue = targetPrice ? parseFloat(targetPrice) : null;
  const targetPricePct = targetPriceValue && targetPriceValue > 0
    ? ((targetPriceValue - currentPrice) / currentPrice * 100)
    : null;

  // Fix 1 (owner-audit correction, Phase A1) — a Daily Pick Buy is opened
  // with `entryEvidenceOverride` frozen from the ORIGINAL Daily Pick the
  // user saw at its ORIGINAL horizon. If the user switches this modal's
  // horizon selector to a different horizon, that frozen override describes
  // a different recommendation than the one actually being bought — reusing
  // it would silently mislabel the trade's entry evidence. When the horizon
  // still matches what the Daily Pick was generated for, the override is
  // exactly correct and is used verbatim. When it doesn't, this modal falls
  // back to the same Prediction-based evidence builder Stock Detail (RESEARCH)
  // Buys use, built from the `prediction` query above — which is already
  // fetched for `selectedHorizon` unconditionally (see the `useQuery` above,
  // `enabled: !isSell`) regardless of evidenceSource, so a genuine
  // horizon-matched Prediction really is available here, not fabricated.
  // `evidenceSource` intentionally stays whatever the caller passed (e.g.
  // "DAILY_PICK") even in this fallback branch: the UX-origin of the Buy
  // (the user came from the Daily Picks page) is a separate fact from which
  // evidence payload was actually knowable at the moment of submission, and
  // changing evidence_source here would misrepresent where the user
  // navigated from.
  const originalHorizon = initialHorizon as Horizon;
  const horizonMatchesOriginalPick = selectedHorizon === originalHorizon;
  const entryEvidence: EntryEvidencePayload | null =
    entryEvidenceOverride !== undefined
      ? (horizonMatchesOriginalPick
          ? entryEvidenceOverride
          : buildEntryEvidenceFromPrediction(prediction ?? null))
      : buildEntryEvidenceFromPrediction(prediction ?? null);

  // Fix 2 (owner-audit correction, Phase A1) — ONE logical Buy attempt =
  // ONE frozen request payload + ONE stable idempotency key. Previously the
  // idempotency key alone was stabilized, but the request payload itself
  // (price, entryEvidence, activeSignal, ...) was recomputed from live state
  // on every retry — so a lost-response retry after a quote/Prediction
  // change could send a DIFFERENT payload under the SAME key, defeating the
  // backend's idempotency guarantee (which authenticates the key against a
  // hash of the request body). `frozenBuyRequestRef` holds the exact
  // request object (including the key) for the current logical attempt;
  // `materialInputsSignatureRef` tracks only the inputs a new explicit user
  // decision can change (quantity/horizon/stop/target/trade-management
  // mode) — NOT currentPrice/prediction/entryEvidence/activeSignal, since an
  // automatic quote or Prediction refresh must never by itself start a new
  // logical attempt. A successful Buy clears both refs so the NEXT Buy
  // click (a new, later, intentional decision) freshly freezes.
  const frozenBuyRequestRef = useRef<{
    idempotency_key: string;
    user_id: string; symbol: string; market: Market; quantity: number;
    price: number; signal: string; horizon: Horizon;
    stop_loss: number | null; target_price: number | null;
    email?: string;
    trade_management_mode: TradeManagementMode;
    evidence_source: EvidenceSource;
    entry_evidence: EntryEvidencePayload | null;
  } | null>(null);
  const materialInputsSignatureRef = useRef<string | null>(null);
  // Fix 3 — a synchronous (same-tick) in-flight guard. `buyMutation.isPending`
  // only flips after React commits the mutation's internal state update,
  // which is not guaranteed to happen before a second click handler runs on
  // a fast double-click/rapid-Enter; this ref is set synchronously in the
  // click handler itself, before `mutate` is even called, so it can never
  // be raced the way `isPending` can.
  const buySubmissionInFlightRef = useRef(false);

  function getOrCreateFrozenBuyRequest() {
    // Only a genuine, user-driven change counts as "material" here — the
    // AI-suggested quantity/stop-loss/target-price auto-fill effects above
    // (computeSuggestedQuantity, the Prediction-driven stop/target sync)
    // can change `quantity`/`stopLoss`/`targetPrice` purely because a live
    // quote or Prediction refreshed, with no user action at all. Gating each
    // field on its own `*Edited` ref (already tracked for the auto-fill
    // "don't fight a manual edit" logic above) means an automatic refresh
    // alone can never perturb this signature and mint an unwanted new key —
    // only an explicit user edit (or an explicit horizon switch, which
    // itself resets these refs) does.
    const materialSignature = JSON.stringify({
      quantity: quantityEdited.current ? quantity : "auto",
      horizon: selectedHorizon,
      stopLoss: stopLossEdited.current
        ? (stopLossValue && stopLossValue > 0 ? stopLossValue : null)
        : "auto",
      targetPrice: targetPriceEdited.current
        ? (targetPriceValue && targetPriceValue > 0 ? targetPriceValue : null)
        : "auto",
      tradeManagementMode,
    });
    if (frozenBuyRequestRef.current === null || materialInputsSignatureRef.current !== materialSignature) {
      frozenBuyRequestRef.current = {
        idempotency_key: generateBuyIdempotencyKey(),
        user_id: userId, symbol, market, quantity,
        price: currentPrice, signal: activeSignal, horizon: selectedHorizon,
        stop_loss: stopLossValue && stopLossValue > 0 ? stopLossValue : null,
        target_price: targetPriceValue && targetPriceValue > 0 ? targetPriceValue : null,
        email: user?.email,
        trade_management_mode: tradeManagementMode,
        evidence_source: evidenceSource,
        entry_evidence: entryEvidence,
      };
      materialInputsSignatureRef.current = materialSignature;
    }
    return frozenBuyRequestRef.current;
  }

  const buyMutation = useMutation({
    mutationFn: (req: NonNullable<typeof frozenBuyRequestRef.current>) => placePaperBuy(req),
    onSuccess: () => {
      setSuccess(`Bought ${quantity} × ${symbol} @ ${currency}${currentPrice.toLocaleString()}`);
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      // A new, later intentional Buy after this success is a NEW logical
      // attempt — clear the frozen request/key so it freshly freezes.
      frozenBuyRequestRef.current = null;
      materialInputsSignatureRef.current = null;
      buySubmissionInFlightRef.current = false;
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => {
      buySubmissionInFlightRef.current = false;
      setError(e.response?.data?.detail ?? "Failed to place trade");
    },
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

  // Defense in depth: every entry point that renders this modal (stock-detail
  // page, Daily Picks page) is expected to already gate opening it on `user`,
  // but a logged-out visitor must never see a usable Buy/Sell form even if
  // reached some other way — the backend requires a verified JWT for every
  // Paper Trading mutation regardless, so this is a UX clarification only,
  // not a replacement for that server-side check.
  if (!user) {
    return (
      <div className="fixed inset-0 z-[60] w-screen h-screen overflow-hidden flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
        <div className="bg-dark-card border border-dark-border rounded-2xl w-full max-w-sm shadow-2xl flex flex-col">
          <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-dark-border shrink-0">
            <div>
              <h2 className="text-base font-bold">Sign In Required</h2>
              <p className="text-xs text-gray-400 mt-0.5">{symbol} · {market}</p>
            </div>
            <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
              <X size={18} />
            </button>
          </div>
          <div className="px-5 py-6 space-y-4 text-center">
            <p className="text-sm text-gray-300">
              Paper trading practices with virtual money, but still requires a free account to track your positions.
            </p>
            <Link
              href="/login"
              className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold transition-colors"
            >
              Sign in to paper trade
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[60] w-screen h-screen overflow-hidden flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
      <div className="bg-dark-card border border-dark-border rounded-2xl w-full max-w-sm shadow-2xl flex flex-col max-h-[94dvh] sm:max-h-[92dvh]">

        {/* Header — fixed, never scrolls */}
        <div className="flex items-center justify-between px-4 sm:px-5 pt-4 sm:pt-5 pb-2.5 sm:pb-3 border-b border-dark-border shrink-0">
          <div>
            <h2 className="text-base font-bold">{isSell ? "Close Position" : "Paper Trade"}</h2>
            <p className="text-xs text-gray-400 mt-0.5 flex items-center gap-1.5 flex-wrap">
              <span>{symbol} · {market}</span>
              {/* Explicit Open/Closed status, not just a warning when closed —
                  reuses the same getMarketStatus label already computed above
                  for the closed-market banner, no new status source. */}
              <span className={clsx(
                "text-[10px] font-semibold px-1.5 py-0.5 rounded-full border",
                marketStatus.isOpen
                  ? "bg-bull/10 border-bull/30 text-bull"
                  : "bg-yellow-500/10 border-yellow-500/30 text-yellow-300"
              )}>
                {marketStatus.label}
              </span>
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto overscroll-contain flex-1 px-4 sm:px-5 py-2.5 sm:py-3 space-y-2 sm:space-y-2.5">

          {/* Market closed — block order placement */}
          {marketClosed && (
            <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-xs border bg-yellow-500/10 border-yellow-500/30 text-yellow-300">
              <Clock size={13} className="shrink-0 mt-0.5" />
              <span>
                <strong>{market} market is closed.</strong> Orders execute at the live price, so trading is
                paused until the market reopens{marketStatus.nextEventLabel ? ` — ${marketStatus.nextEventLabel}` : ""}.
              </span>
            </div>
          )}

          {/* Horizon selector */}
          {!isSell && (
            <div>
              <p className="text-xs text-gray-400 mb-1 sm:mb-1.5">Horizon</p>
              <div className="grid grid-cols-3 gap-1.5">
                {HORIZONS.map(({ key, label, desc }) => (
                  <button key={key}
                    onClick={() => { setSelectedHorizon(key); setError(null); stopLossEdited.current = false; targetPriceEdited.current = false; quantityEdited.current = false; }}
                    className={clsx("rounded-lg px-2 py-1.5 sm:py-2 text-center border transition-colors",
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
                <span className="text-xs text-gray-400">{usingLivePriceOverReference ? "Execution Price" : "Price"}</span>
                <span className="font-mono font-bold text-white text-sm ml-auto">{currency}{currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
              </div>
            </div>
          )}

          {/* Execution vs. recommendation-generation price — calm, factual,
              never phrased as urgency; only appears when the two genuinely
              differ, so it can't read as noise on the common case. */}
          {usingLivePriceOverReference && (
            <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-xs border bg-white/5 border-dark-border text-gray-400">
              <AlertCircle size={13} className="shrink-0 mt-0.5 opacity-70" />
              <span>
                Using latest market price for paper trade execution. Recommendation was generated at{" "}
                {currency}{referencePrice!.toLocaleString(undefined, { maximumFractionDigits: 2 })}.
              </span>
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
                <button onClick={() => { quantityEdited.current = true; setQuantity(q => Math.max(1, q - 1)); }}
                  className="w-8 h-8 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold text-sm shrink-0">−</button>
                <input type="number" min={1} value={quantity}
                  onChange={e => { quantityEdited.current = true; setQuantity(Math.max(1, parseInt(e.target.value) || 1)); }}
                  className="flex-1 bg-dark-bg border border-dark-border rounded-lg px-3 py-1.5 text-center font-mono font-bold text-white focus:outline-none focus:border-brand-500 text-sm" />
                <button onClick={() => { quantityEdited.current = true; setQuantity(q => q + 1); }}
                  className="w-8 h-8 rounded-lg bg-dark-bg border border-dark-border text-white hover:bg-white/10 transition-colors font-bold text-sm shrink-0">+</button>
              </div>
              {suggestedQuantity != null ? (
                <p className="text-[10px] text-gray-500 mt-1">
                  Risk-based suggestion: {suggestedQuantity} shares — risks ~{currency}{(perShareRisk! * suggestedQuantity).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  {" "}({(RISK_PCT_OF_CAPITAL * 100).toFixed(0)}% of {currency}{availableCash!.toLocaleString(undefined, { maximumFractionDigits: 0 })} available)
                  {quantityEdited.current && quantity !== suggestedQuantity && (
                    <button onClick={() => { quantityEdited.current = false; setQuantity(suggestedQuantity); }}
                      className="ml-1.5 text-brand-400 hover:text-brand-300 underline underline-offset-2">Use suggestion</button>
                  )}
                </p>
              ) : (
                <p className="text-[10px] text-gray-600 mt-1">
                  Set a stop loss to get a risk-based quantity suggestion (risks a fixed % of your available capital per trade).
                </p>
              )}
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

          {/* Trade Management — how a stop-loss/target hit is handled.
              Compact 3-up grid: short one-word labels keep each card small
              enough that it doesn't need to stack on a ~375px screen. */}
          {!isSell && (
            <div>
              <label className="text-xs text-gray-400 mb-1 block">Trade Management</label>
              <div className="grid grid-cols-3 gap-1">
                {TRADE_MANAGEMENT_OPTIONS.map(({ key, label, desc, disabled }) => (
                  <button key={key} type="button"
                    disabled={disabled}
                    title={disabled ? desc : undefined}
                    onClick={() => !disabled && setTradeManagementMode(key)}
                    className={clsx("rounded-lg px-1.5 py-1.5 text-center border transition-colors",
                      disabled
                        ? "bg-dark-bg border-dark-border text-gray-600 cursor-not-allowed opacity-60"
                        : tradeManagementMode === key
                          ? "bg-brand-500/20 border-brand-500 text-white"
                          : "bg-dark-bg border-dark-border text-gray-400 hover:border-white/30 hover:text-white")}>
                    <p className="text-xs font-semibold">{label}</p>
                    <p className="text-[9px] opacity-70 mt-0.5 leading-tight">{desc}</p>
                  </button>
                ))}
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
        <div className="px-4 sm:px-5 py-2.5 sm:py-3 border-t border-dark-border shrink-0 space-y-1.5 sm:space-y-2">
          <p className="text-[10px] text-gray-600 text-center">AI pre-filled · editable · Virtual money only</p>
          <div className="flex gap-2">
            <button onClick={onClose}
              className="flex-1 px-4 py-2 rounded-xl border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors text-sm">
              Cancel
            </button>
            <button
              onClick={() => {
                // Fix 3 — defense in depth against a double-click/rapid-Enter
                // race: the ref guard is checked and set SYNCHRONOUSLY,
                // before `mutate` is called, so a second click that fires
                // before React has committed `isPending` (not guaranteed to
                // happen same-tick) still can't slip through. The `disabled`
                // attribute below and the backend's durable idempotency key
                // are the other two layers of this defense-in-depth.
                if (buySubmissionInFlightRef.current || buyMutation.isPending || sellMutation.isPending) return;
                setError(null);
                if (isSell) { sellMutation.mutate(); return; }
                buySubmissionInFlightRef.current = true;
                buyMutation.mutate(getOrCreateFrozenBuyRequest());
              }}
              disabled={buyMutation.isPending || sellMutation.isPending || marketClosed}
              title={marketClosed ? `${market} market is closed` : undefined}
              className={clsx("flex-1 px-4 py-2 rounded-xl font-semibold text-sm transition-colors",
                isSell ? "bg-bear hover:bg-red-600 text-white disabled:opacity-50" : "bg-bull hover:bg-green-600 text-white disabled:opacity-50")}>
              {buyMutation.isPending || sellMutation.isPending
                ? "Placing…"
                : marketClosed
                  ? "Market Closed"
                  : isSell ? `Sell ${existingQuantity} shares` : `Buy ${quantity} shares`}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
