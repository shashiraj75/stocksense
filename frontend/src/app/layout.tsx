import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { SearchBar } from "@/components/SearchBar";
import Link from "next/link";
import { TrendingUp } from "lucide-react";
import { MobileNav } from "@/components/MobileNav";
import { NavLinks } from "@/components/NavLinks";
import { UserMenu } from "@/components/UserMenu";

export const metadata: Metadata = {
  title: { default: "StockSense — AI Stock Predictor", template: "%s | StockSense" },
  description: "Free AI-powered stock predictions for US and Indian markets. Daily picks, heatmap, screener, and portfolio tracker.",
  icons: { icon: "/favicon.svg" },
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
            {/* Row 1: Logo · Search · Sign In */}
            <div className="max-w-7xl mx-auto px-4 pt-3 pb-2 flex items-center gap-4">
              <Link href="/" className="flex items-center gap-2 text-brand-500 font-bold text-lg shrink-0">
                <TrendingUp size={22} />
                StockSense
              </Link>
              <div className="flex-1 max-w-sm"><SearchBar /></div>
              <div className="ml-auto flex items-center gap-2">
                <UserMenu />
                <div className="lg:hidden">
                  <MobileNav links={NAV_LINKS} />
                </div>
              </div>
            </div>
            {/* Row 2: Nav links (desktop only) */}
            <div className="hidden lg:block border-t border-dark-border/60">
              <div className="max-w-7xl mx-auto px-4">
                <NavLinks links={NAV_LINKS} />
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
