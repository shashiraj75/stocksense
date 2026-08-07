# StockSense360 — AI Stock Intelligence Platform

> Short, medium & long-term AI signals for Indian (NSE) and US markets.  
> Built with institutional-grade quant methods. Fully explainable. Zero subscription cost.

**Live:** Railway (backend) · Vercel (frontend) · [stocksense360.com](https://stocksense360.com)

> **Live operational status:** For release status, validation gates, feature flags, scheduler state, and operational blockers, see [`Documentation/Engineering-Handbook/Operations/Current-Release-Status.md`](Documentation/Engineering-Handbook/Operations/Current-Release-Status.md).

---

## What StockSense360 Does

- **BUY / HOLD / SELL signals** with confidence score, target price, and stop-loss for every stock
- **Daily Picks:** Up to 6 BUY ideas per horizon (short / medium / long), screened from the NSE and US universes. India generates once daily; US generates in two stages — a Pre-Open base run followed by a separate Premarket Review. Automated GitHub Actions triggering is active for both markets; scheduler dispatch is best-effort (GitHub Actions cron can fire late) and exact cron times, DST handling, and operational state change over time — see the Current Release Status register for the current, authoritative schedule rather than relying on a snapshot here. A future/planned intraday news validity overlay is documented in [`Intraday-News-Impact-Layer-Daily-Picks-Spec.md`](Documentation/Engineering-Handbook/Architecture/Intraday-News-Impact-Layer-Daily-Picks-Spec.md); it is documentation-only and not live production behavior.
- **Full explainability** — factor breakdown, bull/bear thesis, and reasoning bullets for every call
- **Learning Alpha infrastructure** — records prediction/outcome evidence and evaluates adaptive IC/meta-model learning in shadow; adaptive influence on live Daily Picks ranking remains contained behind `LEARNING_ALPHA_PRODUCTION_ENABLED` and is off by default
- **Paper Trading** — test signals with virtual money, track P&L, set stop-losses and targets
- **Trade Postmortem** — post-trade, evidence-based win/loss analysis for a closed Paper Trade. From a completed trade's history entry, "View Postmortem" opens `/postmortem/[tradeId]`: a deterministic executive summary, factor-by-factor explainability, an investor-facing "What You Can Learn" classification per factor (CONFIRMED / SUPPORTED BUT NOT PROVEN / NOT ESTABLISHED / DATA NEEDED FOR A DEEPER REPORT), and price-path evidence (MFE, MAE, target/stop level touches). Live in Production; see [Trade Postmortem Explainability — Production Closure](Documentation/Engineering-Handbook/Releases/Trade-Postmortem-Explainability-Production-Closure.md).
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
| Backend | FastAPI + Python 3.11 | Hosted on Railway |
| Frontend | Next.js 14 + TailwindCSS | Hosted on Vercel |
| Database | PostgreSQL (Supabase) | All user data persisted across restarts |
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

## Production Environment Variables (Railway)

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

### Learning Alpha Engine (Daily Picks) — infrastructure built, adaptive influence feature-gated off by default

Corrected 2026-08-07: this was previously listed as a live-sounding
production feature ("activates after N outcomes"). Per
`backend/services/alpha_engine/containment.py`, with
`LEARNING_ALPHA_PRODUCTION_ENABLED` unset (the repo default), Daily Picks
ranking uses fixed academic-prior IC weights, not this engine's live
adaptation:

1. **Outcome resolution** — records predictions vs. actual returns into `predictions`/`outcomes`.
2. **IC engine** — Bayesian-shrunk Information Coefficients per factor; computes in shadow mode for observability (activates after 60+ outcomes) but its output does not overwrite production ranking while containment is active.
3. **Regime detection** — KMeans clustering on VIX, S&P 500, crude, gold, USD/INR; this one component runs independently of the containment flag (`weight_adapter.run_adaptation`).
4. **Z-score normalisation** — cross-sectional factor normalisation, shadow-only under containment.
5. **Meta-model** — XGBoost / Ridge trained on outcomes (activates after 180+ outcomes), shadow-only under containment.
6. **Portfolio optimisation** — Ledoit-Wolf covariance, max 40% per position.

In short: this engine records and evaluates prediction/outcome evidence
continuously, but its adaptive production influence on live Daily Picks
ranking remains feature-gated off by default. See the "Experimental /
Shadow / Dormant" section below and
[`Current-Release-Status.md`](Documentation/Engineering-Handbook/Operations/Current-Release-Status.md)
for the authoritative current state.

---

## Data Persistence

All user data lives in Postgres and survives Railway restarts:

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
| Daily Picks | `/picks` | Top 6 BUY ideas per horizon + real backtest results |
| Heatmap | `/heatmap` | Sector performance tiles (IN / US) |
| Screener | `/screener` | Filter by PE, ROE, sector, signal |
| Watchlist | `/watchlist` | Saved stocks with live prices |
| Alerts | `/alerts` | Price alerts with live trigger detection |
| Portfolio | `/portfolio` | Holdings with AI signal per position |
| Paper Trade | `/paper-trading` | Simulated trading with P&L tracker |
| Trade Postmortem | `/postmortem/[tradeId]` | Explainable close-out report for a closed paper trade (evidence-backed factor breakdown, price-path evidence, contradictions) — flag-gated live in Production (`TRADE_POSTMORTEM_PRICE_PATH_ENABLED`) |
| Multibagger | `/multibagger` | Weekly-refreshed Multibagger screen (India Saturday / US Sunday) |
| Validation | `/validation` | Walk-forward hit rate, Sharpe, alpha |

---

## Experimental / Shadow / Dormant (not exposed as live user-facing surfaces)

These exist in the codebase and are covered by tests, but are gated off by
default and are not part of the live product surface above. See
[`Documentation/Engineering-Handbook/Operations/Current-Release-Status.md`](Documentation/Engineering-Handbook/Operations/Current-Release-Status.md)
for the authoritative, current lifecycle classification of every subsystem.

| Subsystem | Classification |
|---|---|
| Market Leadership (Relative Strength Rank, Sector Leadership, Trend Lifecycle, Market Breadth) | FEATURE-FLAGGED OFF / DEPLOYED DORMANT — code merged and deployed, all flags off, no UI exposed, scoring influence unconsumed |
| Learning Alpha Engine production activation | FEATURE-FLAGGED OFF — contained behind `LEARNING_ALPHA_PRODUCTION_ENABLED`, unset by default |
| Intelligence Engine / Universe Builder shadow observations | SHADOW / EXPERIMENTAL — behind `INTELLIGENCE_ENGINE_SHADOW_ENABLED`, unset by default |
| NSE Instrument Master (source registry, offline validators) | FOUNDATION / UNINTEGRATED — no production code path consumes it yet |
| RCI (`RCI_LIVE_STOCK_ANALYSIS_ENABLED`) | FEATURE-FLAGGED OFF — aggregate observability counters deployed, feature itself disabled |

---

For full technical documentation see [STOCKSENSE_DOCUMENTATION.md](Documentation/STOCKSENSE_DOCUMENTATION.md).
