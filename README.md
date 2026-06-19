# StockSense — AI Stock Intelligence Platform

> Short, medium & long-term AI signals for Indian (NSE) and US markets.  
> Built with institutional-grade quant methods. Fully explainable. Zero subscription cost.

**Live:** [stocksense-api-12ii.onrender.com](https://stocksense-api-12ii.onrender.com) (backend) · Vercel (frontend)

---

## What StockSense Does

- **BUY / HOLD / SELL signals** with confidence score, target price, and stop-loss for every stock
- **Daily Picks** — top 5 BUY ideas per horizon (short / medium / long) delivered every morning at 9 AM IST
- **Full explainability** — factor breakdown, bull/bear thesis, and reasoning bullets for every call
- **Learning engine** — tracks prediction outcomes, retrains factor weights weekly via IC (Information Coefficient)
- **Paper Trading** — test signals with virtual money, track P&L, set stop-losses and targets
- **Price Alerts** — get notified when a stock crosses your target price
- **Screener & Heatmap** — filter Nifty 100 / S&P 500; sector heatmap with colour-coded performance
- **Watchlist** — save favourite stocks with live prices and change%

---

## What Powers It

| Layer | Tool | Notes |
|-------|------|-------|
| Market data | yfinance (`yf.download`) | Real-time quotes, OHLCV, fundamentals |
| Indian fundamentals | screener.in (authenticated) | 10-year history, ROCE, CAGR, promoter % |
| Indian institutional flows | NSE FII/DII API | Daily flows in ₹ Cr |
| News & sentiment | Yahoo Finance RSS + Google News RSS | VADER NLP + financial lexicon |
| Technical indicators | `ta` library | RSI, MACD, BB, EMA, ADX, Stoch, OBV |
| Backend | FastAPI + Python 3.11 | Hosted on Render free tier |
| Frontend | Next.js 14 + TailwindCSS | Hosted on Vercel |
| Database | PostgreSQL (Render) | All user data persisted across restarts |
| Automation | GitHub Actions | Daily picks cron, weekly validation, keep-alive |

---

## Run Locally in 3 Steps

### Step 1 — Backend
```bash
cd "Stock Portfolio/stock-predictor/backend"
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
# API: http://localhost:8000
# Swagger: http://localhost:8000/docs
```

### Step 2 — Frontend
```bash
cd "Stock Portfolio/stock-predictor/frontend"
npm install
npm run dev
# App: http://localhost:3000
```

### Step 3 — Open browser
Go to http://localhost:3000

---

## Production Environment Variables (Render)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `USE_POSTGRES` | Set to `1` to enable Postgres persistence |
| `SCREENER_EMAIL` | screener.in login email (Indian fundamentals) |
| `SCREENER_PASSWORD` | screener.in login password |
| `PICKS_SECRET` | Secret header for picks generation cron |
| `FRONTEND_URL` | Vercel frontend URL for CORS |

---

## Prediction Engine

### Signal Formula
```
Composite Score = (Technical × W_tech) + (Fundamental × W_fund) + (Sentiment × W_sent)
                + Macro Adjustment + Quality Factor + Analyst Consensus
                - Risk Penalty
```
All scores on 0–100 scale. Score ≥ 60 → BUY · 45–59 → HOLD · < 45 → SELL.

### Weights by Horizon

| Horizon | Technical | Fundamental | Sentiment |
|---------|-----------|-------------|-----------|
| Short (1–5 days) | 70% | 15% | 15% |
| Medium (2–4 weeks) | 40% | 45% | 15% |
| Long (3–6 months) | 15% | 75% | 10% |

Weights are dynamically modulated by volatility and market regime (BULL / BEAR / SIDEWAYS).

### Learning Alpha Engine (Daily Picks)
1. **Outcome resolution** — compare past predictions vs actual returns
2. **IC engine** — Bayesian-shrunk Information Coefficients per factor (activates after 60+ outcomes)
3. **Regime detection** — KMeans clustering on VIX, S&P 500, crude, gold, USD/INR
4. **Z-score normalisation** — cross-sectional factor normalisation
5. **Meta-model** — XGBoost / Ridge trained on outcomes (activates after 180+ outcomes)
6. **Portfolio optimisation** — Ledoit-Wolf covariance, max 40% per position

---

## Data Persistence

All user data lives in Postgres and survives Render restarts:

| Data | Postgres Table |
|------|---------------|
| Watchlist | `watchlist` |
| Price alerts | `price_alerts` |
| Paper trades | `paper_trades`, `paper_portfolio` |
| Daily picks cache | `daily_picks_cache` |
| Validation results | `val_runs`, `val_signals` |
| Alpha engine learning | `predictions`, `outcomes`, `factor_ic_history` |

---

## Pages

| Page | URL | What it shows |
|------|-----|---------------|
| Dashboard | `/` | Top movers, live index bar, quick access |
| Stock Detail | `/stock/:symbol` | Full prediction, trade levels, news, chart |
| Daily Picks | `/picks` | Top 5 BUY ideas + real backtest results |
| Heatmap | `/heatmap` | Sector performance tiles (IN / US) |
| Screener | `/screener` | Filter by PE, ROE, sector, signal |
| Watchlist | `/watchlist` | Saved stocks with live prices |
| Alerts | `/alerts` | Price alerts with live trigger detection |
| Portfolio | `/portfolio` | Holdings with AI signal per position |
| Paper Trade | `/paper-trading` | Simulated trading with P&L tracker |
| Validation | `/validation` | Walk-forward hit rate, Sharpe, alpha |

---

For full technical documentation see [STOCKSENSE_DOCUMENTATION.md](./STOCKSENSE_DOCUMENTATION.md).
