"use client";
import { AlertCircle } from "lucide-react";
import { useGlobalMarketContext, GLOBAL_MARKET_CONTEXTS, type GlobalMarketContext } from "@/hooks/useGlobalMarketContext";

/**
 * Renders a graceful "coming soon" banner when the global market context
 * (set via the header's GlobalMarketDropdown) is one this page doesn't
 * support yet — e.g. Crypto/Commodities on a page that's currently IN/US
 * only. Renders nothing when the global context is supported.
 *
 * The page's own content underneath keeps working normally in this case —
 * its own useMarketPreference() call already safely ignores an unsupported
 * stored value and falls back to its own default (see that hook's own
 * `allowed` scoping) — this banner only makes that fallback visible to the
 * user instead of silently showing IN data while the header says "Crypto".
 */
export function UnsupportedMarketNotice({ supported }: { supported: readonly GlobalMarketContext[] }) {
  const [context] = useGlobalMarketContext();
  if (supported.includes(context)) return null;

  const { emoji, label } = GLOBAL_MARKET_CONTEXTS.find(c => c.key === context) ?? GLOBAL_MARKET_CONTEXTS[0];

  return (
    <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-3 flex items-center gap-2.5 text-sm text-yellow-300">
      <AlertCircle size={16} className="shrink-0" />
      <span>
        {emoji} {label} isn&apos;t supported on this page yet — showing your last supported market instead.
      </span>
    </div>
  );
}
