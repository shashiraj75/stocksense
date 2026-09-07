import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { SearchBar } from "@/components/SearchBar";
import Link from "next/link";
import { TrendingUp } from "lucide-react";
import { MobileNav } from "@/components/MobileNav";
import { NavLinks } from "@/components/NavLinks";
import { UserMenu } from "@/components/UserMenu";
import { MarketStatusInline } from "@/components/MarketStatusBar";
import { LiveClock } from "@/components/LiveClock";
import { TickerRibbon } from "@/components/TickerRibbon";
import { NavHeightObserver } from "@/components/NavHeightObserver";
import { GlobalMarketDropdown } from "@/components/GlobalMarketDropdown";
import { isTradePostmortemDailyEnabled } from "@/utils/featureFlags";

export const metadata: Metadata = {
  title: { default: "StockSense360 — AI Stock Predictor", template: "%s | StockSense360" },
  description: "Free AI-powered stock predictions for US and Indian markets. Daily picks, heatmap, screener, and portfolio tracker.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export const NAV_LINKS = [
  { href: "/picks",      label: "Daily Picks", accent: true },
  { href: "/multibagger", label: "Multibagger", color: "text-purple-400 hover:text-purple-300" },
  { href: "/dashboard",  label: "Dashboard" },
  { href: "/heatmap",    label: "Heatmap" },
  { href: "/screener",   label: "Screener" },
  { href: "/portfolio",  label: "Portfolio" },
  { href: "/alerts",     label: "Alerts" },
  { href: "/watchlist",  label: "Watchlist" },
  { href: "/validation",    label: "Validation" },
  { href: "/paper-trading", label: "Paper Trade" },
  // PR #32 pre-merge correction: dormant by default — only shown once
  // NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED is explicitly enabled at
  // build time. See src/utils/featureFlags.ts for the fail-safe parsing.
  ...(isTradePostmortemDailyEnabled() ? [{ href: "/postmortem", label: "Postmortem" }] : []),
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-dark-bg text-white min-h-screen font-sans antialiased">
        <Providers>
          <NavHeightObserver />
          <nav id="site-nav" className="sticky top-0 z-10 border-b border-dark-border bg-dark-bg sm:bg-dark-bg/90 backdrop-blur-none sm:backdrop-blur-md">
            {/* Row 1: Logo · Search · (mobile market dropdown) · Sign In · Hamburger.
                Kept deliberately lean — clock and market-open/closed status
                used to live in this same row (desktop-only) with a SEPARATE
                mobile-only strip below it, which left a real gap between the
                `md` and `lg` breakpoints where NEITHER showed (confirmed:
                nothing rendered at all from ~768–1023px wide). Moved to its
                own always-visible Row 2 below instead of being split across
                two conditionally-hidden blocks. */}
            <div className="max-w-7xl mx-auto px-3 sm:px-4 pt-2.5 pb-2 flex items-center gap-2 sm:gap-4">
              {/* Logo */}
              <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-base sm:text-lg shrink-0">
                <TrendingUp size={20} />
                <span className="hidden sm:inline">StockSense360</span>
              </Link>

              {/* Search — fills remaining space */}
              <div className="flex-1 min-w-0 sm:min-w-[220px] max-w-xs"><SearchBar /></div>

              {/* Global market context — mobile/tablet only here (below
                  lg). Row 4 renders the single desktop (lg+) copy, after
                  the Paper Trade tab — without this `lg:hidden`, both would
                  render simultaneously on desktop. Both instances share the
                  same localStorage-backed hook and stay in sync live via a
                  custom window event, so this is purely a display split,
                  never two independent selections. */}
              <div className="lg:hidden shrink-0">
                <GlobalMarketDropdown />
              </div>

              {/* User menu — always visible */}
              <div className="shrink-0">
                <UserMenu />
              </div>

              {/* Hamburger — mobile/tablet only */}
              <div className="flex items-center lg:hidden shrink-0">
                <MobileNav links={NAV_LINKS} />
              </div>
            </div>

            {/* Row 2: Clock + market open/closed status — ONE component,
                always rendered at every viewport width (no `hidden`/breakpoint
                split), so there is no width range where this information
                disappears entirely. `flex-wrap` here needs the row to be
                width-CONSTRAINED (not `overflow-x-auto`, which gives a flex
                container license to grow past the viewport instead of
                wrapping) — each market pill drops to its own line on a
                narrow phone instead of two half-cut-off pills sitting
                side by side. */}
            <div className="border-t border-dark-border/40 px-3 sm:px-4 py-1.5">
              <div className="max-w-7xl mx-auto flex items-start flex-wrap gap-x-4 gap-y-1.5">
                <LiveClock inline />
                <span className="hidden sm:inline text-dark-border text-xs shrink-0">|</span>
                <MarketStatusInline />
              </div>
            </div>

            {/* Row 3: live index ticker — NIFTY/SENSEX, S&P/NASDAQ/DOW,
                Bitcoin. A continuously-scrolling marquee (TickerRibbon)
                instead of a static horizontally-scrollable row, so every
                index is visible in turn without the user needing to scroll
                to see the ones off-screen. Persistent across every page. */}
            <div className="border-t border-dark-border/40 py-1.5">
              <TickerRibbon />
            </div>

            {/* Row 4: Nav links (desktop only). Global market dropdown sits
                after the Paper Trade tab, separated by a vertical divider so
                it doesn't read as one more nav link. */}
            <div className="hidden lg:block border-t border-dark-border/60">
              <div className="max-w-7xl mx-auto px-4 flex items-center justify-between gap-3">
                <NavLinks links={NAV_LINKS} />
                <div className="flex items-center gap-3 shrink-0 py-1">
                  <span className="text-dark-border text-xs">|</span>
                  <GlobalMarketDropdown />
                </div>
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
