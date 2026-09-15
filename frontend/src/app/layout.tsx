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

// 2026-09-15 reorder (user-requested nav-UX review) — grouped by what a
// user is actually trying to do, instead of the prior growth-order
// sequence (items landed wherever they were when each feature shipped,
// with no grouping logic). Three groups, left to right:
//   1. Discovery/research  — Daily Picks (kept first: the flagship,
//      already visually emphasized via `accent`), Dashboard (a page whose
//      own <h1> is "Market Overview" — a discovery/overview page, not a
//      personal one, confirmed by reading dashboard/page.tsx directly, so
//      it belongs here, not floating in the middle of the personal group
//      it was previously sandwiched into), Multibagger, Screener, Heatmap.
//   2. Personal tracking — Watchlist and Alerts are already a natural
//      pair (things you're monitoring); Portfolio and Paper Trade are
//      also a natural pair (your real vs. simulated holdings) — grouped
//      together now instead of split apart by four unrelated items.
//   3. Trust/evidence — Validation (Model Validation's own <h1>,
//      confirmed by reading validation/page.tsx) is a track-record/proof
//      page, not a daily workflow tool — moved from the middle of the
//      personal-tracking group to the end, its own natural place.
export const NAV_LINKS = [
  { href: "/picks",      label: "Daily Picks", accent: true },
  { href: "/dashboard",  label: "Dashboard" },
  { href: "/multibagger", label: "Multibagger", color: "text-purple-400 hover:text-purple-300" },
  { href: "/screener",   label: "Screener" },
  { href: "/heatmap",    label: "Heatmap" },
  { href: "/watchlist",  label: "Watchlist" },
  { href: "/alerts",     label: "Alerts" },
  { href: "/portfolio",  label: "Portfolio" },
  { href: "/paper-trading", label: "Paper Trade" },
  { href: "/validation",    label: "Validation" },
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

            {/* Row 2 stacks vertically on mobile (flex-col) — on a narrow
                phone, Market Status (which can itself be two lines: "NSE
                India Open" + "Closes at ...") and the Dropdown/Username
                cluster were both fighting for the same single row and
                visually colliding/overlapping (2026-09-08 user report:
                "totally corrupted, all jumbled up"). Two clean rows on
                mobile removes any chance of that; sm+ keeps them side by
                side as before. */}
            <div className="border-t border-dark-border/40 px-3 sm:px-4 py-1.5">
              <div className="max-w-7xl mx-auto flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-3">
                <div className="min-w-0"><SelectedMarketStatusInline /></div>
                {/* Right-aligned on every width (not just sm+) — was
                    left-aligned directly under Market Status on mobile with
                    barely any gap, so the dropdown's open panel visually sat
                    right on top of the status line above it (2026-09-08
                    user report). `ml-auto` here pushes it to the row's own
                    right edge even while stacked in a flex-col. */}
                <div className="flex items-center gap-2 shrink-0 ml-auto">
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
          {/* Copyright notice — the public landing page (app/page.tsx)
              already showed this; the authenticated app (every page behind
              this layout — Daily Picks, Dashboard, Portfolio, etc.) had no
              footer at all, so it never appeared for a logged-in user
              (2026-09-15 user request: "show the copyright signs on the
              page"). Same wording/dynamic year as the landing page, for
              consistency. */}
          <footer className="border-t border-dark-border py-4 text-center text-xs text-gray-500">
            © {new Date().getFullYear()} StockSense360
          </footer>
        </Providers>
      </body>
    </html>
  );
}
