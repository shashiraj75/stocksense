# StockSense — AI Stock Predictor (100% Free)

> Short, medium & long-term AI signals for US & Indian markets.
> **Zero cost. Zero API keys. Runs entirely on your machine.**

## What powers it (all free)

| Layer | Tool | Cost |
|-------|------|------|
| Market data (US + India) | yfinance | Free, no key |
| Charts | TradingView free widget | Free, no key |
| News | Yahoo Finance + Google News RSS | Free, no key |
| Sentiment NLP | VADER (rule-based) | Free, local |
| Technical indicators | `ta` library (RSI, MACD, BB, EMA) | Free, local |
| Backend | FastAPI + Python | Free, local |
| Frontend | Next.js 14 | Free, local |

---

## Run in 3 steps

### Step 1 — Backend
```bash
cd "Stock Portfolio/stock-predictor/backend"
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
# API running at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### Step 2 — Frontend
```bash
cd "Stock Portfolio/stock-predictor/frontend"
npm install
npm run dev
# App running at http://localhost:3000
```

### Step 3 — Open browser
Go to http://localhost:3000

---

## Features

- **Live quotes** — US (NYSE/NASDAQ) and India (NSE) stocks
- **AI predictions** — BUY / HOLD / SELL with confidence score and target price
- **3 horizons** — Short (1-10 days), Medium (1-3 months), Long (6M-3Y)
- **TradingView charts** — full interactive candlestick charts, free embed
- **News sentiment** — Yahoo Finance + Google News RSS with VADER NLP scoring
- **Screener** — filter stocks by PE, ROE, sector, market cap
- **Watchlist** — track your favourite US and India stocks
- **Dark UI** — built with Next.js + Tailwind CSS

## Prediction engine weights

| Horizon | Technicals | Fundamentals | Sentiment |
|---------|-----------|--------------|-----------|
| Short   | 60%       | 10%          | 30%       |
| Medium  | 40%       | 35%          | 25%       |
| Long    | 20%       | 65%          | 15%       |
