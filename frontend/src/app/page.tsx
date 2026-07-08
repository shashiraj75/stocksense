"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  TrendingUp, TrendingDown, BarChart2, Bell, ShieldCheck,
  Brain, LineChart, Zap, Star, ArrowRight, Lock, Globe,
  BookOpen, RefreshCw, Target, LayoutDashboard, ArrowUpRight, ArrowDownRight,
} from "lucide-react";
import { useAuth } from "@/lib/AuthContext";
import { fetchTopMovers, type Market } from "@/utils/api";
import { getMarketStatus } from "@/utils/marketHours";

const SNAPSHOT_MARKETS: { key: Market; label: string; currency: string; locale: string }[] = [
  { key: "IN", label: "NSE India",       currency: "₹", locale: "en-IN" },
  { key: "US", label: "NYSE / NASDAQ",   currency: "$", locale: "en-US" },
];

function fmtPrice(price: number, currency: string, locale: string): string {
  return `${currency}${price.toLocaleString(locale, { maximumFractionDigits: 2 })}`;
}

// A non-positive last price (e.g. a halted/delisted symbol reporting $0) is
// not meaningful data — showing "$0" alongside a computed "+100%" would be
// exactly the misleading state this release is meant to avoid, so these
// entries are hidden rather than displayed.
function isDisplayableMover(m: { price: number } | null | undefined): m is { symbol: string; price: number; change_pct: number; name?: string } {
  return !!m && Number.isFinite(m.price) && m.price > 0;
}

// One request per market (not per card) — reuses the same server-cached
// /api/screener/top-movers endpoint the Screener page already relies on,
// so the landing page never triggers its own scrape or per-symbol quotes.
function useMarketSnapshot(market: Market) {
  return useQuery({
    queryKey: ["landing-market-snapshot", market],
    queryFn: () => fetchTopMovers(market),
    staleTime: 60_000,
    refetchInterval: 300_000, // 5 min — calm, backend's own cache absorbs the real refresh cost
    retry: 1,
  });
}

