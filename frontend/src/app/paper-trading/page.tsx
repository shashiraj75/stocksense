"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import clsx from "clsx";
import {
  TrendingUp, TrendingDown, RotateCcw, ExternalLink, Beaker,
  BarChart2, AlertTriangle, CheckCircle2, ShieldAlert, Pencil, Check, X, Target,
} from "lucide-react";
import {
  fetchPaperPortfolio, closePaperTrade, resetPaperPortfolio, editPaperTrade,
  fetchQuote, type PaperTrade,
} from "@/utils/api";
import { useSessionId } from "@/hooks/useSessionId";
import { PaperTradeModal } from "@/components/PaperTradeModal";
import { SignalBadge } from "@/components/SignalBadge";

const STARTING_CASH = 1_000_000;
const fmt = (n: number, dec = 2) =>
  n.toLocaleString("en-IN", { minimumFractionDigits: dec, maximumFractionDigits: dec });

function StatCard({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4">
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      <p className={clsx("text-xl font-bold font-mono",
        positive === true ? "text-bull" : positive === false ? "text-bear" : "text-white"
      )}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function OpenTradeRow({ trade, onSell, sessionId }: { trade: PaperTrade; onSell: (t: PaperTrade) => void; sessionId: string }) {
  const currency = trade.market === "IN" ? "₹" : "$";

  const { data: quote } = useQuery({
    queryKey: ["quote", trade.symbol, trade.market],
    queryFn: () => fetchQuote(trade.symbol, trade.market as any),
    refetchInterval: 30_000,
    staleTime: 25_000,
  });

  const livePrice = quote?.price ?? null;
  const unrealizedPnl = livePrice != null ? (livePrice - trade.entry_price) * trade.quantity : null;
  const unrealizedPct = livePrice != null ? ((livePrice - trade.entry_price) / trade.entry_price * 100) : null;
  const nearStopLoss = livePrice != null && trade.stop_loss != null && livePrice <= trade.stop_loss * 1.02;
  const nearTarget   = livePrice != null && trade.target_price != null && livePrice >= trade.target_price * 0.98;
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [slInput, setSlInput] = useState(trade.stop_loss ? trade.stop_loss.toFixed(2) : "");
  const [tpInput, setTpInput] = useState(trade.target_price ? trade.target_price.toFixed(2) : "");

  const editMutation = useMutation({
    mutationFn: () => editPaperTrade(
      trade.id, sessionId,
      slInput ? parseFloat(slInput) : null,
      tpInput ? parseFloat(tpInput) : null,
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setEditing(false);
    },
  });

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
      <td className="px-4 py-3 text-sm font-mono">{currency}{fmt(trade.entry_price)}</td>
      <td className="px-4 py-3">
        {livePrice != null ? (
          <div>
            <span className={clsx(
              "text-sm font-mono font-bold",
              nearStopLoss ? "text-red-400 animate-pulse" :
              nearTarget   ? "text-bull animate-pulse" : "text-white"
            )}>
              {currency}{fmt(livePrice)}
            </span>
            {/* Day change — helps user understand why price differs from entry */}
            {quote?.change_pct != null && (
              <p className={clsx("text-[10px] font-medium", quote.change_pct >= 0 ? "text-bull/70" : "text-bear/70")}>
                {quote.change_pct >= 0 ? "▲" : "▼"} {Math.abs(quote.change_pct).toFixed(2)}% today
              </p>
            )}
            {nearStopLoss && <p className="text-[10px] text-red-400">⚠ Near stop loss</p>}
            {nearTarget   && <p className="text-[10px] text-bull">🎯 Near target</p>}
          </div>
        ) : (
          <span className="text-xs text-gray-600">Loading…</span>
        )}
      </td>
      <td className="px-4 py-3">
        {unrealizedPnl != null ? (
          <div>
            <span className={clsx("text-sm font-mono font-bold", unrealizedPnl >= 0 ? "text-bull" : "text-bear")}>
              {unrealizedPnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(unrealizedPnl))}
            </span>
            <p className={clsx("text-[10px]", unrealizedPct! >= 0 ? "text-bull/70" : "text-bear/70")}>
              {unrealizedPct! >= 0 ? "+" : ""}{unrealizedPct!.toFixed(2)}%
            </p>
          </div>
        ) : (
          <span className="text-xs text-gray-600">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <SignalBadge signal={trade.signal as any} size="sm" />
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
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {new Date(trade.opened_at).toLocaleDateString("en-IN")}
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
    {/* Inline reminder row — always shown so all positions look consistent */}
    <tr className="border-b border-dark-border bg-dark-bg/40">
      <td colSpan={11} className="px-4 py-2">
        <div className="flex flex-wrap gap-3">
          {trade.stop_loss && trade.stop_loss > 0 ? (
            <span className="flex items-center gap-1.5 text-[11px] text-yellow-300/80">
              <ShieldAlert size={11} className="shrink-0" />
              Stop Loss: <strong>{currency}{fmt(trade.stop_loss)}</strong>
              <span className="text-yellow-300/50">
                (−{((trade.entry_price - trade.stop_loss) / trade.entry_price * 100).toFixed(1)}% from entry) · Close manually if price drops here
              </span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-[11px] text-gray-600">
              <ShieldAlert size={11} className="shrink-0" />
              Stop Loss: <span className="italic">not set — click ✎ to add one</span>
            </span>
          )}
          <span className="text-gray-700 text-[11px]">·</span>
          {trade.target_price && trade.target_price > 0 ? (
            <span className="flex items-center gap-1.5 text-[11px] text-green-300/80">
              <Target size={11} className="shrink-0" />
              Target: <strong>{currency}{fmt(trade.target_price)}</strong>
              <span className="text-green-300/50">
                (+{((trade.target_price - trade.entry_price) / trade.entry_price * 100).toFixed(1)}% from entry) · Consider closing when price reaches here
              </span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-[11px] text-gray-600">
              <Target size={11} className="shrink-0" />
              Target: <span className="italic">not set — click ✎ to add one</span>
            </span>
          )}
        </div>
      </td>
    </tr>
    </>
  );
}

function ClosedTradeRow({ trade }: { trade: PaperTrade }) {
  const currency = trade.market === "IN" ? "₹" : "$";
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
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {trade.closed_at ? new Date(trade.closed_at).toLocaleDateString("en-IN") : "—"}
      </td>
    </tr>
  );
}

export default function PaperTradingPage() {
  const sessionId = useSessionId();
  const queryClient = useQueryClient();
  const [sellTarget, setSellTarget] = useState<PaperTrade | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const { data: portfolio, isLoading } = useQuery({
    queryKey: ["paper-portfolio", sessionId],
    queryFn: () => fetchPaperPortfolio(sessionId),
    enabled: sessionId !== "ssr",
    refetchInterval: 30_000,
  });

  const resetMutation = useMutation({
    mutationFn: () => resetPaperPortfolio(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] });
      setShowResetConfirm(false);
    },
  });

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

  const openTrades = portfolio.open_trades;
  const closedTrades = portfolio.closed_trades;

  // Calculate unrealized P&L — we don't have live prices here, but show "–" for now
  // (accurate P&L shown in modal when selling)
  const totalInvested = openTrades.reduce((s, t) => s + t.invested, 0);
  const totalRealized = portfolio.total_realized_pnl;
  const portfolioValue = portfolio.cash + totalInvested;
  const totalReturn = portfolioValue - STARTING_CASH + totalRealized;
  const totalReturnPct = (totalReturn / STARTING_CASH) * 100;

  const winTrades = closedTrades.filter(t => (t.realized_pnl ?? 0) > 0).length;
  const winRate = closedTrades.length > 0 ? (winTrades / closedTrades.length * 100) : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Beaker size={24} className="text-brand-400" />
            Paper Trading
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Practice with ₹10,00,000 virtual money · No real funds involved
          </p>
        </div>
        <button
          onClick={() => setShowResetConfirm(true)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dark-border text-gray-400 hover:text-white hover:border-white/30 transition-colors"
        >
          <RotateCcw size={13} />
          Reset Portfolio
        </button>
      </div>

      {/* Reset confirm */}
      {showResetConfirm && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-yellow-300 text-sm">
            <AlertTriangle size={16} />
            This will erase all trades and reset to ₹10,00,000. Are you sure?
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
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="Virtual Cash"
          value={`₹${fmt(portfolio.cash, 0)}`}
          sub={`of ₹${fmt(STARTING_CASH, 0)} starting`}
        />
        <StatCard
          label="Invested"
          value={`₹${fmt(totalInvested, 0)}`}
          sub={`${openTrades.length} open position${openTrades.length !== 1 ? "s" : ""}`}
        />
        <StatCard
          label="Realized P&L"
          value={`${totalRealized >= 0 ? "+" : ""}₹${fmt(Math.abs(totalRealized), 0)}`}
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
          <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-dark-border text-xs text-gray-500">
                    <th className="px-4 py-2.5 text-left">Stock</th>
                    <th className="px-4 py-2.5 text-left">Qty</th>
                    <th className="px-4 py-2.5 text-left">Entry</th>
                    <th className="px-4 py-2.5 text-left">Mkt Price <span className="text-gray-600 font-normal">(last close when closed)</span></th>
                    <th className="px-4 py-2.5 text-left">Unr. P&L</th>
                    <th className="px-4 py-2.5 text-left">Signal</th>
                    <th className="px-4 py-2.5 text-left">Stop Loss</th>
                    <th className="px-4 py-2.5 text-left">Target</th>
                    <th className="px-4 py-2.5 text-left">Date</th>
                    <th className="px-4 py-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map(t => (
                    <OpenTradeRow key={t.id} trade={t} onSell={setSellTarget} sessionId={sessionId} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Trade History */}
      {closedTrades.length > 0 && (
        <div>
          <h2 className="font-bold text-base mb-3 flex items-center gap-2">
            <BarChart2 size={16} className="text-gray-400" />
            Trade History
          </h2>
          <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden">
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
                  {closedTrades.map(t => (
                    <ClosedTradeRow key={t.id} trade={t} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
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
