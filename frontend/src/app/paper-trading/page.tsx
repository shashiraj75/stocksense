"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import clsx from "clsx";
import {
  TrendingUp, TrendingDown, RotateCcw, ExternalLink, Beaker,
  DollarSign, BarChart2, AlertTriangle, CheckCircle2,
} from "lucide-react";
import {
  fetchPaperPortfolio, closePaperTrade, resetPaperPortfolio,
  type PaperTrade,
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

function OpenTradeRow({ trade, onSell }: { trade: PaperTrade; onSell: (t: PaperTrade) => void }) {
  const currency = trade.market === "IN" ? "₹" : "$";
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
      <td className="px-4 py-3 text-sm font-mono text-gray-400">
        {currency}{fmt(trade.invested)}
      </td>
      <td className="px-4 py-3">
        <SignalBadge signal={trade.signal as any} size="sm" />
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {new Date(trade.opened_at).toLocaleDateString("en-IN")}
      </td>
      <td className="px-4 py-3 text-right">
        <button
          onClick={() => onSell(trade)}
          className="px-3 py-1.5 rounded-lg text-xs font-medium bg-bear/10 border border-bear/30 text-red-400 hover:bg-bear/20 transition-colors"
        >
          Close
        </button>
      </td>
    </tr>
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
                    <th className="px-4 py-2.5 text-left">Invested</th>
                    <th className="px-4 py-2.5 text-left">Signal</th>
                    <th className="px-4 py-2.5 text-left">Date</th>
                    <th className="px-4 py-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map(t => (
                    <OpenTradeRow key={t.id} trade={t} onSell={setSellTarget} />
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
          currentPrice={sellTarget.entry_price}
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
