import axios from "axios";

export const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  timeout: 90000,  // 90s — Render cold start takes 50-70s; must outlast it
});

export type Market = "US" | "IN";
export type Horizon = "short" | "medium" | "long";
export type Signal = "BUY" | "HOLD" | "SELL";

export interface StockQuote {
  symbol: string;
  market: Market;
  price: number;
  prev_close: number;
  change: number;
  change_pct: number;
  volume: number;
  market_cap: number;
  fifty_two_week_high: number;
  fifty_two_week_low: number;
}

export interface OHLCVBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Prediction {
  symbol: string;
  market: Market;
  horizon: Horizon;
  signal: Signal;
  confidence: number;
  current_price: number;
  target_price: number;
  reasoning: { indicator: string; signal: string; reason: string }[];
  technical: { overall: Signal; rsi: number; macd_diff: number };
  fundamental_score: { score: number; reasons: string[] };
  sentiment_score: { score: number; label: string; bullish: number; bearish: number };
}

export interface NewsArticle {
  title: string;
  source: string;
  url: string;
  published_at: string;
  description: string;
  sentiment?: { label: string; score: number };
}

// ─── API calls ────────────────────────────────────────────────

export const fetchQuote = (symbol: string, market: Market) =>
  api.get<StockQuote>(`/api/stocks/quote/${symbol}`, { params: { market } }).then((r) => r.data);

export const fetchOHLCV = (symbol: string, market: Market, period = "1y", interval = "1d") =>
  api
    .get<{ data: OHLCVBar[] }>(`/api/stocks/ohlcv/${symbol}`, { params: { market, period, interval } })
    .then((r) => r.data);

export const fetchPrediction = (symbol: string, market: Market, horizon: Horizon) =>
  api
    .get<Prediction>(`/api/predictions/${symbol}`, { params: { market, horizon } })
    .then((r) => r.data);

export const fetchNews = (symbol: string, market: Market) =>
  api
    .get<{ articles: NewsArticle[] }>(`/api/news/${symbol}`, { params: { market } })
    .then((r) => r.data);

export const fetchTopMovers = (market: Market) =>
  api
    .get<{ movers: { symbol: string; price: number; change_pct: number }[] }>("/api/screener/top-movers", {
      params: { market },
    })
    .then((r) => r.data);

export const searchStocks = (q: string, market: Market | "ALL" = "ALL") =>
  api.get<{ symbol: string; name: string }[]>("/api/stocks/search", { params: { q, market } }).then((r) => r.data);
