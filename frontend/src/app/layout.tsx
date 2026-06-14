import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { SearchBar } from "@/components/SearchBar";
import Link from "next/link";
import { TrendingUp } from "lucide-react";

export const metadata: Metadata = {
  title: "StockSense — AI Stock Predictor",
  description: "AI-powered stock predictions for US & Indian markets",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-dark-bg text-white min-h-screen font-sans antialiased">
        <Providers>
          <nav className="sticky top-0 z-40 border-b border-dark-border bg-dark-bg/80 backdrop-blur-md">
            <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
              <Link href="/" className="flex items-center gap-2 text-brand-500 font-bold text-lg shrink-0">
                <TrendingUp size={22} />
                StockSense
              </Link>
              <SearchBar />
              <div className="flex items-center gap-4 ml-auto text-sm text-gray-400">
                <Link href="/" className="hover:text-white transition-colors">Dashboard</Link>
                <Link href="/screener" className="hover:text-white transition-colors">Screener</Link>
                <Link href="/portfolio" className="hover:text-white transition-colors">Portfolio</Link>
                <Link href="/alerts" className="hover:text-white transition-colors">Alerts</Link>
                <Link href="/watchlist" className="hover:text-white transition-colors">Watchlist</Link>
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
