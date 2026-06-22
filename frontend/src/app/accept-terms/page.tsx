"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { acceptTerms } from "@/utils/api";
import { TrendingUp, CheckCircle } from "lucide-react";

const TERMS_VERSION = "v1.0";

const COUNTRIES = [
  "India", "United States", "United Kingdom", "United Arab Emirates", "Singapore",
  "Australia", "Canada", "Germany", "France", "Japan", "China", "Other",
];

const DISCLAIMER_SECTIONS = [
  {
    title: "1. Not financial advice",
    body: "StockSense360 is an AI-powered information and research tool. All content, signals, scores, picks, alerts, and analysis — including BUY, HOLD, and SELL signals — are provided for informational and educational purposes only. Nothing on this platform constitutes financial advice or investment advice regulated under the SEBI Act, 1992 or any other applicable law. StockSense360 is not a SEBI-registered investment adviser, research analyst, or portfolio manager.",
  },
  {
    title: "2. Investment risk disclosure",
    body: "Investing in stocks and securities involves substantial risk of loss, including the possible loss of your entire investment. Past performance of any signal or pick is not indicative of future results. Stock markets are inherently unpredictable. AI models and algorithmic signals can and do produce incorrect predictions. Historical backtesting results shown do not guarantee future returns.",
  },
  {
    title: "3. User responsibility & independent judgement",
    body: "Any investment or trading decision you make is entirely your own responsibility. You must conduct your own independent research and consult a qualified SEBI-registered investment adviser before making financial decisions. You must not rely solely on StockSense360 signals, picks, or scores.",
  },
  {
    title: "4. Data accuracy & reliability",
    body: "StockSense360 sources data from third-party providers including Yahoo Finance, screener.in, NSE India, and public news feeds. We make no warranty that any data, price, or market information is accurate, complete, or up-to-date. Data may be delayed or unavailable. You must not treat any data shown as real-time or authoritative for the purpose of executing trades.",
  },
  {
    title: "5. AI & algorithmic limitations",
    body: "Signals are produced by machine learning models with inherent limitations. Models are trained on historical data and may not adapt quickly to sudden market shifts. The AI may assign a BUY signal to a stock that subsequently declines. No AI model can predict the future performance of any stock with certainty.",
  },
  {
    title: "6. Paper trading & simulation",
    body: "The paper trading feature uses simulated virtual currency only. It does not involve real money or real brokerage accounts. Simulated results may not reflect real market results due to slippage, liquidity, and execution timing. Performance in paper trading is not indicative of real trading performance.",
  },
  {
    title: "7. No liability",
    body: "To the maximum extent permitted by applicable law, StockSense360 and its creators shall not be liable for any financial loss, trading loss, or loss of profits arising from reliance on any signal, pick, score, or analysis on this platform, including losses arising from data inaccuracies, system downtime, or errors in the AI engine.",
  },
  {
    title: "8. Regulatory compliance",
    body: "StockSense360 does not guarantee that using this platform will comply with any applicable securities laws. You are solely responsible for ensuring your investment activities comply with all laws applicable to you, including SEBI regulations, FEMA, and income tax obligations on capital gains.",
  },
  {
    title: "9. Governing law",
    body: "These Terms are governed by the laws of India. Any disputes shall be subject to the exclusive jurisdiction of the courts of Mumbai, India.",
  },
  {
    title: "10. Amendments",
    body: "StockSense360 reserves the right to update these Terms at any time. If material changes are made, you will be required to re-accept the updated Terms before continuing to use the platform.",
  },
];

const inputCls = "w-full bg-dark-bg border border-dark-border rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-gray-500 outline-none focus:border-brand-500 transition-colors";

