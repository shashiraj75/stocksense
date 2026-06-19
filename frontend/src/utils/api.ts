import axios from "axios";

export const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  timeout: 120000,  // 120s — Render prediction can take 90s+ under load
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
  company_name?: string;
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
  generated_at?: string;
  reasoning: { indicator: string; signal: string; reason: string }[];
  technical: { overall: Signal; rsi: number; macd_diff: number };
  fundamental_score: { score: number; reasons: string[] };
  sentiment_score: { score: number; label: string; bullish: number; bearish: number };
  market_regime?: { trend: string; score_adj: number; reason: string };
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

export const fetchPrediction = async (
  symbol: string,
  market: Market,
  horizon: Horizon,
  onComputing?: () => void,
): Promise<Prediction> => {
  // Poll up to 180 s (36 × 5 s) for background computation to complete
  for (let attempt = 0; attempt < 36; attempt++) {
    const res = await api.get<Prediction | { status: string; retry_after?: number }>(
      `/api/predictions/${symbol}`,
      {
        params: { market, horizon },
        validateStatus: (s) => s === 200 || s === 202,
      },
    );
    if (res.status === 200) return res.data as Prediction;
    // Check for error field returned in a 202 response body
    if ((res.data as any)?.error) throw new Error((res.data as any).error);
    // 202 = computing in background — notify caller and wait
    if (attempt === 0) onComputing?.();
    const delay = ((res.data as { retry_after?: number }).retry_after ?? 5) * 1000;
    await new Promise((r) => setTimeout(r, delay));
  }
  throw new Error("Prediction timed out after 120 s");
};

export const fetchNews = (symbol: string, market: Market) =>
  api
    .get<{ articles: NewsArticle[] }>(`/api/news/${symbol}`, { params: { market } })
    .then((r) => r.data);

type Mover = { symbol: string; price: number; change_pct: number; name?: string };
export const fetchTopMovers = (market: Market) =>
  api
    .get<{ movers: Mover[]; gainers: Mover[]; losers: Mover[]; market_open: boolean }>("/api/screener/top-movers", {
      params: { market },
    })
    .then((r) => r.data);

export const searchStocks = (q: string, market: Market | "ALL" = "ALL") =>
  api.get<{ symbol: string; name: string }[]>("/api/stocks/search", { params: { q, market } }).then((r) => r.data);

export interface IndexQuote {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  change_pts: number | null;
}

export const fetchIndices = (market: Market | "CRYPTO") =>
  api.get<{ indices: IndexQuote[] }>("/api/stocks/indices", { params: { market } }).then((r) => r.data);

export const fetchFactorAttribution = (symbol: string, market: Market, horizon: Horizon) =>
  api
    .get(`/api/stocks/${symbol}/factor-attribution`, { params: { market, horizon } })
    .then((r) => r.data);

export interface ScoreHistoryPoint {
  date: string;
  composite_score: number | null;
  quality_score: number | null;
  growth_score: number | null;
  valuation_score: number | null;
  technical_score: number | null;
  sentiment_score: number | null;
  risk_score: number | null;
  confidence_score: number | null;
  signal: string | null;
}

export const fetchScoreHistory = (symbol: string, horizon: Horizon, days = 90) =>
  api
    .get<{ symbol: string; horizon: string; window_days: number; points: ScoreHistoryPoint[] }>(
      `/api/stocks/${symbol}/score-history`,
      { params: { horizon, days } }
    )
    .then((r) => r.data);

// ─── Paper Trading ────────────────────────────────────────────────────────────

export interface PaperTrade {
  id: number;
  symbol: string;
  market: Market;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
  status: "OPEN" | "CLOSED";
  signal: string;
  horizon: string;
  opened_at: string;
  closed_at: string | null;
  invested: number;
  realized_pnl?: number;
}

export interface PaperPortfolio {
  session_id: string;
  cash: number;
  starting_cash: number;
  open_trades: PaperTrade[];
  closed_trades: PaperTrade[];
  total_realized_pnl: number;
}

export const fetchPaperPortfolio = (sessionId: string) =>
  api.get<PaperPortfolio>("/api/paper-trading/portfolio", { params: { session_id: sessionId } }).then((r) => r.data);

export const placePaperBuy = (data: {
  session_id: string; symbol: string; market: Market;
  quantity: number; price: number; signal?: string; horizon?: string;
  stop_loss?: number | null; target_price?: number | null;
}) => api.post("/api/paper-trading/buy", data).then((r) => r.data);

export const closePaperTrade = (tradeId: number, sessionId: string, price: number) =>
  api.post(`/api/paper-trading/sell/${tradeId}`, { session_id: sessionId, price }).then((r) => r.data);

export const resetPaperPortfolio = (sessionId: string) =>
  api.post("/api/paper-trading/reset", null, { params: { session_id: sessionId } }).then((r) => r.data);

export const editPaperTrade = (tradeId: number, sessionId: string, stopLoss: number | null, targetPrice: number | null, entryPrice?: number | null) =>
  api.patch(`/api/paper-trading/trade/${tradeId}`, { session_id: sessionId, stop_loss: stopLoss, target_price: targetPrice, entry_price: entryPrice ?? null }).then((r) => r.data);
