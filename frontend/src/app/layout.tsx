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
            {/* 2026-09-08 user-specified 4-row structure — replaces the
                earlier single-line/flex-wrap header entirely, for every
                viewport width (not just mobile): the row-per-concern shape
                the user asked for already reads cleanly at any width, so
                there's no separate mobile/desktop variant to keep in sync.
                  Row 1: Logo ······················· Date/Time (right)
                  Row 2: Market Status · Market Dropdown · Username
                  Row 3: Live index ticker ribbon
                  Row 4: Stock search bar */}
            <div className="max-w-7xl mx-auto px-3 sm:px-4 py-2.5 flex items-center justify-between gap-3">
              <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-base sm:text-lg shrink-0">
                <TrendingUp size={20} />
                <span>StockSense360</span>
              </Link>
              <LiveClock inline />
            </div>

            <div className="border-t border-dark-border/40 px-3 sm:px-4 py-1.5">
              <div className="max-w-7xl mx-auto flex items-center justify-between gap-3">
                <div className="min-w-0"><SelectedMarketStatusInline /></div>
                <div className="flex items-center gap-2 shrink-0">
                  <GlobalMarketDropdown />
                  <UserMenu />
                  <div className="flex items-center lg:hidden">
                    <MobileNav links={NAV_LINKS} />
                  </div>
                </div>
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

            {/* Row 4: Stock search bar. */}
            <div className="border-t border-dark-border/40 px-3 sm:px-4 py-2">
              <div className="max-w-7xl mx-auto sm:max-w-xs"><SearchBar /></div>
            </div>

            {/* Row 5: Nav links (desktop only). */}
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
