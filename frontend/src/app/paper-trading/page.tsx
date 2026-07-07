"use client";
import { useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useStaggeredQueries } from "@/hooks/useStaggeredQueries";
import Link from "next/link";
import clsx from "clsx";
import {
  TrendingUp, TrendingDown, RotateCcw, ExternalLink, Beaker,
  BarChart2, AlertTriangle, CheckCircle2, Pencil, Check, X,
  Bell, BellOff, ChevronDown, ChevronUp,
} from "lucide-react";
import {
  fetchPaperPortfolio, closePaperTrade, resetPaperPortfolio, editPaperTrade,
  updatePaperTradeManagementMode, updatePaperTradeNotificationPreference,
  fetchQuote, fetchOlderClosedTrades, type PaperTrade, type TradeManagementMode,
  type ClosedTradeHorizonBucket, type ClosedHistoryHorizonKey, type OlderClosedTradesCursor,
} from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { SignalBadge } from "@/components/SignalBadge";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { useMarketOpen } from "@/hooks/useMarketOpen";
import { UnsupportedMarketNotice } from "@/components/UnsupportedMarketNotice";

const MARKETS = [
  { key: "IN" as const, label: "🇮🇳 IN", currency: "₹", locale: "en-IN" },
  { key: "US" as const, label: "🇺🇸 US", currency: "$", locale: "en-US" },
];

const fmt = (n: number, dec = 2, locale = "en-IN") =>
  n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });

const HORIZON_BLOCKS = [
  { key: "short",  label: "Short Term",  sub: "1–5 days",   accent: "border-l-blue-500" },
  { key: "medium", label: "Medium Term", sub: "2–4 weeks",  accent: "border-l-purple-500" },
  { key: "long",   label: "Long Term",   sub: "3–6 months", accent: "border-l-indigo-500" },
] as const;

// Trade History display metadata only (label/sub/accent) — the backend
// (GET /portfolio's closed_trade_history_by_horizon) owns which trades
// belong in each bucket, their counts, and every metric; this array never
// groups or classifies a trade, it only maps an already-backend-assigned
// bucket key to its heading text and accent color, in the fixed
// Short → Medium → Long → Unclassified display order.
const CLOSED_HISTORY_BLOCKS: { key: ClosedHistoryHorizonKey; label: string; sub: string; accent: string }[] = [
  { key: "short",  label: "Short Term",  sub: "1–5 days",   accent: "border-l-blue-500" },
  { key: "medium", label: "Medium Term", sub: "2–4 weeks",  accent: "border-l-purple-500" },
  { key: "long",   label: "Long Term",   sub: "3–6 months", accent: "border-l-indigo-500" },
  { key: "unclassified", label: "Unclassified / Legacy", sub: "Missing or unrecognized horizon", accent: "border-l-gray-500" },
];

// How close the live price is to triggering either the stop loss or the
// target — smallest distance sorts first (most urgent to watch).
function urgencyScore(trade: PaperTrade, livePrice: number | null | undefined): number {
  if (livePrice == null || livePrice === 0) return Infinity;
  const distances: number[] = [];
  if (trade.target_price) distances.push(Math.abs(livePrice - trade.target_price) / livePrice);
  if (trade.stop_loss)    distances.push(Math.abs(livePrice - trade.stop_loss) / livePrice);
  return distances.length ? Math.min(...distances) : Infinity;
}

// Exact stop-loss / target trigger check — distinct from the existing 2%
// "near" proximity warning above. Every StockSense360 paper trade today is a
// long (BUY) position — `trade.signal` is only the AI recommendation shown
// at the time the position was opened (a user can open a BUY position even
// against a SELL/HOLD signal), never the position's own direction, so it
// must not be used to infer long/short here. The `isSellPosition` parameter
// exists purely so this function computes the correct answer the day a real
// short-position type is added (per the spec's "future-proof if SELL
// exists" requirement) — today every caller passes `false`.
function checkExitTrigger(
  trade: PaperTrade,
  livePrice: number | null | undefined,
  isSellPosition: boolean = false,
): "STOP_LOSS" | "TARGET_HIT" | null {
  if (livePrice == null || !Number.isFinite(livePrice) || livePrice <= 0) return null;
  const sl = trade.stop_loss;
  const tp = trade.target_price;
  if (sl != null && sl > 0) {
    const slHit = isSellPosition ? livePrice >= sl : livePrice <= sl;
    if (slHit) return "STOP_LOSS";
  }
  if (tp != null && tp > 0) {
    const tpHit = isSellPosition ? livePrice <= tp : livePrice >= tp;
    if (tpHit) return "TARGET_HIT";
  }
  return null;
}

// Same 2%-proximity definition OpenTradeRow already used inline for its own
// "Near stop loss"/"Near target" labels — pulled up to module scope so the
// Action Queue sort (computed once per open trade at the parent level) and
// the row's own display stay in agreement about what "near" means.
function isNearStopLoss(trade: PaperTrade, livePrice: number | null | undefined): boolean {
  return livePrice != null && trade.stop_loss != null && trade.stop_loss > 0 && livePrice <= trade.stop_loss * 1.02;
}
function isNearTarget(trade: PaperTrade, livePrice: number | null | undefined): boolean {
  return livePrice != null && trade.target_price != null && trade.target_price > 0 && livePrice >= trade.target_price * 0.98;
}

function unrealizedPct(trade: PaperTrade, livePrice: number | null | undefined): number | null {
  if (livePrice == null || !Number.isFinite(livePrice) || trade.entry_price <= 0) return null;
  const pct = (livePrice - trade.entry_price) / trade.entry_price * 100;
  return Number.isFinite(pct) ? pct : null;
}

// Action Queue priority (lower tier number = more urgent, sorts first):
//   1. Manual trade with target/stop-loss reached, awaiting the user's own
//      Close Position / Keep Open decision — the single most actionable state.
//   2. Auto trade at or nearing its stop loss (will self-close imminently —
//      surfaced so the user can watch/intervene before it does).
//   3. Auto trade at or nearing its target (same reasoning, positive case).
//   4. Any other trade near its stop loss.
//   5. Any other trade near its target.
//   6. Biggest unrealized loss %.
//   7. Biggest unrealized gain %.
//   8. Newest trade (no live price yet, or nothing else applies).
function actionQueueTier(trade: PaperTrade, livePrice: number | null | undefined): number {
  const isAuto = trade.trade_management_mode === "auto";
  const trigger = checkExitTrigger(trade, livePrice);
  const nearSL = isNearStopLoss(trade, livePrice);
  const nearTgt = isNearTarget(trade, livePrice);

  if (!isAuto && trigger != null) return 1;
  if (isAuto && (trigger === "STOP_LOSS" || nearSL)) return 2;
  if (isAuto && (trigger === "TARGET_HIT" || nearTgt)) return 3;
  if (nearSL) return 4;
  if (nearTgt) return 5;
  const pct = unrealizedPct(trade, livePrice);
  if (pct != null && pct < 0) return 6;
  if (pct != null && pct > 0) return 7;
  return 8;
}

// Secondary key within a tier — smaller sorts first. Tiers 6/7 rank by move
// magnitude (biggest loss/gain first, per the spec); tier 8 ranks newest
// first; tiers 1-5 fall back to the existing proximity-to-trigger distance
// so multiple simultaneously-urgent trades still order sensibly.
function actionQueueSecondary(trade: PaperTrade, livePrice: number | null | undefined, tier: number): number {
  if (tier === 6 || tier === 7) {
    const pct = unrealizedPct(trade, livePrice) ?? 0;
    return -Math.abs(pct);
  }
  if (tier === 8) {
    return -new Date(trade.opened_at).getTime();
  }
  return urgencyScore(trade, livePrice);
}

