import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { SearchBar } from "@/components/SearchBar";
import Link from "next/link";
import { TrendingUp } from "lucide-react";
import { MobileNav } from "@/components/MobileNav";
import { NavLinks } from "@/components/NavLinks";
import { UserMenu } from "@/components/UserMenu";
import { MarketStatusInline, MobileMarketStrip } from "@/components/MarketStatusBar";
import { LiveClock } from "@/components/LiveClock";
import { IndexBar } from "@/components/IndexBar";
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
            {/* Row 1: Logo · Search · Hamburger (mobile) / Clock + Market Status + Sign In (desktop).
                flex-wrap (instead of squeezing the status block into a narrow
                horizontal-scroll strip) lets it drop to its own full-width
                line when it doesn't fit alongside Logo+Search — same clean
                two-line layout (label row + "Opens/Closes at..." sub-row)
                every market pill already uses, just without needing to
                scroll to see all of it. */}
            <div className="max-w-7xl mx-auto px-3 sm:px-4 pt-2.5 pb-2 flex items-center flex-wrap gap-2 sm:gap-4">
              {/* Logo */}
              <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-base sm:text-lg shrink-0">
                <TrendingUp size={20} />
                <span className="hidden sm:inline">StockSense360</span>
              </Link>

              {/* Search — fills remaining space */}
              <div className="flex-1 min-w-0"><SearchBar /></div>

              {/* Desktop: clock + market status — wraps to its own line, full width, if it doesn't fit on row 1 */}
              <div className="hidden lg:flex items-start flex-wrap gap-4 shrink-0">
                <LiveClock inline />
                <span className="text-dark-border text-xs shrink-0">|</span>
                <MarketStatusInline />
              </div>

              {/* Global market context — mobile/tablet only here (below
                  lg). Row 3 renders the single desktop (lg+) copy, after
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

            {/* Row 2 (mobile): compact market status strip */}
            <div className="md:hidden border-t border-dark-border/40 px-3 py-1.5 overflow-x-auto scrollbar-hide">
              <MobileMarketStrip />
            </div>

            {/* Index strip — NIFTY/SENSEX, S&P/NASDAQ/DOW, Bitcoin. Persistent
                across every page instead of being duplicated per-page, same
                way MarketStatusInline shows all markets' hours regardless of
                which one a given page has selected. */}
            <div className="border-t border-dark-border/40 px-3 sm:px-4 py-1.5 overflow-x-auto scrollbar-hide">
              {/* No width utility here — IndexBar's own `inline` wrapper is
                  already w-max, and nesting two width-constrained flex
                  containers breaks width calculation in some browsers,
                  causing items to overlap instead of sitting side by side.
                  Flex items size to their content by default, so this row
                  doesn't need one of its own. */}
              <div className="max-w-7xl mx-auto flex items-center gap-4">
                <div className="shrink-0"><IndexBar market="IN" inline /></div>
                <span className="text-dark-border text-xs shrink-0">|</span>
                <div className="shrink-0"><IndexBar market="US" inline /></div>
                <span className="text-dark-border text-xs shrink-0">|</span>
                <div className="shrink-0"><IndexBar market="CRYPTO" inline /></div>
              </div>
            </div>

            {/* Row 3: Nav links (desktop only). Global market dropdown sits
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
