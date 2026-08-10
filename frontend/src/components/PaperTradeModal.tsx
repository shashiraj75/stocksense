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
import { buyOperationKey, closeOperationKey, getBuyLock, reserveBuyLock, releaseBuyLock } from "@/utils/paperBuyLock";

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
  // Race closure (4) — true only between an EXPLICIT horizon-button click
  // and that new horizon's Prediction actually resolving (or the user
  // switching back to a horizon that's already reconciled, e.g. the frozen
  // Daily Pick horizon). Deliberately NOT set on initial mount — the
  // ordinary "Prediction hasn't loaded yet" state right after opening the
  // modal is not this race (there's no stale prior-horizon data on screen
  // to submit), so gating Buy on it there would be a needless regression
  // for the common case. See `awaitingMatchingHorizonPrediction` below.
  const [horizonSwitchPending, setHorizonSwitchPending] = useState(false);
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

  // Fix 4/5/6 (Phase A1.1 final decision-context correction) — ONE explicit
  // "active recommendation source" concept, computed once, that ALL
  // recommendation-facing UI (signal pill, stop/target sync, reference-price
  // notice) AND the persisted `entryEvidence` read from — not independently
  // per field. That's what the earlier version of this fix got only
  // half-right: the AI Signal pill correctly switched to the frozen Daily
  // Pick's signal/confidence, but the stop/target auto-fill effect further
  // below still synced straight from the live, independently-fetched
  // `prediction` query's `trade_levels`, regardless of this condition — a
  // second, unrelated recommendation source leaking into the same UI/submit
  // path. Production trade 304 (AMG) is the origin case this whole family of
  // fixes addresses.
  //
  // Unchanged-horizon DAILY_PICK (`usingFrozenDailyPickEvidence === true`):
  // the frozen `entryEvidenceOverride` is the active source for signal,
  // confidence, reference price, and suggested stop/target. The live
  // `prediction` query's `trade_levels` must NOT feed the stop/target input
  // fields in this case.
  // RESEARCH / horizon-switched DAILY_PICK (`=== false`): the
  // selected-horizon `prediction` query is the active source for the same
  // fields, exactly as before.
  const originalHorizon = initialHorizon as Horizon;
  const horizonMatchesOriginalPick = selectedHorizon === originalHorizon;
  const usingFrozenDailyPickEvidence =
    entryEvidenceOverride !== undefined && horizonMatchesOriginalPick;

  // Phase A1.1 race closure — the useQuery above has no `placeholderData`/
  // `keepPreviousData`, so react-query itself never surfaces a PRIOR
  // horizon's cached object under the new queryKey; `prediction` genuinely
  // goes `undefined` while a newly-selected horizon's fetch is in flight.
  // The actual race was elsewhere: every `active*` constant below used to
  // fall back to a stale prop (`initialSignal`, `referencePrice`, the old
  // `stopLoss`/`targetPrice` input values) whenever `prediction` was
  // momentarily undefined/loading — which is exactly the transition window
  // between clicking a new horizon and its Prediction resolving. This
  // derived value is the single gate that closes that window: it is the
  // live Prediction ONLY when it is genuinely present AND (belt-and-braces,
  // since the API response itself carries a `horizon` field) confirmed to
  // describe the currently selected horizon — never a stale/mismatched
  // object, and never used merely because `predLoading` happens to be
  // false between renders.
  const matchingSelectedHorizonPrediction =
    prediction && prediction.horizon === selectedHorizon ? prediction : null;
  // Clears the in-flight horizon-switch gate the instant it's genuinely
  // resolved: either a real matching Prediction has arrived, or the user
  // switched back to a horizon the frozen Daily Pick context already covers
  // (`usingFrozenDailyPickEvidence`) — in which case there is no live
  // Prediction dependency to wait on at all.
  useEffect(() => {
    if (matchingSelectedHorizonPrediction || usingFrozenDailyPickEvidence) {
      setHorizonSwitchPending(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matchingSelectedHorizonPrediction, usingFrozenDailyPickEvidence, selectedHorizon]);
  // True only while the user has explicitly navigated to a horizon whose
  // Prediction hasn't (yet, or ever will) resolve — the narrow "between
  // horizons with no matching data yet" window fixes 2/3/4 below gate on.
  // Never true for the frozen Daily Pick path (it has no live-Prediction
  // dependency at all) and never true once a genuine match has loaded.
  // `horizonSwitchPending` (below) narrows this further to genuinely
  // in-flight EXPLICIT horizon switches — never the modal's ordinary
  // initial-mount loading, which is not a race at all (nothing stale is on
  // screen yet to submit).
  const awaitingMatchingHorizonPrediction =
    horizonSwitchPending && !usingFrozenDailyPickEvidence && !isSell && matchingSelectedHorizonPrediction === null;

  const activeSignal = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride?.recommendation_signal ?? initialSignal)
    : (matchingSelectedHorizonPrediction?.signal ?? initialSignal);
  // Only meaningful when NOT using the frozen Daily Pick evidence — the
  // frozen path never depends on the live Prediction query, so it can
  // never be "loading" in a way the UI needs to reflect. Uses `predLoading`
  // directly (not the horizon-switch-gated `awaitingMatchingHorizonPrediction`
  // below) so the pill's own "Loading…" spinner still covers BOTH the
  // ordinary initial-mount fetch and any horizon-switch fetch — it already
  // correctly hid the stale-signal fallback visually before this pass; only
  // the OTHER active-source constants (confidence/reference/stop/target/
  // evidence/Buy-gate) had the actual leak this pass closes.
  const isSignalPillLoading = !usingFrozenDailyPickEvidence && predLoading;
  const activeConfidence = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride?.confidence_score ?? null)
    : (matchingSelectedHorizonPrediction?.confidence ?? null);
  // The recommended stop/target the active source suggests — used ONLY to
  // drive the auto-fill effect below (never overwrites a manually-edited
  // field; see that effect's guard). Kept separate from `stopLoss`/
  // `targetPrice` state (the trade's own submitted values) and from
  // `entryEvidence.recommended_stop_loss`/`recommended_target_price` (the
  // persisted evidence fields) — this constant exists purely to give the
  // sync effect one source to react to.
  const activeRecommendedStopLoss = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride?.recommended_stop_loss ?? null)
    : ((matchingSelectedHorizonPrediction as any)?.trade_levels?.stop_loss ?? null);
  const activeRecommendedTargetPrice = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride?.recommended_target_price ?? null)
    : ((matchingSelectedHorizonPrediction as any)?.trade_levels?.take_profit ?? null);
  // The reference price the ACTIVE recommendation source was generated
  // against — used for the "Recommendation was generated at ..." notice.
  // Unchanged DAILY_PICK: the frozen Pick's own reference price (falls back
  // to the caller-supplied `referencePrice` prop only if the override
  // somehow lacks one — never fabricated).
  // Horizon-switched DAILY_PICK or RESEARCH: Fix (race closure) — must NOT
  // fall back to `referencePrice` (the ORIGINAL horizon's reference) while
  // the newly-selected horizon's Prediction is still in flight/unmatched,
  // since that would present a stale prior-horizon price as though it
  // belongs to the new horizon. Only a genuinely matching Prediction's own
  // reference price is used; while awaiting one, this is null (no
  // fabricated placeholder), and the notice below is suppressed entirely
  // during that window (see `usingLivePriceOverReference`).
  const activeReferencePrice = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride?.recommendation_reference_price ?? referencePrice ?? null)
    : (matchingSelectedHorizonPrediction?.current_price ?? null);

  // Fix 5 — sync the stop-loss/target-price INPUT fields (and therefore what
  // actually gets submitted) from the ACTIVE recommendation source above,
  // never straight from `prediction`. Depending on `activeRecommendedStopLoss`/
  // `activeRecommendedTargetPrice`/`usingFrozenDailyPickEvidence` (rather than
  // on `prediction` itself) means this effect correctly re-fires on every
  // relevant transition: a live Prediction resolving, a horizon switch to a
  // new Prediction, AND a horizon switch BACK to the original Daily Pick
  // horizon (`usingFrozenDailyPickEvidence` flips back to true even though
  // the cached Prediction object for that horizon may not have changed
  // identity) — restoring the frozen Pick's own stop/target rather than
  // leaving a leftover alternate-horizon Prediction's levels displayed. As
  // before, a manual edit (`stopLossEdited.current`/`targetPriceEdited.current`)
  // permanently blocks any further automatic overwrite from any source.
  useEffect(() => {
    if (activeRecommendedStopLoss != null && !stopLossEdited.current) {
      setStopLoss(Number(activeRecommendedStopLoss).toFixed(2));
    }
    if (activeRecommendedTargetPrice != null && !targetPriceEdited.current) {
      setTargetPrice(Number(activeRecommendedTargetPrice).toFixed(2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRecommendedStopLoss, activeRecommendedTargetPrice, usingFrozenDailyPickEvidence]);

  const cost = currentPrice * quantity;

  // Fix 6 — the distinction notice now follows the same active source as
  // everything else above (`activeReferencePrice`), not the static
  // `referencePrice` prop directly — after a horizon switch the prop alone
  // could describe a stale reference no longer matching what's actually
  // being persisted. Only shown when the reference price is a real,
  // different number — not for the (already-correct) case where no live
  // quote existed and currentPrice already equals the reference price.
  const usingLivePriceOverReference =
    !isSell &&
    activeReferencePrice != null && Number.isFinite(activeReferencePrice) && activeReferencePrice > 0 &&
    Math.abs(currentPrice - activeReferencePrice) > 0.01;

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
  // `originalHorizon` / `horizonMatchesOriginalPick` / `usingFrozenDailyPickEvidence`
  // are declared above (alongside `activeSignal`/`activeConfidence`) so the
  // AI Signal pill and this persisted-evidence selection share the exact
  // same "is this an unchanged-horizon Daily Pick Buy" condition — they
  // must never disagree.
  const entryEvidence: EntryEvidencePayload | null = usingFrozenDailyPickEvidence
    ? (entryEvidenceOverride ?? null)
    : buildEntryEvidenceFromPrediction(matchingSelectedHorizonPrediction);

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

  // Defense-in-depth (A/B) — the stable "logical Buy operation" identifier
  // for THIS pick, scoped to userId+market+symbol+the ORIGINAL horizon the
  // modal was opened for (not `selectedHorizon`, which the user can change
  // mid-session before submitting — the operation identity must not shift
  // underneath an in-flight lock just because the horizon selector moved).
  // See frontend/src/utils/paperBuyLock.ts for why this lives at module
  // scope instead of component state: it must survive this component
  // unmounting when Cancel/X closes the modal while a Buy is still
  // in-flight, so a remounted modal for the SAME pick recognizes the prior
  // operation as still pending instead of presenting a fresh, unlocked Buy.
  const buyOpKey = buyOperationKey(userId, market, symbol, originalHorizon);
  // True the instant this component mounts (or re-renders) if a PRIOR
  // instance for this exact pick left an operation pending when it
  // unmounted — this is what actually closes the remount gap; local
  // `buyMutation.isPending`/`buySubmissionInFlightRef` alone cannot, since
  // both reset to a blank slate on remount.
  const sharedBuyLock = !isSell ? getBuyLock(buyOpKey) : undefined;
  // A pending lock only blocks THIS instance when it belongs to a
  // DIFFERENT logical attempt than the one this instance itself currently
  // holds — i.e. `frozenBuyRequestRef.current` is null (nothing frozen
  // yet here) or its key doesn't match the lock's key. When they DO match
  // (this instance is the one that reserved the lock, including after an
  // ambiguous/network failure — see the buyMutation.onError lifecycle
  // notes below), the lock must NOT block this instance's own same-key
  // retry click, only a genuinely different instance's fresh attempt.
  const buyLockedByOtherInstance =
    sharedBuyLock?.status === "pending" &&
    frozenBuyRequestRef.current?.idempotency_key !== sharedBuyLock.idempotencyKey;
  // Module-level lock recheck poll — see below (after sellLockedByOtherInstance
  // is computed) for why this exists.
  const [, forceLockRecheck] = useState(0);

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
    // (B) Reuse the shared lock's key when a prior instance of THIS same
    // component (before an unmount/remount via Cancel/X) already froze one
    // for this exact operation and it's still pending — this is what makes
    // the key stable across remount, not just across this instance's own
    // re-renders. Only reused while pending: once the operation reaches a
    // terminal state the lock is released (see buyMutation callbacks
    // below) and this falls through to minting a genuinely new key for the
    // next, later, intentional Buy.
    const lockedKey = getBuyLock(buyOpKey);
    if (lockedKey?.status === "pending" && lockedKey.idempotencyKey && frozenBuyRequestRef.current === null) {
      frozenBuyRequestRef.current = {
        idempotency_key: lockedKey.idempotencyKey,
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
    } else if (frozenBuyRequestRef.current === null || materialInputsSignatureRef.current !== materialSignature) {
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
      // (B/E) Terminal (success) — release the shared lock. A remount of
      // this modal for the same pick after this point (e.g. reopened from
      // Daily Picks again) is a genuinely NEW Buy decision and must mint a
      // new key, not reuse this now-completed one.
      releaseBuyLock(buyOpKey);
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => {
      buySubmissionInFlightRef.current = false;
      // (E) Full key-lifecycle lifecycle decision point — DEFINITIVE vs
      // AMBIGUOUS failure are handled differently on purpose:
      //
      // DEFINITIVE (e.response is present — the backend actually received
      // the request, ran it to completion inside its own atomic
      // transaction, and returned a real HTTP error status: 400
      // insufficient funds/market closed, 409 idempotency conflict, 422
      // validation, etc.). Our backend's transactional design (see
      // backend/api/routers/paper_trading.py's `with conn.transaction():`
      // comment) means a response — success OR error — is authoritative:
      // if we got one at all, the backend definitely knows its own final
      // state, so it is safe to treat this operation as terminal. The
      // shared lock is released so the user can retry as a genuinely NEW
      // logical operation (new key next click); local
      // `frozenBuyRequestRef` is intentionally left alone so a SAME-
      // instance immediate retry (no remount in between) still reuses the
      // same key for the specific "lost-response retry" pattern this
      // suite already covers — reserveBuyLock() on the next click just
      // re-registers that same key against the now-empty lock slot.
      //
      // AMBIGUOUS (e.response is absent — a network error, timeout, or
      // aborted request: the client genuinely does not know whether the
      // backend received/committed the request). Per task requirement
      // (e): do NOT release the lock and do NOT let a later click silently
      // mint a brand-new key here — that would risk a second real trade if
      // the original request actually did land server-side. The lock
      // stays "pending" under the SAME idempotency key. A same-instance
      // retry click is still allowed (see `buyLockedByOtherInstance`'s
      // "belongs to THIS instance" carve-out above) and reuses that exact
      // key — which is exactly what makes it safe: replaying the SAME key
      // against the backend's reservation table either replays the
      // original completed result, reports "already in progress", or
      // proceeds fresh if the original genuinely never reached the
      // server — never a second independent trade. A remounted instance
      // (Cancel/X then reopen) still sees this as locked and blocked,
      // consistent with "no new key on ambiguous failure" — this is the
      // "authoritative reconciliation" the task refers to: the backend's
      // idempotency table, not a client-side timer/poll.
      const isDefinitiveFailure = e?.response != null;
      if (isDefinitiveFailure) {
        releaseBuyLock(buyOpKey);
      }
      setError(e.response?.data?.detail ?? "Failed to place trade — network issue, retry to confirm");
    },
  });

  // (F) Same remount-survival lock as the Buy side, keyed on the specific
  // trade being closed — see closeOperationKey's docstring for why this
  // never carries/sends an idempotency key (close_service.py's row lock +
  // status check is already the authoritative dedup; this only closes the
  // frontend's "reopen after Cancel/X" UI gap).
  const closeOpKey = isSell && existingTradeId != null ? closeOperationKey(userId, existingTradeId) : null;
  const sellLockedByOtherInstance = closeOpKey ? getBuyLock(closeOpKey)?.status === "pending" : false;

  // The shared lock is a plain module-level Map, not React state, so
  // nothing re-renders this instance automatically when a PRIOR instance's
  // in-flight request (the one that left this lock pending) eventually
  // resolves in the background after that prior instance unmounted. Poll
  // at a low frequency purely to pick that up — this only ever runs while
  // this instance believes the operation is locked by someone else, and
  // stops the moment it isn't.
  useEffect(() => {
    if (!buyLockedByOtherInstance && !sellLockedByOtherInstance) return;
    const id = setInterval(() => forceLockRecheck((n) => n + 1), 750);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buyLockedByOtherInstance, sellLockedByOtherInstance]);

  const sellMutation = useMutation({
    mutationFn: () => closePaperTrade(existingTradeId!, userId, currentPrice),
    onSuccess: () => {
      setSuccess(`Sold ${existingQuantity} × ${symbol} @ ${currency}${currentPrice.toLocaleString()}`);
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      if (closeOpKey) releaseBuyLock(closeOpKey);
      setTimeout(onClose, 1500);
    },
    onError: (e: any) => {
      if (closeOpKey) releaseBuyLock(closeOpKey);
      setError(e.response?.data?.detail ?? "Failed to close trade");
    },
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
          <button
            onClick={onClose}
            disabled={buyMutation.isPending || sellMutation.isPending || buyLockedByOtherInstance || sellLockedByOtherInstance}
            title={(buyLockedByOtherInstance || sellLockedByOtherInstance) ? "A purchase for this pick is still processing — please wait" : undefined}
            className="text-gray-500 hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-gray-500">
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
                    onClick={() => {
                      setSelectedHorizon(key);
                      setError(null);
                      stopLossEdited.current = false;
                      targetPriceEdited.current = false;
                      quantityEdited.current = false;
                      // Race closure (4) — mark an explicit horizon-switch
                      // transition as in-flight; cleared automatically above
                      // once a genuine matching Prediction (or the frozen
                      // Daily Pick context) is confirmed active.
                      setHorizonSwitchPending(true);
                      // Race closure (2) — an explicit horizon switch must not
                      // leave the PRIOR horizon's stop/target numbers sitting in
                      // the input fields while the new horizon's Prediction is
                      // still in flight (they'd otherwise be visible/submittable
                      // as if they belonged to the new horizon). Clearing here is
                      // safe even when switching back to the frozen Daily Pick
                      // horizon or to an already-cached horizon: the sync effect
                      // above re-fires synchronously off `activeRecommendedStopLoss`/
                      // `activeRecommendedTargetPrice`/`usingFrozenDailyPickEvidence`
                      // and repopulates immediately once a value is available.
                      setStopLoss("");
                      setTargetPrice("");
                    }}
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
                {isSignalPillLoading ? <Loader2 size={12} className="animate-spin opacity-60" /> : <SignalIcon size={13} />}
                <span className="font-bold">{isSignalPillLoading ? "Loading…" : activeSignal}</span>
                {activeConfidence != null && !isSignalPillLoading && <span className="opacity-60 ml-auto">{activeConfidence}%</span>}
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
                {currency}{activeReferencePrice!.toLocaleString(undefined, { maximumFractionDigits: 2 })}.
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
          {(buyLockedByOtherInstance || sellLockedByOtherInstance) && (
            <p className="text-[10px] text-yellow-300 text-center">
              A {isSell ? "sale" : "purchase"} for this {isSell ? "trade" : "pick"} is still processing — please wait for it to finish before trying again.
            </p>
          )}
          <div className="flex gap-2">
            {/* (E) Option A — Cancel/X are disabled for the whole in-flight
                window, including a window re-opened after a prior instance
                left the shared lock pending. Correctness over
                responsiveness: closing the modal must never make the user
                believe a still-resolving request has been abandoned, since
                the axios POST already in flight cannot be aborted
                server-side-safely from here (no AbortController wiring, and
                even with one, the server may have already committed by the
                time an abort reaches it — see PaperTradeModal.tsx module
                docs / task E rationale). */}
            <button onClick={onClose}
              disabled={buyMutation.isPending || sellMutation.isPending || buyLockedByOtherInstance || sellLockedByOtherInstance}
              title={(buyLockedByOtherInstance || sellLockedByOtherInstance) ? "A purchase for this pick is still processing — please wait" : undefined}
              className="flex-1 px-4 py-2 rounded-xl border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors text-sm disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-gray-400 disabled:hover:border-dark-border">
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
                // (A) The shared-lock check is the layer that actually
                // covers the unmount/remount gap the other three don't:
                // `buySubmissionInFlightRef`/`isPending` are blank on a
                // freshly remounted instance, but `buyLockedByOtherInstance`
                // / `sellLockedByOtherInstance` reflect the module-level
                // lock a PRIOR instance may have left pending.
                if (buySubmissionInFlightRef.current || buyMutation.isPending || sellMutation.isPending) return;
                if (buyLockedByOtherInstance || sellLockedByOtherInstance) return;
                if (!isSell && awaitingMatchingHorizonPrediction) return;
                setError(null);
                if (isSell) {
                  if (closeOpKey) reserveBuyLock(closeOpKey);
                  sellMutation.mutate();
                  return;
                }
                buySubmissionInFlightRef.current = true;
                const req = getOrCreateFrozenBuyRequest();
                reserveBuyLock(buyOpKey, req.idempotency_key);
                buyMutation.mutate(req);
              }}
              disabled={buyMutation.isPending || sellMutation.isPending || marketClosed || (!isSell && awaitingMatchingHorizonPrediction) || buyLockedByOtherInstance || sellLockedByOtherInstance}
              title={marketClosed ? `${market} market is closed` : (!isSell && awaitingMatchingHorizonPrediction) ? "Waiting for the selected horizon's recommendation to load" : (buyLockedByOtherInstance || sellLockedByOtherInstance) ? "Already processing — please wait" : undefined}
              className={clsx("flex-1 px-4 py-2 rounded-xl font-semibold text-sm transition-colors",
                isSell ? "bg-bear hover:bg-red-600 text-white disabled:opacity-50" : "bg-bull hover:bg-green-600 text-white disabled:opacity-50")}>
              {buyMutation.isPending || sellMutation.isPending || buyLockedByOtherInstance || sellLockedByOtherInstance
                ? "Placing…"
                : marketClosed
                  ? "Market Closed"
                  : (!isSell && awaitingMatchingHorizonPrediction)
                    ? "Loading…"
                    : isSell ? `Sell ${existingQuantity} shares` : `Buy ${quantity} shares`}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
