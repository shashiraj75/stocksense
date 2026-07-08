"use client";
import { useState, useEffect, useRef, useCallback, cloneElement } from "react";
import { createPortal } from "react-dom";
import { TrendingUp, BookmarkPlus, Check, LogIn } from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";

interface ContextMenuProps {
  symbol: string;
  market: string;
  // Exactly one element (card link, heatmap button, or table row). The
  // right-click handler is attached to it directly via cloneElement — no
  // wrapper node is rendered around it. The previous wrapper <div> sat
  // between <tbody> and <tr> on the Screener page, invalid HTML that
  // React 19 flags as a hydration-error risk.
  children: React.ReactElement<{ onContextMenu?: React.MouseEventHandler }>;
}

export function StockContextMenu({ symbol, market, children }: ContextMenuProps) {
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

  // Logged-out visitors must never invoke the watchlist mutation — the
  // backend already requires a verified JWT (Depends(require_owner)), so
  // this is a UX clarification, not the actual security boundary.
  const goToSignIn = () => {
    router.push("/login");
    close();
  };

  return (
    <>
      {/* Right-click handler goes straight onto the child element (a <tr>,
          card link, or heatmap button) — no wrapper node, so table markup
          stays valid: <tbody> contains only <tr>. */}
      {cloneElement(children, { onContextMenu: open })}

      {/* Floating context menu — portaled to <body> so it never renders
          inside a <tbody> (or any overflow-clipped ancestor); it only
          mounts after a user interaction, so document is always available
          and SSR/hydration never sees it. Same fixed positioning as before. */}
      {pos && createPortal(
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

          {user ? (
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
          ) : (
            <button
              onClick={goToSignIn}
              className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm hover:bg-dark-border transition-colors"
            >
              <LogIn size={14} className="text-brand-400 shrink-0" />
              <span className="text-white">Sign in to save stocks</span>
            </button>
          )}
        </div>,
        document.body
      )}
    </>
  );
}