function SnapshotCard({
  market, label, currency, locale,
}: { market: Market; label: string; currency: string; locale: string }) {
  const { data, isLoading, isError } = useMarketSnapshot(market);
  const marketStatus = getMarketStatus(market);
  // Two distinct symbols from the covered-liquid-universe movers feed —
  // not necessarily gainers/losers, just the first two available so the
  // Market Movers section below isn't a duplicate of these cards.
  const picks = (data?.movers ?? []).filter(isDisplayableMover).slice(0, 2);

  return (
    <div className="rounded-xl bg-dark-card border border-dark-border p-2 min-w-[220px] flex-1 max-w-xs shadow-sm shadow-black/20 transition-all duration-150 hover:border-brand-500/25 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{label}</span>
        <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
          marketStatus.isOpen ? "bg-bull/10 border-bull/30 text-bull" : "bg-white/5 border-dark-border text-gray-400"
        }`}>
          {marketStatus.isOpen ? "Live price" : "Last close"}
        </span>
      </div>

      {isLoading ? (
        <div className="space-y-1">
          <div className="h-6 rounded-lg bg-white/[0.04] animate-pulse" />
          <div className="h-6 rounded-lg bg-white/[0.04] animate-pulse" />
        </div>
      ) : isError || picks.length === 0 ? (
        <p className="text-xs text-gray-500 py-2 text-center">Market data temporarily unavailable</p>
      ) : (
        <div className="space-y-1">
          {picks.map((m) => (
            <div key={m.symbol} className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.03] px-2.5 py-1">
              <span className="font-mono font-bold text-white text-sm">{m.symbol}</span>
              <div className="text-right">
                <p className="font-mono text-sm text-white tabular-nums">{fmtPrice(m.price, currency, locale)}</p>
                <p className={`text-[11px] font-medium flex items-center justify-end gap-0.5 ${
                  m.change_pct >= 0 ? "text-bull" : "text-bear"
                }`}>
                  {m.change_pct >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                  {m.change_pct >= 0 ? "+" : ""}{m.change_pct.toFixed(2)}%
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[10px] text-gray-600 mt-1 text-center">
        {marketStatus.isOpen ? "Updating periodically" : "As of last market close"}
      </p>
    </div>
  );
}

type Mover = { symbol: string; price: number; change_pct: number };

function MoverList({
  title, movers, currency, locale,
}: { title: string; movers: Mover[]; currency: string; locale: string }) {
  if (movers.length === 0) return null; // hide rather than show an empty/misleading column
  return (
    <div className="rounded-lg bg-dark-card border border-dark-border px-3 py-3 shadow-sm shadow-black/20 transition-all duration-150 hover:border-brand-500/25 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30">
      <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-2">{title}</p>
      <div className="space-y-1.5">
        {movers.map(m => {
          const positive = m.change_pct >= 0;
          return (
            <div key={m.symbol} className="flex items-center justify-between gap-2">
              <span className="font-mono font-bold text-white text-sm">{m.symbol}</span>
              <div className="text-right">
                <p className="font-mono text-sm text-white tabular-nums">{fmtPrice(m.price, currency, locale)}</p>
                <p className={`text-[11px] font-semibold flex items-center justify-end gap-0.5 ${positive ? "text-bull" : "text-bear"}`}>
                  {positive ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                  {positive ? "+" : ""}{m.change_pct.toFixed(2)}%
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MarketMovers() {
  const inSnap = useMarketSnapshot("IN");
  const usSnap = useMarketSnapshot("US");
  // Up to 3 gainers + 3 losers per market from the SAME two already-fetched
  // responses the snapshot cards use (shared query key — no extra requests).
  // Fewer than 3 valid entries degrades to fewer rows; an empty column, an
  // empty market, or a fully empty feed hides itself rather than fabricating
  // placeholders. ₹ and $ stay in their own market groups, never mixed.
  const groups = [
    {
      label: "🇮🇳 India · NSE", currency: "₹", locale: "en-IN",
      gainers: (inSnap.data?.gainers ?? []).filter(isDisplayableMover).slice(0, 3),
      losers:  (inSnap.data?.losers  ?? []).filter(isDisplayableMover).slice(0, 3),
    },
    {
      label: "🇺🇸 US · NYSE/NASDAQ", currency: "$", locale: "en-US",
      gainers: (usSnap.data?.gainers ?? []).filter(isDisplayableMover).slice(0, 3),
      losers:  (usSnap.data?.losers  ?? []).filter(isDisplayableMover).slice(0, 3),
    },
  ].filter(g => g.gainers.length > 0 || g.losers.length > 0);

  if (groups.length === 0) return null;

  return (
    <section className="px-4 py-6 border-t border-dark-border">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-3">
          <h2 className="text-xl font-bold text-white">Market Movers</h2>
          <p className="text-gray-500 text-xs mt-1">Top movers in the StockSense360 covered liquid universe</p>
          <p className="text-gray-600 text-[11px] mt-0.5">Price movement only — not an AI recommendation or signal</p>
        </div>
        <div className="space-y-4">
          {groups.map(g => (
            <div key={g.label}>
              <p className="text-xs font-semibold text-gray-400 mb-2">{g.label}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <MoverList title="Top Gainers" movers={g.gainers} currency={g.currency} locale={g.locale} />
                <MoverList title="Top Losers"  movers={g.losers}  currency={g.currency} locale={g.locale} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const FEATURES = [
  {
    icon: Brain,
    title: "AI-Powered Signals",
    desc: "BUY / HOLD / SELL signals with confidence scores, target prices, and stop-losses — for every NSE and US stock.",
    color: "text-purple-400",
    bg: "bg-purple-500/10",
    border: "border-purple-500/20",
  },
  {
    icon: Zap,
    title: "Daily Picks",
    desc: "Every day, the AI ranks the full NSE and US universe and surfaces the top 6 BUY ideas per time horizon, for both markets.",
    color: "text-yellow-400",
    bg: "bg-yellow-500/10",
    border: "border-yellow-500/20",
  },
  {
    icon: BarChart2,
    title: "Full Explainability",
    desc: "Not a black box. See exactly why a signal was generated — factor breakdown, bull/bear thesis, and sentiment drivers.",
    color: "text-brand-400",
    bg: "bg-brand-500/10",
    border: "border-brand-500/20",
  },
  {
    icon: RefreshCw,
    title: "Self-Learning Engine",
    desc: "Tracks every prediction outcome. Factor weights auto-update weekly via Information Coefficient — the model gets smarter over time.",
    color: "text-teal-400",
    bg: "bg-teal-500/10",
    border: "border-teal-500/20",
  },
  {
    icon: Target,
    title: "Paper Trading",
    desc: "Test signals with virtual money, track P&L in real time, set stop-losses and target prices — zero real capital at risk.",
    color: "text-green-400",
    bg: "bg-green-500/10",
    border: "border-green-500/20",
  },
  {
    icon: Bell,
    title: "Price Alerts",
    desc: "Set target price alerts on any stock. Get notified the moment a price crosses your threshold.",
    color: "text-orange-400",
    bg: "bg-orange-500/10",
    border: "border-orange-500/20",
  },
  {
    icon: LineChart,
    title: "Screener & Heatmap",
    desc: "Filter the NSE and S&P 500 universe by PE, ROE, signal type, and sector. Colour-coded heatmap for instant market overview.",
    color: "text-cyan-400",
    bg: "bg-cyan-500/10",
    border: "border-cyan-500/20",
  },
  {
    icon: BookOpen,
    title: "Walk-Forward Validation",
    desc: "Live backtesting results — real hit rates, Sharpe ratios, and alpha vs Nifty benchmark. No cherry-picked back-tests.",
    color: "text-rose-400",
    bg: "bg-rose-500/10",
    border: "border-rose-500/20",
  },
];

const HOW_IT_WORKS = [
  {
    step: "01",
    title: "Universe Screening",
    desc: "The AI scans the entire NSE universe (1,800+ stocks, ₹100 Cr+ market cap) every morning, scoring each stock across 6 fundamental dimensions.",
  },
  {
    step: "02",
    title: "Multi-Factor Scoring",
    desc: "Technical indicators, fundamental quality, sentiment analysis, macro regime, and institutional flows are blended into a single 0–100 composite score.",
  },
  {
    step: "03",
    title: "Signal & Trade Levels",
    desc: "Score ≥ 60 → BUY with target price and stop-loss. Every signal comes with a confidence score and full factor breakdown.",
  },
];

const HORIZONS = [
  { label: "Short Term", period: "1–10 days", desc: "Technicals, momentum, volume, news sentiment", color: "text-green-400", border: "border-green-500/40" },
  { label: "Medium Term", period: "1–3 months", desc: "Earnings, sector rotation, macro trends", color: "text-yellow-400", border: "border-yellow-500/40" },
  { label: "Long Term", period: "3–6 months", desc: "Fundamentals, management quality, growth", color: "text-purple-400", border: "border-purple-500/40" },
];

export default function LandingPage() {
  const { user } = useAuth();
  const isLoggedIn = !!user;

  return (
    <div className="-mx-3 sm:-mx-4 -my-4 sm:-my-6">

      {/* ── Hero ── */}
      <section className="relative overflow-hidden px-4 pt-10 pb-12 text-center">
        {/* Background glow */}
        <div className="pointer-events-none absolute inset-0 flex items-start justify-center">
          <div className="h-72 w-72 rounded-full bg-brand-500/10 blur-3xl mt-8" />
        </div>

        <div className="relative max-w-3xl mx-auto space-y-3">
          <div className="inline-flex items-center gap-2 rounded-full border border-brand-500/30 bg-brand-500/10 px-4 py-1 text-xs font-medium text-brand-400">
            <Lock size={12} /> Institutional-grade AI · Invite-only beta
          </div>

          <h1 className="text-4xl sm:text-5xl font-bold text-white leading-[1.1] tracking-tight">
            AI Stock Intelligence<br />
            <span className="text-brand-500">for Indian & US Markets</span>
          </h1>

          <p className="text-lg text-gray-400 max-w-xl mx-auto leading-relaxed">
            Data-driven BUY / HOLD / SELL signals, daily picks, and full explainability — built on a self-learning model, completely free.
          </p>

          {/* Featured Market Snapshot — dynamic, cached, not a recommendation */}
          <div className="pt-1">
            <div className="flex flex-wrap justify-center gap-2">
              {SNAPSHOT_MARKETS.map(({ key, label, currency, locale }) => (
                <SnapshotCard key={key} market={key} label={label} currency={currency} locale={locale} />
              ))}
            </div>
            <p className="text-[11px] text-gray-600 mt-1.5">
              Sample of covered stocks — not investment recommendations
            </p>
          </div>

          <div className="flex flex-col sm:flex-row gap-3 justify-center pt-1">
            <Link
              href={isLoggedIn ? "/dashboard" : "/login"}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-500 hover:bg-brand-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm shadow-brand-500/20 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md hover:shadow-brand-500/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-dark-bg"
            >
              {isLoggedIn ? <><LayoutDashboard size={16} /> Go to Dashboard</> : <>Sign In <ArrowRight size={16} /></>}
            </Link>
            <a
              href="#features"
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-dark-border bg-dark-card hover:border-brand-500/40 hover:bg-white/[0.04] px-6 py-2.5 text-sm font-semibold text-gray-300 transition-all duration-150 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-dark-bg"
            >
              See Features
            </a>
          </div>
        </div>
      </section>

      <MarketMovers />

      {/* ── How it works ── */}
      <section className="px-4 py-16 border-t border-dark-border">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-10">
            <h2 className="text-2xl font-bold text-white">How it works</h2>
            <p className="text-gray-400 text-sm mt-2">From raw market data to an actionable signal in 3 steps</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {HOW_IT_WORKS.map(({ step, title, desc }) => (
              <div key={step} className="relative bg-dark-card border border-dark-border rounded-2xl p-6 space-y-3 shadow-sm shadow-black/20 transition-all duration-150 hover:border-brand-500/25 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30">
                <span className="text-4xl font-black text-dark-border select-none">{step}</span>
                <h3 className="text-base font-semibold text-white">{title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Horizons ── */}
      <section className="px-4 py-10 bg-dark-card/40 border-y border-dark-border">
        <div className="max-w-4xl mx-auto">
          <p className="text-center text-xs text-gray-500 uppercase tracking-widest mb-6">Three prediction horizons</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {HORIZONS.map(({ label, period, desc, color, border }) => (
              <div key={label} className={`bg-dark-card border ${border} rounded-xl p-5 space-y-2 shadow-sm shadow-black/20 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30`}>
                <div className="flex items-baseline gap-2">
                  <p className={`text-sm font-bold ${color}`}>{label}</p>
                  <p className="text-xs text-gray-500">{period}</p>
                </div>
                <p className="text-xs text-gray-400">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="px-4 py-16 border-b border-dark-border">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-10">
            <h2 className="text-2xl font-bold text-white">Everything you need</h2>
            <p className="text-gray-400 text-sm mt-2">A complete AI research platform — not just a screener</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {FEATURES.map(({ icon: Icon, title, desc, color, bg, border }) => (
              <div key={title} className={`bg-dark-card border ${border} rounded-2xl p-5 space-y-3 shadow-sm shadow-black/20 transition-all duration-150 hover:border-opacity-50 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30`}>
                <div className={`w-9 h-9 rounded-xl ${bg} flex items-center justify-center`}>
                  <Icon size={18} className={color} />
                </div>
                <h3 className="text-sm font-semibold text-white">{title}</h3>
                <p className="text-xs text-gray-400 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Data sources ── */}
      <section className="px-4 py-12 border-b border-dark-border">
        <div className="max-w-3xl mx-auto text-center space-y-6">
          <p className="text-xs text-gray-500 uppercase tracking-widest">Powered by</p>
          <div className="flex flex-wrap justify-center gap-4 text-sm text-gray-400">
            {["Yahoo Finance", "screener.in", "NSE India", "VADER NLP", "XGBoost / Ridge", "Ledoit-Wolf Optimizer"].map(s => (
              <span key={s} className="flex items-center gap-1.5 bg-dark-card border border-dark-border rounded-lg px-4 py-2">
                <Globe size={12} className="text-brand-500" /> {s}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ── Disclaimer strip ── */}
      <section className="px-4 py-8 bg-dark-card/40 border-b border-dark-border">
        <div className="max-w-3xl mx-auto text-center space-y-3">
          <div className="flex justify-center">
            <ShieldCheck size={20} className="text-amber-400" />
          </div>
          <p className="text-xs text-gray-500 leading-relaxed">
            <strong className="text-gray-400">Important disclaimer:</strong> StockSense360 is an AI research tool for informational purposes only. It is not a SEBI-registered investment adviser and does not provide financial advice. All signals and picks are generated by machine learning models and may be inaccurate. Investing involves risk of loss. Always conduct your own research and consult a qualified financial adviser before making investment decisions.
          </p>
          <Link href="/accept-terms" className="text-xs text-brand-500 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 rounded">
            Read full Terms of Use & Legal Disclaimer →
          </Link>
        </div>
      </section>

      {/* ── CTA footer ── */}
      <section className="px-4 py-16 text-center">
        <div className="max-w-xl mx-auto space-y-5">
          <div className="flex items-center justify-center gap-2 text-brand-500">
            <TrendingUp size={24} />
            <span className="text-xl font-bold text-white">StockSense360</span>
          </div>
          <h2 className="text-2xl font-bold text-white">Ready to trade smarter?</h2>
          <p className="text-gray-400 text-sm">
            {isLoggedIn ? `Welcome back, ${user?.email}` : "Access is by invitation only during beta. Sign in if you have been invited."}
          </p>
          <Link
            href={isLoggedIn ? "/dashboard" : "/login"}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-500 hover:bg-brand-600 px-8 py-3 text-sm font-semibold text-white shadow-sm shadow-brand-500/20 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md hover:shadow-brand-500/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-2 focus-visible:ring-offset-dark-bg"
          >
            {isLoggedIn ? <><LayoutDashboard size={16} /> Go to Dashboard</> : <>Sign In <ArrowRight size={16} /></>}
          </Link>
          <p className="text-xs text-gray-600 pt-2">
            © {new Date().getFullYear()} StockSense360 · <Link href="/accept-terms" className="hover:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 rounded">Terms & Disclaimer</Link>
          </p>
        </div>
      </section>

    </div>
  );
}
