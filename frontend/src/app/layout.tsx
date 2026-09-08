import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { SearchBar } from "@/components/SearchBar";
import Link from "next/link";
import { TrendingUp } from "lucide-react";
import { MobileNav } from "@/components/MobileNav";
import { NavLinks } from "@/components/NavLinks";
import { UserMenu } from "@/components/UserMenu";
import { SelectedMarketStatusInline } from "@/components/MarketStatusBar";
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
            {/* Mobile header (< sm) — deliberately a separate, explicitly
                stacked layout rather than the same flex-wrap row reflowing
                itself, which produced an uneven, "accidental" looking stack
                (2026-09-08 user report: alignment on mobile "doesn't look
                good at all"). Four clean rows: Logo+Hamburger, Search,
                Clock+Status, Market/Username — each own row is internally
                aligned rather than letting pieces wrap independently. */}
            <div className="sm:hidden">
              <div className="px-3 pt-2.5 flex items-center justify-between gap-3">
                <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-base shrink-0">
                  <TrendingUp size={20} />
                  <span>StockSense360</span>
                </Link>
                <MobileNav links={NAV_LINKS} />
              </div>
              <div className="px-3 pt-2"><SearchBar /></div>
              <div className="px-3 pt-2 flex items-center gap-3 min-w-0">
                <LiveClock inline />
                <span className="text-dark-border text-xs shrink-0">|</span>
                <div className="min-w-0">
                  <SelectedMarketStatusInline />
                </div>
              </div>
              <div className="px-3 pt-2 pb-2.5 flex items-center justify-end gap-2">
                <GlobalMarketDropdown />
                <UserMenu />
              </div>
            </div>

            {/* Desktop/tablet header (sm+) — one line: Logo · Search ·
                Clock · Market Status (selected market only) · Market
                Dropdown · Username. Clock+status is the flex-wrap-able
                middle group so it drops onto its own line first on a
                mid-width viewport, while the dropdown/username cluster
                stays pinned right via a single `ml-auto` (not also a
                growing search field — combining both left a dead gap). */}
            <div className="hidden sm:flex max-w-7xl mx-auto px-4 py-2.5 items-center flex-wrap gap-x-4 gap-y-2">
              {/* Logo */}
              <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-lg shrink-0">
                <TrendingUp size={20} />
                <span>StockSense360</span>
              </Link>

              {/* Search — fixed comfortable width, not flex-growing, so it
                  doesn't fight the right-hand cluster's own ml-auto for
                  the row's free space. */}
              <div className="min-w-0 w-64 shrink-0"><SearchBar /></div>

              {/* Everything else — Clock, selected-market status, market
                  dropdown, and username — as one right-aligned cluster,
                  in that left-to-right order. Wraps as a whole onto its
                  own line on a mid-width viewport rather than each piece
                  wrapping independently and drifting out of alignment. */}
              <div className="flex items-center flex-wrap gap-x-4 gap-y-2 ml-auto">
                <div className="flex items-center flex-wrap gap-x-3 gap-y-1 shrink-0">
                  <LiveClock inline />
                  <span className="text-dark-border text-xs shrink-0">|</span>
                  <SelectedMarketStatusInline />
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <GlobalMarketDropdown />
                  <UserMenu />
                  <div className="flex items-center lg:hidden">
                    <MobileNav links={NAV_LINKS} />
                  </div>
                </div>
              </div>
            </div>

            {/* Row 2: live index ticker — NIFTY/SENSEX, S&P/NASDAQ/DOW,
                Bitcoin. A continuously-scrolling marquee (TickerRibbon)
                instead of a static horizontally-scrollable row, so every
                index is visible in turn without the user needing to scroll
                to see the ones off-screen. Persistent across every page. */}
            <div className="border-t border-dark-border/40 py-1.5">
              <TickerRibbon />
            </div>

            {/* Row 3: Nav links (desktop only). The market dropdown now
                lives once, in Row 1, alongside Username — no longer
                duplicated here. */}
            <div className="hidden lg:block border-t border-dark-border/60">
              <div className="max-w-7xl mx-auto px-4 flex items-center gap-3">
                <NavLinks links={NAV_LINKS} />
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
