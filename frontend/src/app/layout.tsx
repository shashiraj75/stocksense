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

export const metadata: Metadata = {
  title: { default: "StockSense — AI Stock Predictor", template: "%s | StockSense" },
  description: "Free AI-powered stock predictions for US and Indian markets. Daily picks, heatmap, screener, and portfolio tracker.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export const NAV_LINKS = [
  { href: "/picks",      label: "Daily Picks", accent: true },
  { href: "/",           label: "Dashboard" },
  { href: "/heatmap",    label: "Heatmap" },
  { href: "/screener",   label: "Screener" },
  { href: "/portfolio",  label: "Portfolio" },
  { href: "/alerts",     label: "Alerts" },
  { href: "/watchlist",  label: "Watchlist" },
  { href: "/validation",    label: "Validation" },
  { href: "/paper-trading", label: "Paper Trade" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-dark-bg text-white min-h-screen font-sans antialiased">
        <Providers>
          <nav className="sticky top-0 z-10 border-b border-dark-border bg-dark-bg/90 backdrop-blur-md">
            {/* Row 1: Logo · Search · Hamburger (mobile) / Clock + Market Status + Sign In (desktop) */}
            <div className="max-w-7xl mx-auto px-3 sm:px-4 pt-2.5 pb-2 flex items-center gap-2 sm:gap-4">
              {/* Logo */}
              <Link href="/" className="flex items-center gap-1.5 text-brand-500 font-bold text-base sm:text-lg shrink-0">
                <TrendingUp size={20} />
                <span className="hidden sm:inline">StockSense</span>
              </Link>

              {/* Search — fills remaining space */}
              <div className="flex-1 min-w-0"><SearchBar /></div>

              {/* Desktop: clock + market status */}
              <div className="hidden md:flex items-start gap-4 shrink-0">
                <LiveClock inline />
                <span className="text-dark-border text-xs">|</span>
                <MarketStatusInline />
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

            {/* Row 3: Nav links (desktop only) */}
            <div className="hidden lg:block border-t border-dark-border/60">
              <div className="max-w-7xl mx-auto px-4">
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