// Builds the " at ₹524.78 (+3.4%)" fragment shared by every notification
// that reports a stop-loss/target trigger. % is always computed against
// entry price with the same formula for both target and stop loss — the
// sign (positive for target, negative for stop loss) falls out of the math
// naturally, it is never special-cased. Returns "" (not a partial fragment)
// when the trigger price itself is unavailable, so callers can just append
// this and fall back to their symbol-only message automatically.
function formatTriggerFragment(currency: string, triggerPrice: number | null | undefined, entryPrice: number): string {
  if (triggerPrice == null || !Number.isFinite(triggerPrice) || triggerPrice <= 0) return "";
  const priceStr = `${currency}${triggerPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return ` at ${priceStr}`;
  const pct = (triggerPrice - entryPrice) / entryPrice * 100;
  if (!Number.isFinite(pct)) return ` at ${priceStr}`;
  // "−" (U+2212), not the ASCII hyphen toFixed() would give a negative
  // number — matches this file's existing "−X.X% from entry" convention.
  const pctStr = pct >= 0 ? `+${pct.toFixed(1)}` : `−${Math.abs(pct).toFixed(1)}`;
  return ` at ${priceStr} (${pctStr}%)`;
}

const MANAGEMENT_MODE_LABEL: Record<string, string> = {
  manual: "Manual",
  auto: "Auto",
  ai_assisted: "AI (Coming Soon)",
};

const MANAGEMENT_MODE_PICKER_OPTIONS: { key: "manual" | "auto" | "ai_assisted"; label: string; desc: string; disabled?: boolean }[] = [
  { key: "manual", label: "Manual", desc: "Alerts only" },
  { key: "auto",   label: "Auto",   desc: "Automatically closes trade" },
  { key: "ai_assisted", label: "AI", desc: "Coming Soon", disabled: true },
];

const MANAGEMENT_MODE_CONFIRM_COPY: Record<"manual" | "auto", string> = {
  auto: "Switch to Auto Close? Future stop-loss or target hits will close this paper trade automatically.",
  manual: "Switch to Manual Close? Future stop-loss or target hits will require your confirmation.",
};

// Removed the former page-level "Recent / Biggest Winner / Target Hit /
// Auto Close / ..." Trade History sort control (and its supporting
// closedTradePnlPct/isAutoCloseExecution/sortClosedTrades helpers). Every
// backend closed-trade-history bucket (see GET /portfolio's
// closed_trade_history_by_horizon) is now always newest-first by
// construction (_sort_closed_desc in paper_trading.py), so "Recent" was
// already redundant. The other 8 modes required sorting the *entire*
// mixed closed-trade list in memory — exactly the "recompute from a full
// mixed list" and "prefetch unbounded history" this hardening pass
// removes, and isn't reconstructable from a 5-row-plus-lazy-pages bucket
// without fetching everything up front. Not replaced with a per-bucket
// equivalent since none of this session's requirements call for one.

// Trying this out — easy to remove (just delete this function + its one
// call site below) if it doesn't end up being useful in practice.
function daysSinceLabel(dateStr: string): string {
  const opened = new Date(dateStr);
  opened.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today.getTime() - opened.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

function StatCard({
  label, value, sub, positive, pct,
}: { label: string; value: string; sub?: string; positive?: boolean; pct?: number | null }) {
  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4">
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      <div className="flex items-baseline gap-1.5 flex-wrap">
        <p className={clsx("text-xl font-bold font-mono leading-tight",
          positive === true ? "text-bull" : positive === false ? "text-bear" : "text-white"
        )}>{value}</p>
        {pct != null && (
          <span className={clsx(
            "text-[11px] font-semibold font-mono px-1.5 py-0.5 rounded-md shrink-0",
            pct >= 0 ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear"
          )}>
            {pct >= 0 ? "+" : ""}{pct.toFixed(2)}%
          </span>
        )}
      </div>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

// Scoped to this page only — no other Paper Trading animation currently
// depends on motion preference, so this isn't promoted to a shared hook.
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return reduced;
}

// Tracks which (tradeId, kind) pairs already triggered a browser
// notification this session, so a price hovering near the line doesn't
// re-fire on every 30s poll. Resets on page reload — that's fine, the
// backend email notifier has its own multi-hour cooldown for the
// "even if you closed the tab" case.
const _notifiedThisSession = new Set<string>();

// Guards Auto Close against firing twice for the same trade — e.g. two
// quote refetches landing close together while the close mutation is still
// in flight, before `paper-portfolio` has been invalidated and this row has
// unmounted. Once a trade id is added here it is never removed; a trade can
// only be auto-closed once in its lifetime, and once closed it disappears
// from open_trades permanently (the backend's own `WHERE status = 'OPEN'`
// guard on the sell endpoint is the authoritative, race-proof backstop —
// this is just to avoid firing a redundant request in the common case).
const _autoCloseAttempted = new Set<number>();

function OpenTradeRow({
  trade, onSell, userId, onNotify, notificationsEnabled,
}: {
  trade: PaperTrade;
  onSell: (t: PaperTrade) => void;
  userId: string;
  onNotify: (message: string, tone: "success" | "warning") => void;
  notificationsEnabled: boolean;
}) {
  const currency = trade.market === "IN" ? "₹" : "$";
  const locale = trade.market === "IN" ? "en-IN" : "en-US";
  const fmt = (n: number, dec = 2) => n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });

  const { data: quote } = useQuery({
    queryKey: ["quote", trade.symbol, trade.market],
    queryFn: () => fetchQuote(trade.symbol, trade.market as any),
    refetchInterval: 30_000,
    staleTime: 25_000,
  });

  const livePrice = quote?.price ?? null;
  const unrealizedPnl = livePrice != null ? (livePrice - trade.entry_price) * trade.quantity : null;
  const unrealizedPct = livePrice != null && trade.entry_price > 0 ? ((livePrice - trade.entry_price) / trade.entry_price * 100) : null;
  const nearStopLoss = isNearStopLoss(trade, livePrice);
  const nearTarget   = isNearTarget(trade, livePrice);

  // Paper Trading only supports IN/US today — getMarketStatus/useMarketOpen
  // also accepts "CRYPTO" (always open) so this stays correct unchanged if a
  // crypto asset class is ever added here.
  const marketOpen = useMarketOpen(trade.market as "IN" | "US");
  const prefersReducedMotion = usePrefersReducedMotion();
  // Never animate on an unresolved/unknown status, and never override a
  // user's reduced-motion preference — the static label below still
  // conveys the same information either way.
  const shouldAnimate = marketOpen === true && !prefersReducedMotion;

  // Browser popup notification — fires once per (trade, kind) per session,
  // only while this tab is open, permission has been granted, and the
  // trade's own market is open (a closed-market position shouldn't imply
  // the same "act now" urgency as a live one).
  useEffect(() => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    if (Notification.permission !== "granted") return;
    if (!notificationsEnabled) return;
    if (marketOpen !== true) return;

    if (nearTarget) {
      const key = `${trade.id}-target`;
      if (!_notifiedThisSession.has(key)) {
        _notifiedThisSession.add(key);
        new Notification(`🎯 ${trade.symbol} is near your target`, {
          body: `Live price ${currency}${livePrice?.toLocaleString()} vs target ${currency}${trade.target_price?.toLocaleString()}`,
          tag: key,
        });
      }
    }
    if (nearStopLoss) {
      const key = `${trade.id}-stop`;
      if (!_notifiedThisSession.has(key)) {
        _notifiedThisSession.add(key);
        new Notification(`⚠️ ${trade.symbol} is near your stop loss`, {
          body: `Live price ${currency}${livePrice?.toLocaleString()} vs stop loss ${currency}${trade.stop_loss?.toLocaleString()}`,
          tag: key,
        });
      }
    }
  }, [nearTarget, nearStopLoss, trade.id, trade.symbol, livePrice, currency, trade.target_price, trade.stop_loss, marketOpen, notificationsEnabled]);

  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [epInput, setEpInput] = useState(trade.entry_price.toFixed(2));
  const [slInput, setSlInput] = useState(trade.stop_loss ? trade.stop_loss.toFixed(2) : "");
  const [tpInput, setTpInput] = useState(trade.target_price ? trade.target_price.toFixed(2) : "");

  const editMutation = useMutation({
    mutationFn: () => editPaperTrade(
      trade.id, userId,
      slInput ? parseFloat(slInput) : null,
      tpInput ? parseFloat(tpInput) : null,
      epInput ? parseFloat(epInput) : null,
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setEditing(false);
    },
  });

  // Trade Management mode editing — a small popover on the badge, with a
  // confirmation step before the switch is actually saved (per spec: this
  // is a real behavior change for a live position, not a cosmetic setting).
  const [modePickerOpen, setModePickerOpen] = useState(false);
  const [pendingMode, setPendingMode] = useState<"manual" | "auto" | null>(null);
  const modePickerRef = useRef<HTMLTableCellElement>(null);
  useEffect(() => {
    if (!modePickerOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (modePickerRef.current && !modePickerRef.current.contains(e.target as Node)) {
        setModePickerOpen(false);
        setPendingMode(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [modePickerOpen]);
  const modeMutation = useMutation({
    mutationFn: (mode: "manual" | "auto") => updatePaperTradeManagementMode(trade.id, userId, mode),
    onSuccess: (_data, mode) => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setPendingMode(null);
      setModePickerOpen(false);
      onNotify(
        trade.symbol
          ? `${trade.symbol} trade management set to ${mode === "auto" ? "Auto" : "Manual"}.`
          : `Trade management set to ${mode === "auto" ? "Auto" : "Manual"}.`,
        "success",
      );
    },
    onError: (e: any) => {
      onNotify(e.response?.data?.detail ?? "Failed to update trade management mode.", "warning");
      setPendingMode(null);
    },
  });

  // Exact stop-loss/target hit (not the 2%-proximity nearStopLoss/nearTarget
  // warning above) — this is what actually drives Manual/Auto Close
  // behavior. `false` here is the position-direction flag from
  // checkExitTrigger's signature — always long/BUY today, see its own
  // comment for why.
  const exitTrigger = checkExitTrigger(trade, livePrice, false);

  // Manual mode: the alert banner below reappears every time a new trigger
  // fires, but "Keep Open" should silence *that specific* trigger until the
  // price genuinely moves away and back — not forever, and not for a
  // different trigger (e.g. dismissing a stop-loss alert must not also
  // silence a later target alert).
  const [dismissedTrigger, setDismissedTrigger] = useState<string | null>(null);
  useEffect(() => {
    if (exitTrigger == null) setDismissedTrigger(null);
  }, [exitTrigger]);
  const showManualBanner =
    trade.trade_management_mode !== "auto" &&
    exitTrigger != null &&
    dismissedTrigger !== exitTrigger;

  const closeMutation = useMutation({
    mutationFn: (exitReason: "STOP_LOSS" | "TARGET_HIT" | "MANUAL") =>
      closePaperTrade(trade.id, userId, livePrice!, exitReason),
    onSuccess: (_data, exitReason) => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      const isAuto = trade.trade_management_mode === "auto";
      const isTarget = exitReason === "TARGET_HIT";
      // Falls back to a symbol-only message automatically when livePrice is
      // unavailable (formatTriggerFragment returns "" in that case), and to
      // the fully generic wording below when trade.symbol itself is falsy.
      const symbolPrefix = trade.symbol ? `${trade.symbol} ` : "";
      const triggerFragment = formatTriggerFragment(currency, livePrice, trade.entry_price);
      const actionPhrase = isAuto
        ? `${isTarget ? "target achieved" : "stop loss hit"}${triggerFragment}. Position closed automatically.`
        : `${isTarget ? "target" : "stop loss"} reached${triggerFragment}. Position closed.`;
      onNotify(
        symbolPrefix
          ? `${symbolPrefix}${actionPhrase}`
          : `${actionPhrase.charAt(0).toUpperCase()}${actionPhrase.slice(1)}`,
        exitReason === "STOP_LOSS" ? "warning" : "success",
      );
    },
  });

  // Auto Close: fires the actual close exactly once per trade — guarded by
  // the module-level Set (survives re-renders/refetches within this row's
  // lifetime) and requires a valid live quote (never closes on a missing or
  // stale/zero price) and a non-null trigger (never closes when stop
  // loss/target aren't set — checkExitTrigger already returns null then).
  useEffect(() => {
    if (trade.trade_management_mode !== "auto") return;
    if (exitTrigger == null) return;
    if (livePrice == null || !Number.isFinite(livePrice) || livePrice <= 0) return;
    if (_autoCloseAttempted.has(trade.id)) return;
    _autoCloseAttempted.add(trade.id);
    closeMutation.mutate(exitTrigger);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.trade_management_mode, exitTrigger, livePrice, trade.id]);

  return (
    <>
    <tr className="border-b border-dark-border hover:bg-white/[0.02] transition-colors">
      <td className="px-4 py-3">
        <Link href={`/stock/${trade.symbol}?market=${trade.market}`}
          className="font-bold text-white hover:text-brand-400 flex items-center gap-1">
          {trade.symbol} <ExternalLink size={11} className="opacity-50" />
        </Link>
        <p className="text-xs text-gray-500">{trade.market} · {trade.horizon}</p>
      </td>
      <td className="px-4 py-3 text-sm font-mono">{trade.quantity}</td>
      <td className="px-4 py-3 text-sm font-mono">
        {editing ? (
          <input
            type="number" min={0} step="0.01" value={epInput}
            onChange={e => setEpInput(e.target.value)}
            className="w-28 bg-dark-bg border border-brand-500/60 rounded-lg px-2 py-1 text-xs font-mono text-white focus:outline-none"
            title="Correct entry price — cash is refunded/charged for the difference"
          />
        ) : (
          <span>{currency}{fmt(trade.entry_price)}</span>
        )}
      </td>
      <td className="px-4 py-3">
        {livePrice != null ? (
          <div>
            <span className={clsx(
              "text-sm font-mono font-bold",
              nearStopLoss ? clsx("text-red-400", shouldAnimate && "animate-pulse") :
              nearTarget   ? clsx("text-bull", shouldAnimate && "animate-pulse") : "text-white"
            )}>
              {currency}{fmt(livePrice)}
            </span>
            {/* Day change — helps user understand why price differs from entry */}
            {quote?.change_pct != null && (
              <p className={clsx("text-[10px] font-medium", quote.change_pct >= 0 ? "text-bull/70" : "text-bear/70")}>
                {quote.change_pct >= 0 ? "▲" : "▼"} {Math.abs(quote.change_pct).toFixed(2)}% today
              </p>
            )}
            {nearStopLoss && (
              <p className="text-[10px] text-red-400">
                ⚠ Near stop loss{marketOpen === false ? " (as of last close)" : marketOpen === null ? " (status unavailable)" : ""}
              </p>
            )}
            {nearTarget && (
              <p className="text-[10px] text-bull">
                🎯 Near target{marketOpen === false ? " (as of last close)" : marketOpen === null ? " (status unavailable)" : ""}
              </p>
            )}
          </div>
        ) : (
          <span className="text-xs text-gray-600">Loading…</span>
        )}
      </td>
      <td className="px-4 py-3 text-sm font-mono text-gray-300">
        {currency}{fmt(trade.entry_price * trade.quantity, 0)}
      </td>
      <td className="px-4 py-3">
        {unrealizedPnl != null ? (
          <div>
            <span className={clsx("text-sm font-mono font-bold", unrealizedPnl >= 0 ? "text-bull" : "text-bear")}>
              {unrealizedPnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(unrealizedPnl))}
            </span>
            {unrealizedPct != null && (
              <p className={clsx("text-[10px]", unrealizedPct >= 0 ? "text-bull/70" : "text-bear/70")}>
                {unrealizedPct >= 0 ? "+" : ""}{unrealizedPct.toFixed(2)}%
              </p>
            )}
          </div>
        ) : (
          <span className="text-xs text-gray-600">—</span>
        )}
      </td>
      <td className="px-4 py-3 relative" ref={modePickerRef}>
        <SignalBadge signal={trade.signal as any} size="sm" />
        <button
          type="button"
          onClick={() => setModePickerOpen(o => !o)}
          title="Change trade management mode"
          className={clsx(
            "mt-1 inline-flex items-center gap-0.5 rounded-full border px-1.5 py-0.5 text-[10px] font-medium cursor-pointer transition-colors",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-1 focus-visible:ring-offset-dark-card",
            trade.trade_management_mode === "auto"
              ? "bg-bull/10 border-bull/30 text-bull hover:bg-bull/20"
              : trade.trade_management_mode === "ai_assisted"
                ? "bg-white/5 border-dark-border text-gray-500 hover:bg-white/10"
                : "bg-brand-500/10 border-brand-500/30 text-brand-400 hover:bg-brand-500/20"
          )}
        >
          {MANAGEMENT_MODE_LABEL[trade.trade_management_mode] ?? "Manual"}
          <ChevronDown size={10} className="opacity-70" />
        </button>

        {modePickerOpen && (
          <div className="absolute z-20 top-full left-0 mt-1 w-44 bg-dark-card border border-dark-border rounded-lg shadow-xl p-1.5">
            {pendingMode ? (
              <div className="p-1.5">
                <p className="text-[11px] text-gray-300 leading-snug">
                  {MANAGEMENT_MODE_CONFIRM_COPY[pendingMode]}
                </p>
                <div className="flex gap-1.5 mt-2">
                  <button
                    onClick={() => setPendingMode(null)}
                    className="flex-1 px-2 py-1 rounded-md text-[11px] border border-dark-border text-gray-400 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => modeMutation.mutate(pendingMode)}
                    disabled={modeMutation.isPending}
                    className="flex-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-brand-500 hover:bg-brand-600 text-white transition-colors disabled:opacity-50"
                  >
                    {modeMutation.isPending ? "Saving…" : "Confirm"}
                  </button>
                </div>
              </div>
            ) : (
              MANAGEMENT_MODE_PICKER_OPTIONS.map(({ key, label, desc, disabled }) => (
                <button
                  key={key}
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    if (disabled) return;
                    if (key === trade.trade_management_mode) { setModePickerOpen(false); return; }
                    setPendingMode(key as "manual" | "auto");
                  }}
                  className={clsx(
                    "w-full text-left px-2 py-1.5 rounded-md text-xs transition-colors",
                    disabled
                      ? "text-gray-600 cursor-not-allowed opacity-60"
                      : key === trade.trade_management_mode
                        ? "bg-brand-500/20 text-white"
                        : "text-gray-300 hover:bg-white/5"
                  )}
                >
                  <span className="font-semibold">{label}</span>
                  <span className="block text-[10px] opacity-60">{desc}</span>
                </button>
              ))
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-3">
        {editing ? (
          <div className="flex items-center gap-1">
            <input
              type="number"
              min={0}
              step="0.01"
              value={slInput}
              onChange={e => setSlInput(e.target.value)}
              placeholder="Price"
              autoFocus
              className="w-24 bg-dark-bg border border-yellow-500/50 rounded-lg px-2 py-1 text-xs font-mono text-white focus:outline-none"
            />
            <button
              onClick={() => editMutation.mutate()}
              disabled={editMutation.isPending}
              className="p-1 rounded text-bull hover:bg-bull/10 transition-colors"
            >
              <Check size={13} />
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setSlInput(trade.stop_loss ? trade.stop_loss.toFixed(2) : "");
                setTpInput(trade.target_price ? trade.target_price.toFixed(2) : "");
              }}
              className="p-1 rounded text-gray-400 hover:bg-white/10 transition-colors"
            >
              <X size={13} />
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5 group">
              {trade.stop_loss ? (
                <span className="font-mono text-xs text-yellow-400">
                  {currency}{fmt(trade.stop_loss)}
                </span>
              ) : (
                <span className="text-xs text-gray-600">—</span>
              )}
              <button
                onClick={() => setEditing(true)}
                className="p-0.5 rounded text-gray-500 hover:text-white transition-colors"
                title="Edit stop loss"
              >
                <Pencil size={11} />
              </button>
            </div>
            {trade.stop_loss && trade.stop_loss > 0 && (
              <span className="text-[11px] text-yellow-300/50">
                −{((trade.entry_price - trade.stop_loss) / trade.entry_price * 100).toFixed(1)}%
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-3">
        {editing ? (
          <input
            type="number"
            min={0}
            step="0.01"
            value={tpInput}
            onChange={e => setTpInput(e.target.value)}
            placeholder="Price"
            className="w-24 bg-dark-bg border border-green-500/50 rounded-lg px-2 py-1 text-xs font-mono text-white focus:outline-none"
          />
        ) : (
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-1.5 group">
              {trade.target_price ? (
                <span className="font-mono text-xs text-green-400">
                  {currency}{fmt(trade.target_price)}
                </span>
              ) : (
                <span className="text-xs text-gray-600">—</span>
              )}
              <button
                onClick={() => setEditing(true)}
                className="p-0.5 rounded text-gray-500 hover:text-white transition-colors"
                title="Edit target"
              >
                <Pencil size={11} />
              </button>
            </div>
            {trade.target_price && trade.target_price > 0 && (
              <span className="text-[11px] text-green-300/50">
                +{((trade.target_price - trade.entry_price) / trade.entry_price * 100).toFixed(1)}%
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-300">
        {new Date(trade.opened_at).toLocaleDateString("en-IN")}
        <p className="text-xs font-medium text-brand-400 mt-0.5">{daysSinceLabel(trade.opened_at)}</p>
      </td>
      <td className="px-4 py-3 text-right">
        <button
          onClick={() => onSell({ ...trade, _livePrice: livePrice } as any)}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-bear/10 border border-bear/30 text-red-400 hover:bg-bear/20 transition-colors"
        >
          Close
        </button>
      </td>
    </tr>
    {/* Manual Close alert — only when this trade's own mode is manual (or
        the not-yet-functional ai_assisted, which falls back to alerting
        rather than doing nothing) and a trigger hasn't already been
        dismissed for this specific hit. */}
    {showManualBanner && exitTrigger && (
      <tr className="border-b border-dark-border bg-dark-bg/40">
        <td colSpan={11} className="px-4 py-2">
          <div className={clsx(
            "flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-xs border flex-wrap",
            exitTrigger === "STOP_LOSS" ? "bg-bear/10 border-bear/30 text-red-300" : "bg-bull/10 border-bull/30 text-bull"
          )}>
            <span className="flex items-center gap-1.5">
              <AlertTriangle size={13} className="shrink-0" />
              {(() => {
                const isTarget = exitTrigger === "TARGET_HIT";
                const symbolPrefix = trade.symbol ? `${trade.symbol} ` : "";
                const triggerFragment = formatTriggerFragment(currency, livePrice, trade.entry_price);
                const actionPhrase = `${isTarget ? "target" : "stop loss"} reached${triggerFragment}.`;
                return symbolPrefix
                  ? `${symbolPrefix}${actionPhrase}`
                  : `${actionPhrase.charAt(0).toUpperCase()}${actionPhrase.slice(1)}`;
              })()} Close position?
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => closeMutation.mutate(exitTrigger)}
                disabled={closeMutation.isPending}
                className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-white/10 hover:bg-white/20 text-white transition-colors disabled:opacity-50"
              >
                Close Position
              </button>
              <button
                onClick={() => setDismissedTrigger(exitTrigger)}
                className="px-2.5 py-1 rounded-lg text-[11px] font-medium border border-current/30 hover:bg-white/5 transition-colors"
              >
                Keep Open
              </button>
            </div>
          </div>
        </td>
      </tr>
    )}
    </>
  );
}

function ClosedTradeRow({ trade }: { trade: PaperTrade }) {
  const currency = trade.market === "IN" ? "₹" : "$";
  const locale = trade.market === "IN" ? "en-IN" : "en-US";
  const fmt = (n: number, dec = 2) => n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const pnl = trade.realized_pnl ?? 0;
  const pnlPct = trade.entry_price > 0
    ? ((trade.exit_price! - trade.entry_price) / trade.entry_price * 100)
    : 0;
  return (
    <tr className="border-b border-dark-border last:border-0 hover:bg-white/[0.02] transition-colors">
      <td className="px-4 py-3">
        <Link href={`/stock/${trade.symbol}?market=${trade.market}`}
          className="font-bold text-white hover:text-brand-400 flex items-center gap-1">
          {trade.symbol} <ExternalLink size={11} className="opacity-50" />
        </Link>
        <p className="text-xs text-gray-500">{trade.market} · {trade.horizon}</p>
      </td>
      <td className="px-4 py-3 text-sm font-mono">{trade.quantity}</td>
      <td className="px-4 py-3 text-sm font-mono">{currency}{fmt(trade.entry_price)}</td>
      <td className="px-4 py-3 text-sm font-mono">{currency}{fmt(trade.exit_price ?? 0)}</td>
      <td className="px-4 py-3">
        <span className={clsx("text-sm font-bold font-mono", pnl >= 0 ? "text-bull" : "text-bear")}>
          {pnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(pnl))}
          <span className="text-xs font-normal ml-1 opacity-80">
            ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
          </span>
        </span>
      </td>
      <td className="px-4 py-3">
        {trade.target_price && trade.exit_price ? (
          trade.exit_price >= trade.target_price ? (
            <span className="flex items-center gap-1 text-xs text-bull font-medium">
              <Check size={12} /> Hit
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-gray-500">
              <X size={12} /> Missed
            </span>
          )
        ) : (
          <span className="text-xs text-gray-600">—</span>
        )}
        {trade.exit_reason && (
          <p className="text-[10px] text-gray-600 mt-0.5">
            {trade.exit_reason === "STOP_LOSS" ? "Stop loss"
              : trade.exit_reason === "TARGET_HIT" ? "Target"
              : "Manual"}
          </p>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {trade.closed_at ? new Date(trade.closed_at).toLocaleDateString("en-IN") : "—"}
      </td>
    </tr>
  );
}

// One horizon's Trade History block. `bucket` is rendered verbatim from
// GET /portfolio's closed_trade_history_by_horizon — this component never
// sorts, groups, classifies, or slices a full closed-trade list; it only
// (a) shows the backend-computed summary/latest_trades/earlier_trade_count,
// and (b) lazily fetches additional pages of *that exact horizon's* older
// trades via fetchOlderClosedTrades only once the user expands the
// "Show N earlier" control — never prefetched, never re-fetched on a
// second expand (already-loaded pages stay in local state).
function ClosedTradeHorizonBlock({
  market, horizon, label, sub, accent, bucket, currency, blockExpanded, onToggleBlock,
}: {
  market: PaperTrade["market"]; horizon: ClosedHistoryHorizonKey;
  label: string; sub: string; accent: string;
  bucket: ClosedTradeHorizonBucket; currency: string;
  blockExpanded: boolean; onToggleBlock: () => void;
}) {
  const { summary, latest_trades, earlier_trade_count } = bucket;
  const netPnl = summary.net_realized_pnl;

  const [olderTrades, setOlderTrades] = useState<PaperTrade[]>([]);
  const [cursor, setCursor] = useState<OlderClosedTradesCursor | null>(null);
  const [hasMore, setHasMore] = useState(earlier_trade_count > 0);
  const [olderVisible, setOlderVisible] = useState(false);
  const [olderLoaded, setOlderLoaded] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const loadNextPage = async () => {
    setLoadingOlder(true);
    try {
      const res = await fetchOlderClosedTrades(market, horizon, cursor);
      // Append-only, keyed by id below on render — a duplicate id can only
      // occur if the same page were fetched twice, which the cursor
      // (always advanced from the last-seen row) prevents by construction.
      setOlderTrades(prev => [...prev, ...res.trades]);
      setCursor(res.next_cursor);
      setHasMore(res.has_more);
      setOlderLoaded(true);
      setOlderVisible(true);
    } finally {
      setLoadingOlder(false);
    }
  };

  const remainingCount = earlier_trade_count - olderTrades.length;

  return (
    <div className={clsx("bg-dark-card border border-dark-border rounded-xl overflow-hidden border-l-4", accent)}>
      <button
        onClick={onToggleBlock}
        aria-expanded={blockExpanded}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          {blockExpanded ? <ChevronUp size={15} className="text-gray-500 shrink-0" /> : <ChevronDown size={15} className="text-gray-500 shrink-0" />}
          <span className="font-semibold text-sm text-white">{label}</span>
          <span className="text-xs text-gray-500">({sub})</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap text-xs text-gray-400 justify-end">
          <span>{summary.closed_trade_count} closed</span>
          <span>
            Target Hit Rate{" "}
            {summary.target_hit_rate_pct !== null
              ? <span className="text-gray-300 font-medium">{summary.target_hit_count} / {summary.conclusive_count} · {summary.target_hit_rate_pct.toFixed(1)}%</span>
              : <span className="text-gray-600">— no conclusive outcomes</span>}
          </span>
          <span className={clsx("font-mono font-bold", netPnl > 0 ? "text-bull" : netPnl < 0 ? "text-bear" : "text-gray-400")}>
            Net P&L {netPnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(netPnl), 0)}
          </span>
        </div>
      </button>

      {blockExpanded && (
        <div className="border-t border-dark-border">
          <div className="px-4 py-2 flex items-center gap-4 flex-wrap text-[11px] text-gray-500 border-b border-dark-border/60">
            <span>Stop-loss outcomes: <span className="text-gray-400">{summary.stop_loss_count} / {summary.conclusive_count || 0}</span></span>
            <span>Other / non-conclusive: <span className="text-gray-400">{summary.other_count}</span></span>
            <span>
              Avg realized return:{" "}
              <span className="text-gray-400">
                {summary.avg_realized_return_pct !== null
                  ? `${summary.avg_realized_return_pct >= 0 ? "+" : ""}${summary.avg_realized_return_pct.toFixed(2)}%`
                  : "—"}
              </span>
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-dark-border text-xs text-gray-500">
                  <th className="px-4 py-2.5 text-left">Stock</th>
                  <th className="px-4 py-2.5 text-left">Qty</th>
                  <th className="px-4 py-2.5 text-left">Entry</th>
                  <th className="px-4 py-2.5 text-left">Exit</th>
                  <th className="px-4 py-2.5 text-left">P&L</th>
                  <th className="px-4 py-2.5 text-left">vs Target</th>
                  <th className="px-4 py-2.5 text-left">Closed</th>
                </tr>
              </thead>
              <tbody>
                {latest_trades.map(t => <ClosedTradeRow key={t.id} trade={t} />)}
                {olderVisible && olderTrades.map(t => <ClosedTradeRow key={t.id} trade={t} />)}
              </tbody>
            </table>
          </div>
          {earlier_trade_count > 0 && (
            <button
              onClick={() => {
                if (!olderLoaded) { loadNextPage(); return; }       // first expand — fetch page 1
                if (!olderVisible) { setOlderVisible(true); return; } // re-show already-fetched rows, no re-fetch
                if (hasMore) { loadNextPage(); return; }              // fetch the next page, append
                setOlderVisible(false);                               // fully exhausted — collapse
              }}
              disabled={loadingOlder}
              aria-expanded={olderVisible}
              className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs text-gray-400 hover:text-white border-t border-dark-border transition-colors disabled:opacity-50"
            >
              {loadingOlder
                ? "Loading…"
                : !olderLoaded
                  ? <>Show {earlier_trade_count} earlier closed trade{earlier_trade_count === 1 ? "" : "s"} <ChevronDown size={13} /></>
                  : !olderVisible
                    ? <>Show {olderTrades.length} earlier closed trade{olderTrades.length === 1 ? "" : "s"} <ChevronDown size={13} /></>
                    : hasMore
                      ? <>Show {remainingCount} more earlier closed trade{remainingCount === 1 ? "" : "s"} <ChevronDown size={13} /></>
                      : <>Hide earlier trades <ChevronUp size={13} /></>}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function PaperTradingPage() {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const queryClient = useQueryClient();
  const [market] = useMarketPreference(["IN", "US"] as const, "IN");
  const marketCfg = MARKETS.find(m => m.key === market)!;
  const [sellTarget, setSellTarget] = useState<PaperTrade | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [historyExpanded, setHistoryExpanded] = useState(true);
  // Per-horizon UI-only visibility state — never used to compute a metric,
  // only which block is currently rendered expanded. Every non-empty
  // horizon block starts expanded by default. Older-rows visibility/paging
  // state lives inside ClosedTradeHorizonBlock itself (per-horizon, since
  // it now owns its own lazy fetch of that horizon's older trades).
  const [collapsedHorizonBlocks, setCollapsedHorizonBlocks] = useState<Partial<Record<ClosedHistoryHorizonKey, boolean>>>({});
  const [notifPermission, setNotifPermission] = useState<NotificationPermission | "unsupported">("default");

  // Close-position outcome banners (Manual Close confirmations and Auto
  // Close notifications) — kept at page level, not inside OpenTradeRow,
  // because the row that triggered the message unmounts the moment the
  // trade moves from open_trades to closed_trades.
  const [notifications, setNotifications] = useState<{ id: number; message: string; tone: "success" | "warning" }[]>([]);
  const notifyIdRef = useRef(0);
  const pushNotification = (message: string, tone: "success" | "warning") => {
    const id = ++notifyIdRef.current;
    setNotifications(prev => [...prev, { id, message, tone }]);
  };
  const dismissNotification = (id: number) => setNotifications(prev => prev.filter(n => n.id !== id));

  useEffect(() => {
    if (typeof window !== "undefined" && "Notification" in window) {
      setNotifPermission(Notification.permission);
    } else {
      setNotifPermission("unsupported");
    }
  }, []);

  // Persisted (backend) preference — the single source of truth for whether
  // Paper Trading emails go out at all. Browser Notification.permission is a
  // separate, device-local concern layered on top of it (see the toggle
  // button below): turning this off suppresses both backend emails and this
  // tab's own popups even if browser permission remains granted, since we
  // simply stop calling new Notification() when it's false.
  const notifPrefMutation = useMutation({
    mutationFn: (enabled: boolean) => updatePaperTradeNotificationPreference(userId, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] }),
  });

  const requestNotifications = async () => {
    if (typeof window !== "undefined" && "Notification" in window) {
      const result = await Notification.requestPermission();
      setNotifPermission(result);
    }
    notifPrefMutation.mutate(true);
  };

  const disableNotifications = () => notifPrefMutation.mutate(false);

  const { data: portfolio, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["paper-portfolio", userId],
    queryFn: () => fetchPaperPortfolio(userId, user?.email),
    enabled: !!userId,
    refetchInterval: 30_000,
  });

  const resetMutation = useMutation({
    mutationFn: () => resetPaperPortfolio(userId, market),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setShowResetConfirm(false);
    },
  });

  // Must be called before any early return — hooks must always run in the same order.
  // Staggered, not plain useQueries — firing one quote request per open trade
  // (refetching every 30s, on top of that) saturates the browser's per-origin
  // connection cap once there are more than a handful of positions, which can
  // leave an unrelated request (like closing a position) stuck queued behind
  // them even though the backend itself responds quickly.
  const quoteResults = useStaggeredQueries(
    (portfolio?.open_trades ?? []).map((t, i) => ({
      queryKey: ["quote", t.symbol, t.market],
      queryFn: () => fetchQuote(t.symbol, t.market as any),
      staleTime: 25_000,
      // Jittered by index, not a flat 30s for every query — once the
      // initial staggered load fully unlocks, an unjittered interval would
      // still mean every query refetches at the exact same moment, every
      // 30s, indefinitely (the same connection-saturation problem, just
      // recurring instead of one-time).
      refetchInterval: 28_000 + (i % 8) * 500,
    })),
    8
  );

  // Checked before the loading branch below — once a query settles to an
  // error (after React Query's own retry budget is exhausted), isLoading is
  // already false but `portfolio` stays undefined. Without this check that
  // combination fell through to the generic "Loading…" state forever, with
  // no indication anything had failed and no way to recover short of a full
  // page reload.
  if (isError && !portfolio) {
    return (
      <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
        <div className="text-center max-w-sm px-4">
          <AlertTriangle size={32} className="mx-auto mb-3 text-bear opacity-70" />
          <p className="text-white font-medium">Unable to load paper portfolio.</p>
          <p className="text-sm text-gray-500 mt-1">
            Please retry. If the server is waking up, this may take a few seconds.
          </p>
          {error && (
            <p className="text-xs text-gray-600 mt-2 font-mono break-words">
              {(error as any)?.response?.data?.detail ?? (error as any)?.message ?? "Unknown error"}
            </p>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold transition-colors disabled:opacity-50"
          >
            <RotateCcw size={13} className={isFetching ? "animate-spin" : undefined} />
            {isFetching ? "Retrying…" : "Retry"}
          </button>
        </div>
      </div>
    );
  }

  if (isLoading || !portfolio) {
    return (
      <div className="flex items-center justify-center min-h-[40vh] text-gray-400">
        <div className="text-center">
          <Beaker size={32} className="mx-auto mb-3 opacity-40" />
          <p>Loading paper portfolio…</p>
        </div>
      </div>
    );
  }

  // quoteResults indices line up with portfolio.open_trades (unfiltered) —
  // build a lookup so per-market filtering below doesn't lose the live price.
  const priceByTradeId = new Map<number, number | null>();
  portfolio.open_trades.forEach((t, i) => priceByTradeId.set(t.id, quoteResults[i]?.data?.price ?? null));

  // Everything below is scoped to the selected market — IN (₹) and US ($) are
  // separate ledgers and must never be summed together.
  const openTrades = portfolio.open_trades.filter(t => t.market === market);
  // Retained only for the overall headline count/P&L and win-rate stat card
  // below (both pre-existing, out of this hardening pass's scope) — Trade
  // History's per-horizon sections read closed_trade_history_by_horizon
  // instead and never group/slice this flat list themselves.
  const closedTrades = portfolio.closed_trades.filter(t => t.market === market);
  const cash = market === "IN" ? portfolio.cash : portfolio.cash_usd;
  const startingCash = market === "IN" ? portfolio.starting_cash : portfolio.starting_cash_usd;
  const totalRealized = market === "IN" ? portfolio.total_realized_pnl : portfolio.total_realized_pnl_usd;

  // Trade History by horizon — the entire bucket (summary + latest 5 rows +
  // earlier_trade_count) comes verbatim from the backend for the selected
  // market. This page performs no grouping, sorting, classification, or
  // slicing of a mixed closed-trade list to build these sections.
  const marketClosedHistory = portfolio.closed_trade_history_by_horizon[market];

  // Group open positions into Short/Medium/Long blocks, each sorted by
  // Action Queue priority — how urgently the trade needs the user's
  // attention, not just raw distance to its trigger. See actionQueueTier's
  // own comment for the exact 8-tier ordering.
  const groupedOpenTrades = HORIZON_BLOCKS.map(block => ({
    ...block,
    trades: openTrades
      .map(trade => ({ trade, livePrice: priceByTradeId.get(trade.id) ?? null }))
      .filter(({ trade }) => trade.horizon === block.key)
      .sort((a, b) => {
        const tierA = actionQueueTier(a.trade, a.livePrice);
        const tierB = actionQueueTier(b.trade, b.livePrice);
        if (tierA !== tierB) return tierA - tierB;
        return actionQueueSecondary(a.trade, a.livePrice, tierA) - actionQueueSecondary(b.trade, b.livePrice, tierB);
      })
      .map(({ trade }) => trade),
  }));

  const totalUnrealizedPnl = openTrades.reduce((sum, trade) => {
    const price = priceByTradeId.get(trade.id);
    if (price == null || trade.entry_price == null) return sum;
    return sum + (price - trade.entry_price) * trade.quantity;
  }, 0);
  const unrealizedLoaded = openTrades.some(t => priceByTradeId.get(t.id) != null);

  const totalInvested = openTrades.reduce((s, t) => s + t.invested, 0);
  const unrealizedPct = totalInvested > 0 ? (totalUnrealizedPnl / totalInvested) * 100 : null;
  const totalClosedInvested = closedTrades.reduce((s, t) => s + t.invested, 0);
  const realizedPct = totalClosedInvested > 0 ? (totalRealized / totalClosedInvested) * 100 : null;
  const portfolioValue = cash + totalInvested;
  const totalReturn = portfolioValue - startingCash + totalRealized;
  const totalReturnPct = Math.round((totalReturn / startingCash) * 10000) / 100;

  const winTrades = closedTrades.filter(t => (t.realized_pnl ?? 0) > 0).length;
  const winRate = closedTrades.length > 0 ? (winTrades / closedTrades.length * 100) : null;

  return (
    <div className="space-y-6">
      <UnsupportedMarketNotice supported={["IN", "US"]} />
      {/* Trade Management outcome banners — Manual Close confirmations and
          Auto Close notifications, dismissible, stacked newest-last. */}
      {notifications.length > 0 && (
        <div className="space-y-2">
          {notifications.map(n => (
            <div key={n.id} className={clsx(
              "flex items-center justify-between gap-3 rounded-lg px-4 py-2.5 text-sm border",
              n.tone === "warning" ? "bg-bear/10 border-bear/30 text-red-300" : "bg-bull/10 border-bull/30 text-bull"
            )}>
              <span>{n.message}</span>
              <button onClick={() => dismissNotification(n.id)} className="text-current opacity-60 hover:opacity-100 transition-opacity">
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Beaker size={24} className="text-brand-400" />
            Paper Trading
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Practice with {marketCfg.currency}{fmt(startingCash, 0, marketCfg.locale)} virtual money · No real funds involved
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* IN and US are separate cash ledgers. Market is now chosen once,
              globally, via the header's GlobalMarketDropdown — this
              read-only label just shows which ledger is currently in view
              rather than offering a second, page-level way to change it. */}
          <span className="shrink-0 whitespace-nowrap text-xs px-3 py-1.5 rounded-lg bg-dark-card border border-dark-border text-gray-400">
            Market: {marketCfg.label}
          </span>
          {notifPermission !== "unsupported" && (() => {
            // The persisted backend preference is the single source of
            // truth for "on" — browser permission is layered on top only to
            // distinguish "on, and this tab can also show popups" from "on,
            // but this browser has blocked/not-yet-granted popups".
            const emailEnabled = portfolio.email_notifications_enabled;
            const blocked = notifPermission === "denied";
            const on = emailEnabled && !blocked;
            return (
              <button
                onClick={blocked ? undefined : (on ? disableNotifications : requestNotifications)}
                disabled={blocked || notifPrefMutation.isPending}
                title={
                  blocked
                    ? "Notifications blocked — enable them in your browser's site settings"
                    : on
                      ? "Controls routine paper-trading alerts. Auto-close execution confirmations are always sent. Click to turn off."
                      : "Controls routine paper-trading alerts. Auto-close execution confirmations are always sent. Click to turn on."
                }
                className={clsx(
                  "flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-colors disabled:opacity-60",
                  on
                    ? "border-bull/30 text-bull bg-bull/10 hover:bg-bull/20"
                    : blocked
                      ? "border-dark-border text-gray-600 cursor-not-allowed"
                      : "border-dark-border text-gray-400 hover:text-white hover:border-white/30"
                )}
              >
                {on ? <Bell size={13} /> : <BellOff size={13} />}
                {blocked ? "Notifications Blocked" : on ? "Notifications On" : "Notifications Off"}
              </button>
            );
          })()}
          <button
            onClick={() => setShowResetConfirm(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors"
          >
            <RotateCcw size={13} />
            Reset Portfolio
          </button>
        </div>
      </div>

      {/* Reset confirm */}
      {showResetConfirm && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-yellow-300 text-sm">
            <AlertTriangle size={16} />
            This will erase your {market} trades and reset to {marketCfg.currency}{fmt(startingCash, 0, marketCfg.locale)}.
            Your other market's portfolio is untouched. Are you sure?
          </div>
          <div className="flex gap-2 ml-4">
            <button onClick={() => setShowResetConfirm(false)}
              className="px-3 py-1.5 text-xs rounded-lg border border-dark-border text-gray-400 hover:text-white">
              Cancel
            </button>
            <button onClick={() => resetMutation.mutate()}
              disabled={resetMutation.isPending}
              className="px-3 py-1.5 text-xs rounded-lg bg-yellow-500 text-black font-semibold hover:bg-yellow-400 disabled:opacity-60">
              {resetMutation.isPending ? "Resetting…" : "Yes, Reset"}
            </button>
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatCard
          label={`Virtual Cash (${market})`}
          value={`${marketCfg.currency}${fmt(cash, 0, marketCfg.locale)}`}
          sub={`of ${marketCfg.currency}${fmt(startingCash, 0, marketCfg.locale)} starting`}
        />
        <StatCard
          label="Invested"
          value={`${marketCfg.currency}${fmt(totalInvested, 0, marketCfg.locale)}`}
          sub={`${openTrades.length} open position${openTrades.length !== 1 ? "s" : ""}`}
        />
        <StatCard
          label="Unrealized P&L"
          value={unrealizedLoaded
            ? `${totalUnrealizedPnl >= 0 ? "+" : ""}${marketCfg.currency}${fmt(Math.abs(totalUnrealizedPnl), 0, marketCfg.locale)}`
            : "—"}
          pct={unrealizedLoaded ? unrealizedPct : null}
          sub={unrealizedLoaded ? `across ${openTrades.length} open position${openTrades.length !== 1 ? "s" : ""}` : "Loading…"}
          positive={unrealizedLoaded ? (totalUnrealizedPnl > 0 ? true : totalUnrealizedPnl < 0 ? false : undefined) : undefined}
        />
        <StatCard
          label="Realized P&L"
          value={`${totalRealized >= 0 ? "+" : ""}${marketCfg.currency}${fmt(Math.abs(totalRealized), 0, marketCfg.locale)}`}
          pct={realizedPct}
          sub={`from ${closedTrades.length} closed trade${closedTrades.length !== 1 ? "s" : ""}`}
          positive={totalRealized > 0 ? true : totalRealized < 0 ? false : undefined}
        />
        <StatCard
          label="Win Rate"
          value={winRate !== null ? `${winRate.toFixed(0)}%` : "—"}
          sub={winRate !== null ? `${winTrades} of ${closedTrades.length} trades` : "No closed trades yet"}
          positive={winRate !== null ? winRate >= 50 : undefined}
        />
      </div>

      {/* Disclaimer */}
      <div className="flex items-start gap-2 bg-dark-card border border-dark-border rounded-xl px-4 py-3 text-xs text-gray-500">
        <CheckCircle2 size={14} className="text-brand-400 mt-0.5 shrink-0" />
        Paper trading simulates real trades using live prices. Results do not guarantee future performance.
        Go to any stock page and click <strong className="text-gray-400">Paper Trade</strong> to open a position.
        You'll get an email whenever an open position nears its target or stop loss — enable the button above for an in-browser popup too.
      </div>

      {/* Open Positions */}
      <div>
        <h2 className="font-bold text-base mb-3 flex items-center gap-2">
          <TrendingUp size={16} className="text-bull" />
          Open Positions
          {openTrades.length > 0 && (
            <span className="bg-bull/20 text-bull text-xs px-2 py-0.5 rounded-full font-medium">
              {openTrades.length}
            </span>
          )}
        </h2>
        {openTrades.length === 0 ? (
          <div className="bg-dark-card border border-dark-border rounded-xl px-6 py-10 text-center text-gray-500">
            <Beaker size={28} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">No open positions.</p>
            <p className="text-xs mt-1">Browse a stock and click <strong>Paper Trade</strong> to start.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {groupedOpenTrades.map(({ key, label, sub, accent, trades }) => trades.length > 0 && (
              <div key={key} className={clsx("bg-dark-card border border-dark-border rounded-xl overflow-hidden border-l-4", accent)}>
                <div className="px-4 py-2.5 flex items-center gap-2 border-b border-dark-border bg-white/[0.02]">
                  <h3 className="font-semibold text-sm text-white">{label}</h3>
                  <span className="text-xs text-gray-500">({sub})</span>
                  <span className="bg-white/10 text-gray-300 text-[11px] px-1.5 py-0.5 rounded-full font-medium ml-1">
                    {trades.length}
                  </span>
                  <span className="text-[11px] text-gray-400 ml-auto">Sorted by action priority</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-dark-border text-xs text-gray-500">
                        <th className="px-4 py-2.5 text-left">Stock</th>
                        <th className="px-4 py-2.5 text-left">Qty</th>
                        <th className="px-4 py-2.5 text-left">Entry</th>
                        <th className="px-4 py-2.5 text-left max-w-[120px]">
                          Mkt Price
                          <span className="block text-gray-400 font-normal text-[10px] leading-tight">(last close when closed)</span>
                        </th>
                        <th className="px-4 py-2.5 text-left">Invested</th>
                        <th className="px-4 py-2.5 text-left">Unr. P&L</th>
                        <th className="px-4 py-2.5 text-left" title="The AI signal at the moment you opened this trade — frozen, not re-evaluated live. It can differ from what the stock's current signal shows today; check the stock's own page for the up-to-date call.">
                          Entry Signal
                        </th>
                        <th className="px-4 py-2.5 text-left">Stop Loss</th>
                        <th className="px-4 py-2.5 text-left">Target</th>
                        <th className="px-4 py-2.5 text-left">Date</th>
                        <th className="px-4 py-2.5 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.map(t => (
                        <OpenTradeRow key={t.id} trade={t} onSell={setSellTarget} userId={userId} onNotify={pushNotification} notificationsEnabled={portfolio.email_notifications_enabled} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Trade History */}
      {closedTrades.length > 0 && (
        <div>
          <div className="flex items-center gap-2 flex-wrap mb-3">
            <button
              onClick={() => setHistoryExpanded(v => !v)}
              className="font-bold text-base flex items-center gap-2 hover:text-gray-300 transition-colors"
            >
              <BarChart2 size={16} className="text-gray-400" />
              Trade History
              <span className="text-xs text-gray-500 font-normal">({closedTrades.length})</span>
              <span className={clsx("text-sm font-mono font-bold", totalRealized > 0 ? "text-bull" : totalRealized < 0 ? "text-bear" : "text-gray-400")}>
                {totalRealized >= 0 ? "+" : ""}{marketCfg.currency}{fmt(Math.abs(totalRealized), 0, marketCfg.locale)}
              </span>
              {historyExpanded ? <ChevronUp size={16} className="text-gray-500" /> : <ChevronDown size={16} className="text-gray-500" />}
            </button>
          </div>
          {historyExpanded && (
            <div className="space-y-3">
              {CLOSED_HISTORY_BLOCKS.map(({ key, label, sub, accent }) => {
                const bucket = marketClosedHistory[key];
                // "Unclassified / Legacy" only renders at all when the
                // backend actually reports such a trade for this market —
                // never added as an empty fourth section.
                if (!bucket || bucket.summary.closed_trade_count === 0) return null;
                return (
                  <ClosedTradeHorizonBlock
                    key={key}
                    market={market}
                    horizon={key}
                    label={label}
                    sub={sub}
                    accent={accent}
                    bucket={bucket}
                    currency={marketCfg.currency}
                    blockExpanded={collapsedHorizonBlocks[key] !== true}
                    onToggleBlock={() => setCollapsedHorizonBlocks(prev => ({ ...prev, [key]: prev[key] !== true }))}
                  />
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Sell modal */}
      {sellTarget && (
        <PaperTradeModal
          symbol={sellTarget.symbol}
          market={sellTarget.market}
          currentPrice={(sellTarget as any)._livePrice ?? sellTarget.entry_price}
          signal={sellTarget.signal}
          horizon={sellTarget.horizon}
          currency={sellTarget.market === "IN" ? "₹" : "$"}
          onClose={() => setSellTarget(null)}
          existingTradeId={sellTarget.id}
          existingQuantity={sellTarget.quantity}
          existingEntryPrice={sellTarget.entry_price}
        />
      )}
    </div>
  );
}
