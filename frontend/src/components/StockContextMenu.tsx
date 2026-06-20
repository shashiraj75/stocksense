"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { TrendingUp, BookmarkPlus, Check } from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";

interface ContextMenuProps {
  symbol: string;
  market: string;
  children: React.ReactNode;
  className?: string;
}

export function StockContextMenu({ symbol, market, children, className }: ContextMenuProps) {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const [pos, setPos]         = useState<{ x: number; y: number } | null>(null);
  const [added, setAdded]     = useState(false);
  const [adding, setAdding]   = useState(false);
  const menuRef               = useRef<HTMLDivElement>(null);
  const router                = useRouter();

  const open = (e: React.MouseEvent) => {
    e.preventDefault();
    // Clamp to viewport so menu never overflows
    const x = Math.min(e.clientX, window.innerWidth  - 200);
    const y = Math.min(e.clientY, window.innerHeight - 110);
    setPos({ x, y });
    setAdded(false);
  };

  const close = useCallback(() => setPos(null), []);

  // Close on click-outside or Escape
  useEffect(() => {
    if (!pos) return;
    const onKey   = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) close();
    };
    window.addEventListener("keydown",  onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown",  onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [pos, close]);

  const goToStock = () => {
    router.push(`/stock/${encodeURIComponent(symbol)}?market=${market}`);
    close();
  };

  const addToWatchlist = async () => {
    if (adding || added) return;
    setAdding(true);
    try {
      await api.post(`/api/watchlist/${userId}`, { symbol, market, notes: "" });
      setAdded(true);
      setTimeout(close, 900);
    } catch {
      // duplicate — treat as success
      setAdded(true);
      setTimeout(close, 900);
    } finally {
      setAdding(false);
    }
  };

  return (
    <>
      {/* Wrapper — passes right-click down, keeps long-press friendly */}
      <div className={className} onContextMenu={open}>
        {children}
      </div>

      {/* Floating context menu — rendered in place (not portal) but fixed-positioned */}
      {pos && (
        <div
          ref={menuRef}
          style={{ position: "fixed", top: pos.y, left: pos.x, zIndex: 9999 }}
          className="w-48 bg-dark-card border border-dark-border rounded-xl shadow-2xl overflow-hidden py-1 animate-in fade-in zoom-in-95 duration-100"
        >
          {/* Header chip */}
          <div className="px-3 py-1.5 border-b border-dark-border">
            <span className="text-[10px] text-gray-500 font-medium uppercase tracking-wide">
              {symbol} · {market === "IN" ? "🇮🇳 NSE" : market === "CRYPTO" ? "₿ Crypto" : "🇺🇸 US"}
            </span>
          </div>

          <button
            onClick={goToStock}
            className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-white hover:bg-dark-border transition-colors"
          >
            <TrendingUp size={14} className="text-brand-400 shrink-0" />
            View Analysis
          </button>

          <button
            onClick={addToWatchlist}
            disabled={adding}
            className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-dark-border transition-colors disabled:opacity-60"
          >
            {added ? (
              <>
                <Check size={14} className="text-green-400 shrink-0" />
                <span className="text-green-400">Added to Watchlist</span>
              </>
            ) : (
              <>
                <BookmarkPlus size={14} className="text-brand-400 shrink-0" />
                <span className="text-white">{adding ? "Adding…" : "Add to Watchlist"}</span>
              </>
            )}
          </button>
        </div>
      )}
    </>
  );
}