export default function AcceptTermsPage() {
  const [user, setUser]           = useState<any>(null);
  const [agreed, setAgreed]       = useState(false);
  const [loading, setLoading]     = useState(false);
  const [checking, setChecking]   = useState(true);
  const [error, setError]         = useState<string | null>(null);

  // Profile fields
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName]   = useState("");
  const [mobile, setMobile]       = useState("");
  const [country, setCountry]     = useState("India");

  const router = useRouter();

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      if (!data.user) { router.replace("/login"); return; }
      const cookieName = `ss_terms_${data.user.id}`;
      const cookieAccepted = document.cookie.includes(`${cookieName}=v1.0`);
      if (cookieAccepted || data.user.user_metadata?.terms_accepted === true) {
        if (!cookieAccepted) {
          document.cookie = `${cookieName}=v1.0; path=/; max-age=31536000; SameSite=Lax`;
        }
        router.replace("/dashboard");
        return;
      }
      setUser(data.user);
      setChecking(false);
    });
  }, [router]);

  const handleAccept = async () => {
    if (!agreed || !user) return;
    if (!firstName.trim() || !lastName.trim() || !mobile.trim() || !country) {
      setError("Please fill in all profile fields before accepting.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await acceptTerms(user.id, user.email ?? "", {
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        mobile: mobile.trim(),
        country,
      });

      await supabase.auth.updateUser({
        data: {
          terms_accepted: true,
          terms_version: TERMS_VERSION,
          terms_accepted_at: new Date().toISOString(),
          first_name: firstName.trim(),
          last_name: lastName.trim(),
        },
      });

      document.cookie = `ss_terms_${user.id}=v1.0; path=/; max-age=31536000; SameSite=Lax`;
      router.push("/dashboard");
    } catch (err: any) {
      setError("Failed to record acceptance. Please try again.");
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 space-y-6">

      {/* Header */}
      <div className="text-center space-y-2">
        <div className="flex items-center justify-center gap-2 text-brand-500">
          <TrendingUp size={24} />
          <span className="text-xl font-bold text-white">StockSense360</span>
        </div>
        <h1 className="text-2xl font-bold text-white">Terms of Use & Legal Disclaimer</h1>
        <p className="text-sm text-gray-400">Version 1.0 · Effective 19 June 2026 · Governing jurisdiction: Mumbai, India</p>
      </div>

      {/* Welcome */}
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 text-sm text-amber-300">
        <strong>Welcome, {user?.email}</strong> — please complete your profile and accept the disclaimer below. Your details are stored securely with a timestamp and IP address.
      </div>

      {/* Profile fields */}
      <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-4">
        <h2 className="text-sm font-semibold text-white">Your Profile</h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">First Name *</label>
            <input
              type="text" placeholder="e.g. Rahul" value={firstName}
              onChange={e => setFirstName(e.target.value)}
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Last Name *</label>
            <input
              type="text" placeholder="e.g. Sharma" value={lastName}
              onChange={e => setLastName(e.target.value)}
              className={inputCls}
            />
          </div>
        </div>
        <div>
          <label className="text-xs text-gray-400 mb-1 block">Email</label>
          <input
            type="email" value={user?.email ?? ""} disabled
            className={inputCls + " opacity-50 cursor-not-allowed"}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Mobile Number *</label>
            <input
              type="tel" placeholder="+91 98765 43210" value={mobile}
              onChange={e => setMobile(e.target.value)}
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Country *</label>
            <select
              value={country} onChange={e => setCountry(e.target.value)}
              className={inputCls + " cursor-pointer"}
            >
              {COUNTRIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Scrollable terms */}
      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
        <div className="h-80 overflow-y-auto p-5 space-y-5 scrollbar-thin scrollbar-thumb-dark-border">
          {DISCLAIMER_SECTIONS.map((s) => (
            <div key={s.title}>
              <h3 className="text-sm font-semibold text-white mb-1.5">{s.title}</h3>
              <p className="text-sm text-gray-400 leading-relaxed">{s.body}</p>
            </div>
          ))}
          <div className="border-t border-dark-border pt-4">
            <p className="text-xs text-gray-500 leading-relaxed">
              <strong className="text-gray-400">Full disclaimer:</strong> By clicking "I Agree", you confirm that you are at least 18 years of age, that you have read and understood these Terms in full, and that you agree to be legally bound by them. Your acceptance is recorded with a timestamp, your user ID, and your IP address.
            </p>
          </div>
        </div>
      </div>

      {/* Checkbox */}
      <label className="flex items-start gap-3 cursor-pointer group">
        <div
          onClick={() => setAgreed(a => !a)}
          className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 transition-colors ${agreed ? "bg-brand-500 border-brand-500" : "border-dark-border group-hover:border-brand-500/60"}`}
        >
          {agreed && <CheckCircle size={14} className="text-white" />}
        </div>
        <span className="text-sm text-gray-300 leading-relaxed">
          I have read and understood the Terms of Use & Legal Disclaimer in full. I acknowledge that StockSense360 does not provide financial advice, that investing involves risk of loss, and that I am solely responsible for my investment decisions.
        </span>
      </label>

      {error && (
        <p className="text-xs text-red-400 bg-red-500/10 px-3 py-2 rounded-lg">{error}</p>
      )}

      {/* CTA */}
      <button
        onClick={handleAccept}
        disabled={!agreed || loading}
        className="w-full py-3 rounded-xl bg-brand-500 hover:bg-brand-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold transition-colors"
      >
        {loading ? "Recording acceptance…" : "I Agree — Enter StockSense360"}
      </button>

      <p className="text-center text-xs text-gray-600">
        If you do not agree, close this page. You will not be able to access the platform.
      </p>
    </div>
  );
}
