"use client";
import { useEffect, useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import type { Market } from "@/utils/api";

const DISCLAIMERS: Record<Market, { title: string; regulator: string; points: string[] }> = {
  US: {
    title: "US Market — Important Disclaimer",
    regulator: "SEC (U.S. Securities and Exchange Commission)",
    points: [
      "StockSense is an AI-powered research tool and is NOT a registered investment adviser under the Investment Advisers Act of 1940.",
      "Signals, predictions, and target prices are generated algorithmically from public data and do NOT constitute financial, investment, or trading advice.",
      "Past backtest performance does not guarantee future results. Markets can and do behave differently from historical patterns.",
      "Always consult a licensed financial advisor or broker before making investment decisions.",
      "Investing in US equities involves risk, including possible loss of principal.",
      "This tool does not have access to real-time data — prices may be delayed up to 15 minutes.",
    ],
  },
  IN: {
    title: "Indian Market — Important Disclaimer",
    regulator: "SEBI (Securities and Exchange Board of India)",
    points: [
      "StockSense is an AI-powered research tool and is NOT registered with SEBI as an Investment Adviser under the SEBI (Investment Advisers) Regulations, 2013.",
      "Signals, predictions, and target prices are generated algorithmically from public data and do NOT constitute financial, investment, or trading advice.",
      "Past backtest performance does not guarantee future results. NSE/BSE markets are subject to volatility, regulatory changes, and macroeconomic factors.",
      "Always consult a SEBI-registered investment adviser or stockbroker before making investment decisions.",
      "Investing in Indian equities involves risk, including possible loss of principal. F&O trading carries additional risk.",
      "This tool does not have access to real-time data — NSE prices may be delayed.",
    ],
  },
};

interface Props {
  market: Market;
}

export function MarketDisclaimer({ market }: Props) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const key = `disclaimer_ack_${market}`;
    if (!sessionStorage.getItem(key)) {
      setVisible(true);
    }
  }, [market]);

  const acknowledge = () => {
    sessionStorage.setItem(`disclaimer_ack_${market}`, "1");
    setVisible(false);
  };

  if (!visible) return null;

  const d = DISCLAIMERS[market];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-dark-card border border-dark-border rounded-2xl max-w-lg w-full shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-5 border-b border-dark-border">
          <ShieldAlert size={22} className="text-yellow-400 shrink-0" />
          <h2 className="font-bold text-white text-base flex-1">{d.title}</h2>
          <button onClick={acknowledge} className="text-gray-500 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4 max-h-[60vh] overflow-y-auto">
          <p className="text-xs text-yellow-400 font-medium uppercase tracking-wide">
            Not regulated by {d.regulator}
          </p>
          <ul className="space-y-3">
            {d.points.map((point, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-gray-300">
                <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-yellow-400" />
                {point}
              </li>
            ))}
          </ul>
          <div className="bg-yellow-400/10 border border-yellow-400/30 rounded-xl p-4 mt-2">
            <p className="text-yellow-300 text-xs font-medium">
              By clicking "I Understand", you acknowledge that StockSense provides informational content only
              and you take full responsibility for any investment decisions you make.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-dark-border flex gap-3 justify-end">
          <button
            onClick={acknowledge}
            className="px-6 py-2.5 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-semibold transition-colors"
          >
            I Understand — Continue
          </button>
        </div>
      </div>
    </div>
  );
}
