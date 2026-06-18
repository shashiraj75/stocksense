# StockSense — Complete Product & Technical Documentation

> **Live Document** — Updated automatically as the product evolves.  
> Last updated: 2026-06-18

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Sources](#3-data-sources)
4. [Core Prediction Engine](#4-core-prediction-engine)
5. [Technical Analysis Module](#5-technical-analysis-module)
6. [Fundamental Scoring Module](#6-fundamental-scoring-module)
7. [Sentiment Analysis Module](#7-sentiment-analysis-module)
8. [Global Macro Context Module](#8-global-macro-context-module)
9. [Quality Factors Module](#9-quality-factors-module)
10. [Risk Penalty Framework](#10-risk-penalty-framework)
11. [Confidence Engine](#11-confidence-engine)
12. [Target Price & Trade Levels](#12-target-price--trade-levels)
13. [Daily Picks Engine](#13-daily-picks-engine)
14. [Backtesting & Validation Engine](#14-backtesting--validation-engine)
15. [Crypto Prediction Module](#15-crypto-prediction-module)
16. [Screener & Universe Management](#16-screener--universe-management)
17. [API Reference](#17-api-reference)
18. [Frontend Pages & Components](#18-frontend-pages--components)
19. [Infrastructure & Deployment](#19-infrastructure--deployment)
20. [Automation Workflows](#20-automation-workflows)
21. [Factor Weights by Horizon](#21-factor-weights-by-horizon)
22. [Key Design Principles](#22-key-design-principles)

---

## 1. Product Overview

**StockSense** is an AI-powered stock prediction and portfolio intelligence platform built for Indian and US equity markets. It combines institutional-grade quantitative signals with a consumer-friendly interface to deliver actionable BUY / HOLD / SELL signals with full explainability.

### What StockSense Does

- Generates **BUY / HOLD / SELL signals** for Nifty 100, US large-cap, and top cryptocurrencies
- Delivers **Daily Picks** every morning at 9 AM IST — top 5 BUY ideas per horizon (short / medium / long)
- Shows **why** every signal was generated — factor breakdown, confidence scores, reasoning bullets
- Provides **trade levels** — entry zone, stop-loss, and target price with R:R ratio
- Runs a **learning engine** — tracks prediction outcomes and retrains factor weights weekly
- Supports **screener**, **backtest**, **watchlist**, and **alerts**

### Supported Markets

| Market | Universe | Horizons |
|--------|----------|----------|
| India (NSE) | Nifty 100 | Short (1–5 days), Medium (2–4 weeks), Long (3–6 months) |
| US | S&P 500 large-caps | Short, Medium, Long |
| Crypto | BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT | Short, Medium, Long |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      FRONTEND (Next.js 14)                  │
│  Dashboard · Stock Detail · Daily Picks · Screener          │
│  Backtest · Watchlist · Alerts · Portfolio                  │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTPS / REST
┌───────────────────────────▼─────────────────────────────────┐
│                   BACKEND (FastAPI / Python)                 │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Prediction  │  │  Daily Picks │  │  Screener/Heatmap │  │
│  │   Engine     │  │   Engine     │  │     Service       │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘  │
│         │                 │                    │             │
│  ┌──────▼─────────────────▼────────────────────▼──────────┐  │
│  │  Technical · Fundamental · Sentiment · Quality · Macro  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────┐  ┌──────────────────────────────┐   │
│  │  Validation /      │  │  Outcome Logger / IC Engine  │   │
│  │  Backtester        │  │  / Meta-Model                │   │
│  └───────────────────┘  └──────────────────────────────┘   │
└──────────────────────────────┬──────────────────────────────┘
                               │
         ┌─────────────────────┼──────────────────────┐
         ▼                     ▼                      ▼
   yfinance / NSE        screener.in            PostgreSQL /
   BSE / RSS feeds       BSE / FII-DII          SQLite (local)
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14, React, TypeScript, TailwindCSS, Recharts |
| Backend | Python 3.11, FastAPI, Uvicorn |
| Auth | Supabase (JWT) |
| Database | PostgreSQL (production), SQLite (local) |
| Cache | In-memory (TTL-based), disk (picks_cache.json) |
| Hosting | Render.com (backend), Vercel (frontend) |
| Automation | GitHub Actions (cron jobs) |

---

## 3. Data Sources

| Source | Data Provided | Update Frequency | Used For |
|--------|--------------|-----------------|----------|
| **yfinance** | Price, volume, P/E, ROE, margins, FCF, beta, analyst targets | Real-time (15–60s lag) | All markets |
| **screener.in** | 10-year financials, ROCE, 3Y/5Y CAGR, Piotroski fields, promoter %, pledge %, cashflow | Daily (4h cache) | India only |
| **BSE API** | Current fundamentals for renamed/merged stocks | Daily (1h cache) | India fallback |
| **NSE FII/DII API** | Daily institutional flows (₹ Cr) | Daily (30min cache) | India quality signal |
| **NSE Pledge API** | Promoter pledge %, quarterly | Quarterly | India risk signal |
| **Yahoo Finance RSS** | News headlines per symbol | Real-time (10min cache) | Sentiment |
| **Google News RSS** | "{symbol} stock India" search | Real-time (10min cache) | Sentiment fallback |
| **Economic Times RSS** | India economy & stock news | Real-time | India sentiment |
| **MoneyControl RSS** | Stock & sector news | Real-time | India sentiment |
| **yfinance macro tickers** | S&P 500, VIX, Crude, Gold, USD/INR, Nifty IT, Nifty Bank | Real-time (15min cache) | Global macro |

### Data Fallback Chain (India)
```
yfinance → screener.in (if <5 key fields) → BSE API (if still sparse)
```

---

## 4. Core Prediction Engine

**File:** `backend/services/prediction_engine.py`

### Signal Generation Formula

```
Composite Score = (Tech × W_tech) + (Fund × W_fund) + (Sent × W_sent)
                + Global Macro Adjustment
                + Analyst Consensus Adjustment
                + 52-Week Position Adjustment
                + Quality Factor Adjustment
                + Rounding Adjustment
                - Risk Penalty
```

All component scores are on a **0–100 scale** (50 = neutral). The composite is also 0–100.

### Signal Thresholds

| Composite Score | Signal | Confidence Calculation |
|----------------|--------|----------------------|
| ≥ 70 | **BUY** | `(score − 70) / 30 × 100%` |
| 55 – 69 | **HOLD** | `min(25%, abs(score − 62) × 3%)` |
| < 55 | **SELL** | `(55 − score) / 55 × 100%` |

### Dynamic Weights (Horizon × Volatility × Regime)

**Base weights by horizon:**

| Horizon | Technical | Fundamental | Sentiment |
|---------|-----------|-------------|-----------|
| Short (1–5 days) | 70% | 15% | 15% |
| Medium (2–4 weeks) | 40% | 45% | 15% |
| Long (3–6 months) | 15% | 75% | 10% |

**Volatility modulation** (applied on top of base weights):

| Volatility Level | Annualised Vol | Technical | Fundamental |
|-----------------|---------------|-----------|-------------|
| High | > 35% | Reduced to 10% | Boosted |
| Normal | 15–35% | Base | Base |
| Low | < 15% | Boosted to 75% | Reduced |

**Regime modulation:**
- **BULL regime**: Boost technical, reduce fundamental
- **BEAR regime**: Boost fundamental, reduce technical
- **SIDEWAYS**: No modulation (enables mean-reversion trades)

### Prediction Caching

- **TTL:** 15 minutes per `(symbol:market:horizon)` key
- **Max size:** 300 entries (Render 512 MB free-tier limit)
- **Eviction:** LRU — oldest entry dropped when full
- **Async pattern:** First request returns HTTP 202 (computing); client polls every 5s; result cached on completion

---

## 5. Technical Analysis Module

**File:** `backend/services/technical_indicators.py`

### Indicators Computed

| Category | Indicator | Parameters |
|----------|-----------|-----------|
| **Momentum** | RSI | 14-period |
| | Stochastic RSI | 14-period |
| | Williams %R | 14-period |
| | CCI | 20-period |
| | Stochastic Oscillator | K=14, D=3 |
| **Trend** | MACD | 12/26/9 EMA |
| | EMA | 20, 50, 200 periods |
| | ADX + DI+/DI− | 14-period |
| **Volatility** | Bollinger Bands | 20-period, 2σ |
| | ATR | 14-period |
| **Volume** | OBV | Rolling |
| | Volume SMA | 20-period |
| | VWAP | 20-day rolling |

### Scoring Rules

**RSI:**
- < 30 (oversold): **+15 pts**
- 30–45 (recovering): **+7 pts**
- 60–70 (elevated): **−7 pts**
- > 70 (overbought): **−15 pts**

**MACD:**
- Above signal line: **+12 pts**
- Below signal line: **−12 pts**

**EMA Trend:**
- Price > EMA200 (bull market structure): **+10 pts**
- EMA20 > EMA50 (golden cross zone): **+8 pts**
- EMA20 < EMA50 (death cross zone): **−8 pts**

**ADX (Trend Strength):**
- ADX > 25 AND +DI > −DI (strong uptrend): **+10 pts**
- ADX > 25 AND −DI > +DI (strong downtrend): **−10 pts**
- ADX ≤ 25 (sideways): **0 pts**

**Bollinger Bands (%B):**
- Near lower band (< 0.1): **+8 pts** (oversold)
- Near upper band (> 0.9): **−8 pts** (overbought)

**Volume Confirmation:**
- Up-day volume > down-day volume: **+8 pts**
- Down-day volume > up-day volume: **−8 pts**

**Candlestick Patterns:**

| Pattern | Points | Signal |
|---------|--------|--------|
| Hammer | +5 | BUY |
| Morning Star | +5 | BUY |
| Bullish Engulfing | +5 | BUY |
| Shooting Star | −5 | SELL |
| Evening Star | −5 | SELL |
| Bearish Engulfing | −5 | SELL |

### Technical Signal Output

- Score ≥ 58: **BUY**
- Score ≤ 42: **SELL**
- 42–58: **HOLD**

---

## 6. Fundamental Scoring Module

**File:** `backend/services/prediction_engine.py` → `_fundamental_score()`

### Quality Gate (Hard Rejection)

A stock is flagged **REJECTED** before scoring if:
- ROE < −10% AND Profit Margin < −5%
- Negative Operating Cash Flow (medium/long horizons only)
- D/E ratio > 500% (non-financial sector)

### Scoring Components

#### Valuation

| Metric | Threshold (India / US) | Points |
|--------|----------------------|--------|
| P/E | < 18 IN / < 15 US (cheap) | +12 |
| P/E | < 30 IN / < 25 US (fair) | +6 |
| P/E | > 50 IN / > 40 US (expensive) | −10 |
| P/B | < 2.5 IN / < 2 US | +6 |
| PEG | < 0.75 | +16 |
| PEG | 0.75–1.0 | +10 |
| PEG | > 2.5 | −12 |
| FCF Yield | > 5% | +10 |
| FCF Yield | 3–5% | +5 |

#### Growth

| Metric | Threshold | Points |
|--------|-----------|--------|
| Revenue Growth YoY | > 20% | +12 |
| Revenue Growth YoY | 5–20% | +5 |
| Revenue Growth YoY | < −5% | −10 |
| Revenue CAGR 3Y (screener) | > 15% | +8 |
| Revenue CAGR 3Y | < 0% | −6 |
| Profit CAGR 3Y (screener) | > 15% | +8 |
| Profit CAGR 3Y | < −10% | −6 |
| EPS Growth | > 20% | +8 |
| EPS Growth | < −10% | −8 |
| Revenue acceleration | Latest > prior + 5% | +6 |
| Revenue deceleration | Latest < prior − 5% | −6 |

#### Profitability

| Metric | Threshold | Points |
|--------|-----------|--------|
| ROE | > 20% | +12 |
| ROE | 10–20% | +5 |
| ROE | < 0% | −10 |
| ROCE (screener) | > 20% | +10 |
| ROCE | 12–20% | +4 |
| ROCE | < 6% | −6 |
| Operating Margin | > 25% | +10 |
| Operating Margin | 10–25% | +4 |
| Operating Margin | < 0% | −10 |
| Net Margin | > 20% | +8 |
| Net Margin | < 0% | −8 |

#### Cash Flow (yfinance + screener.in fallback)

| Metric | Threshold | Points |
|--------|-----------|--------|
| Free Cash Flow | Positive | +10 |
| FCF Growth | > 10% YoY | +6 |
| Free Cash Flow | Negative | −8 |

> **Note:** For Indian stocks where yfinance cashflow is empty, operating CF and investing CF are fetched from screener.in's `#cash-flow` section.

#### Leverage & Solvency

| Metric | Threshold | Points |
|--------|-----------|--------|
| D/E | > 300% | −12 |
| D/E | 150–300% | −5 |
| D/E | < 50% | +5 |
| Current Ratio | > 2.0 | +8 |
| Current Ratio | 1.2–2.0 | +3 |
| Current Ratio | < 1.0 | −10 |
| Net Cash Position | More cash than debt | +6 |

#### Shareholding (India)

| Metric | Threshold | Points |
|--------|-----------|--------|
| FII + DII combined | > 50% | +6 |
| FII + DII combined | 25–50% | +3 |
| Promoter holding | > 55% | +5 |
| Promoter holding | < 25% | −5 |
| Promoter pledge | > 50% | −15 |
| Promoter pledge | 25–50% | −8 |
| Promoter pledge | 10–25% | −3 |
| Promoter pledge | 0% | +3 |

---

## 7. Sentiment Analysis Module

**File:** `backend/services/news_sentiment.py`

### Two-Layer Scoring

**Layer 1 — Financial Lexicon (60% weight):**
- ~80 domain-specific phrases with pre-calibrated scores
- Examples: `"beat expectations"` → +0.75, `"profit warning"` → −0.80, `"upgrade"` → +0.65
- Designed to override generic sentiment on financial language

**Layer 2 — VADER Sentiment (40% weight):**
- Title sentiment (70%) + Description sentiment (30%)
- Standard NLP library tuned for social media / news

**Final blend:** `score = 0.60 × lexicon + 0.40 × VADER`

### News Sources (per prediction)

| Source | Articles | Priority |
|--------|----------|----------|
| Yahoo Finance RSS (`{symbol}`, `{symbol}.NS`) | 10 | Primary |
| Google News RSS (`{symbol} stock India`) | 10 | Secondary |
| Economic Times RSS | 10 | India supplement |
| MoneyControl RSS | 10 | India supplement |

**Cache TTL:** 10 minutes per symbol

### Sentiment Classification

| Score | Label |
|-------|-------|
| ≥ +0.05 | BULLISH |
| −0.05 to +0.05 | NEUTRAL |
| ≤ −0.05 | BEARISH |

**Score conversion:** `sentiment_score = 50 + (bullish − bearish) / total × 50`

**When no news available:** Returns neutral (50), redistributes weight to technical + fundamental, sets `data_available = False` flag.

---

## 8. Global Macro Context Module

**File:** `backend/services/global_context.py`

### Macro Indicators Tracked

| Indicator | Ticker | What It Signals |
|-----------|--------|----------------|
| S&P 500 | ^GSPC | US demand → IT/pharma export tailwind |
| NASDAQ | ^IXIC | Tech hiring, demand for Indian IT services |
| VIX | ^VIX | FII flows: high VIX → FII outflows from India |
| USD/INR | INR=X | Weak INR → pharma/IT revenue boost; import cost rise |
| Crude Brent | BZ=F | Oil import cost, OMC margins |
| Gold | GC=F | Jewelry demand (Titan, Muthoot) |
| Nifty IT Index | ^CNXIT | Sector momentum for IT stocks |
| Nifty Bank Index | ^NSEBANK | Banking sector rotation signal |

**Cache TTL:** 15 minutes (fetched in parallel)

### Stock-Specific Macro Sensitivity Map

Over 100 Nifty stocks mapped to their macro sensitivities:

| Stock Category | Tailwind Factors | Headwind Factors |
|---------------|-----------------|-----------------|
| IT (TCS, INFY, WIPRO, HCL) | USD/INR weakness, S&P 500 up, NASDAQ up | INR strengthening |
| Pharma (SUNPHARMA, DRREDDY) | USD/INR weakness, S&P 500 up | — |
| OMCs (BPCL, IOC) | Crude down | Crude up (margin squeeze) |
| Oil producers (ONGC) | Crude up | Crude down |
| Paints (ASIANPAINT, PIDILITIND) | — | Crude up (TiO2 input cost) |
| Banks (HDFCBANK, ICICIBANK) | — | VIX up (FII sensitivity) |
| Jewelry (TITAN) | Gold stable | Gold up (input cost) |

### Stock Adjustment Calculation

```
stock_adj = Σ sensitivity_i × (factor_i − benchmark_i)
Tailwind factors: +2 to +4 points
Headwind factors: −2 to −4 points
```

---

## 9. Quality Factors Module

**File:** `backend/services/quality_factors.py`

> Applied for **India (Nifty 100) only**, on medium and long horizons.

### 10 Dimensions of Quality

#### 1. Earnings Revisions (Weight: 12–14%)
- EPS surprise trend (beat vs miss last 4 quarters): ±16 pts
- Analyst upgrade/downgrade momentum: ±8 pts
- Forward PE compression vs trailing PE: ±8 pts

#### 2. Institutional Ownership (Weight: 5–6%)
- Holdings > 50%: +14 pts
- Holdings 30–50%: +8 pts
- Holdings 15–30%: +3 pts
- Holdings < 5%: −5 pts
- Institution count > 300: +6 pts; < 20: −4 pts

#### 3. Institutional Flow Proxy / MF Trend (Weight: 5–13%)
Blends price-volume signals (OBV trend, MFI, accumulation pattern) with real NSE FII/DII data:
- 60% price-volume proxy + 40% real FII/DII flows (when available)

#### 4. Relative Strength (Weight: 7–15%)
Stock return vs NIFTY 50 over 1M, 3M, 6M:
- Outperform > 10%: +12 pts
- Outperform 4–10%: +6 pts
- Underperform > 10%: −12 pts
- Underperform 4–10%: −6 pts

#### 5. Sector Strength (Weight: 7–15%)
Sector index momentum vs NIFTY 50:
- Sector outperform > 5%: +16 pts
- Sector outperform 2–5%: +8 pts
- Sector underperform > 5%: −14 pts
- Sector underperform 2–5%: −7 pts

#### 6. Valuation Quality (Weight: 8–17%)
Multi-dimensional valuation scoring:
- PEG < 0.75: +16 pts; > 2.5: −12 pts
- EV/EBITDA vs sector: ±10 pts at major discount/premium
- Sector-relative PE: ±12 pts at 30% discount
- P/B for banks < 1.0: +12 pts; > 4.0: −8 pts
- Analyst target upside > 30%: +10 pts (margin of safety)

#### 7. Risk Management (Weight: 10%)
- Max Drawdown 12M < −10%: +14 pts; < −40%: −14 pts
- Volatility Percentile bottom 25%: +10 pts; top 20%: −10 pts
- Sharpe Ratio > 1.5: +12 pts; < 0: −10 pts
- Downside Deviation < 10%: +6 pts; > 30%: −6 pts

#### 8. Corporate Actions (Weight: 3–10%)
- Dividend payer 5Y+: +8 pts
- Growing dividends: +6 pts
- Payout ratio 0–40%: +4 pts; > 80%: −6 pts
- Active buyback: +8 pts
- Share dilution: −5 pts

#### 9. Liquidity (Weight: 4–8%)
- Market cap ₹2T+: +10 pts; < ₹10B: −8 pts
- Avg daily volume > 5M: +8 pts; < 100K: −8 pts
- Beta 0.5–1.2: +4 pts; > 2.0: −5 pts

#### 10. Quality Metrics (Weight: 4–12%)

**Piotroski F-Score (9-point scale):**

| Point | Criterion |
|-------|-----------|
| P1 | ROA > 0 |
| P2 | Operating Cash Flow > 0 |
| P3 | ROA improving YoY |
| P4 | Accruals: cash earnings > accrual earnings |
| P5 | Leverage decreasing |
| P6 | Current ratio improving |
| P7 | No share dilution |
| P8 | Gross margin expanding |
| P9 | Asset turnover improving |

- Score ≥ 8: +20 pts
- Score 6–7: +10 pts
- Score 4–5: 0 pts
- Score < 4: −12 pts

**ROIC (Return on Invested Capital):**
- > 20%: +14 pts
- 12–20%: +8 pts
- 6–12%: +2 pts
- < 6%: −8 pts

### Horizon-Based Weighting

| Dimension | Short | Medium | Long |
|-----------|-------|--------|------|
| Earnings Revisions | 13% | 14% | 12% |
| Institutional Ownership | 5% | 6% | 6% |
| MF/FII Flow | 6% | 8% | 10% |
| Relative Strength | 15% | 11% | 7% |
| Sector Strength | 15% | 11% | 7% |
| Valuation Quality | 8% | 13% | 17% |
| Risk Management | 10% | 10% | 10% |
| Corporate Actions | 3% | 6% | 10% |
| Liquidity | 8% | 6% | 4% |
| Quality Metrics | 4% | 6% | 12% |
| Flow Proxy | 13% | 9% | 5% |

---

## 10. Risk Penalty Framework

Applied **after** all signal scoring. Never adds points — only subtracts (risk override).

| Risk Factor | Condition | Penalty |
|-------------|-----------|---------|
| High leverage | D/E > 300% | −8 pts |
| Elevated leverage | D/E 200–300% | −4 pts |
| High beta | Beta > 2.0 | −6 pts |
| Elevated beta | Beta 1.6–2.0 | −3 pts |
| Negative FCF | FCF < 0 | −5 pts |
| Negative ROE | ROE < 0 | −5 pts |
| Poor risk profile | Risk score < 35 | −5 pts |
| Earnings volatility | CV > 0.5 | −4 pts |

**Maximum penalty capped at −30 pts** (prevents extreme scores on genuinely bad risk/reward stocks).

---

## 11. Confidence Engine

Answers: **"How much should you trust this signal?"**

### Five Components

| Component | What It Measures | Weight (Full) | Weight (Bootstrap) |
|-----------|-----------------|--------------|-------------------|
| Data Completeness | % of key fundamental fields present | 25% | 31.25% |
| Factor Agreement | % of factors agreeing with signal direction | 25% | 31.25% |
| Earnings Stability | Quality earnings_revision sub-score | 15% | 18.75% |
| Regime Certainty | Strength of current market trend (BULL/BEAR vs SIDEWAYS) | 15% | 18.75% |
| Historical Factor Reliability | Live IC values from outcome logger (requires 60+ resolved predictions) | 20% | 0% (falls back to 50) |

### Sector-Aware Field Sets

**Indian Non-Financial:** PE, ROE, revenue growth, D/E, margin, beta (yfinance) + 3Y CAGR, ROCE, FII, promoter (screener) = 10 fields

**Indian Financial (banks/NBFCs):** PE, ROE, revenue growth, profit margin, EPS growth, beta (yfinance) + 3Y CAGR, FII, promoter (screener) = 10 fields

**US Stocks:** PE, ROE, revenue growth, D/E, margin, EPS growth, FCF, beta, ROCE = 13 fields

### Confidence Bands

| Score | Band |
|-------|------|
| ≥ 80 | **HIGH** |
| 60–79 | **MEDIUM** |
| < 60 | **LOW** |

### Percentile Context

| Score | Label |
|-------|-------|
| ≥ 80 | Top 10% of all Nifty predictions |
| ≥ 72 | Top 20% |
| ≥ 65 | Top 35% |
| ≥ 58 | Top 50% |
| ≥ 50 | Below average |
| < 50 | Low range |

---

## 12. Target Price & Trade Levels

**File:** `backend/services/prediction_engine.py` → `_target_price()`, `_trade_levels()`

### Target Price

| Horizon | Method |
|---------|--------|
| **Short** | ATR × 2.5 × confidence factor — moves 1–5 day magnitude |
| **Medium** | Analyst mean target (70%) + price-based projection (30%); BUY floor: `max(blend, price × 1.05)` |
| **Long** | `analyst_target × (1 + EPS_growth)^2`; BUY floor: `max(target, price × 1.15)` |

### Trade Levels

```
Take Profit  = Model Target Price (consistent with signal)
Stop Loss    = ATR-based, adjusted to maintain minimum R:R of 1.5×
Entry Zone   = [price − 0.3×ATR, price + 0.1×ATR] for BUY
               [price − 0.1×ATR, price + 0.3×ATR] for SELL
R:R Ratio    = (target − price) / (price − stop_loss)
```

**ATR multipliers by horizon:**

| Horizon | Base SL | Floor SL | Max SL |
|---------|---------|----------|--------|
| Short | 1.5× ATR | 1× ATR | 25% of price |
| Medium | 3× ATR | 2× ATR | 25% of price |
| Long | 5× ATR | 3× ATR | 25% of price |

---

## 13. Daily Picks Engine

**File:** `backend/services/daily_picks.py`

### Execution Schedule

- Triggered: **Every weekday at 9:00 AM IST** (via GitHub Actions → POST `/api/picks/generate`)
- Generation time: ~10 minutes
- Results cached to disk: `backend/picks_cache.json`
- API response: Instant (reads from cache)

### 9-Phase Pipeline

#### Phase 0 — Outcome Resolution
- Compare previous predictions against actual forward returns (1-day / 63-day / 252-day)
- Update outcome logger database with direction hits (correct/incorrect)
- Feed data into IC engine for retraining

#### Phase 1 — Universe Screening
- Run full prediction engine on all Nifty 100 stocks
- Parallelised: 2 workers via `ThreadPoolExecutor`
- Returns raw factor scores for all stocks (enables cross-sectional z-scoring)

#### Phase 2 — Regime Detection
- KMeans clustering (4 clusters) on global macro features: VIX, S&P 500 return, crude, gold, USD/INR
- Classifies market into: **BULL_CALM**, **BULL_VOLATILE**, **BEAR_CALM**, **BEAR_PANIC**
- Returns regime label + weight multipliers for IC adjustment

#### Phase 3 — IC Weight Computation
- If < 60 outcome pairs: use academic prior weights
  - Short: tech=0.055, fund=0.018, sentiment=0.042, quality=0.032
- If ≥ 60 pairs: Bayesian shrinkage blend of live IC + prior:
  - `weight = live_weight × live_ic + (1 − live_weight) × prior`
- Apply regime multipliers: BULL boosts tech/sentiment; BEAR boosts fundamental/quality

#### Phase 4 — Z-Score Normalisation & Alpha
- Cross-sectional z-scoring: `z_i = (score_i − mean) / std`
- Combined alpha: `Σ ic_weight_k × z_k`
- Meta-model alpha (requires 180+ outcomes across 60 stocks × 3 horizons):
  - Inputs: tech_z, fund_z, sentiment_z, quality_z, combined_alpha, regime_id
  - Output: predicted return %

#### Phase 5 — Pick Selection
- Rank by alpha score (meta_alpha if available, else combined_alpha)
- Select top 5 **BUY** signals per horizon
- Minimum 1 pick per horizon

#### Phase 6 — Portfolio Optimisation
- Fetch 6-month daily returns for selected picks
- Covariance estimation: Ledoit-Wolf shrinkage at 25%
- Optimise: `max (alpha × w − λ × w^T Σ w)`
- Constraints: `Σw = 1.0`, `0 ≤ w_i ≤ 0.40` (max 40% per position)
- Risk aversion `λ`: doubled in BEAR_PANIC regime

#### Phase 7 — Logging
- Log to PostgreSQL (if `USE_POSTGRES=1`) or SQLite
- Stored fields: factor z-scores, combined_alpha, meta_alpha, signal, price, horizon, regime

#### Phase 8 — Weight Adaptation (background)
- Retrain IC engine with new outcomes
- Update meta-model if sufficient data
- Recalibrate regime clustering
- Runs in daemon thread (non-blocking)

---

## 14. Backtesting & Validation Engine

**File:** `backend/services/validation_engine.py`, `backend/services/backtester.py`

### Walk-Forward Methodology (No Look-Ahead Bias)

For each business day `t` in Nifty 100 history:
1. Fetch price data available **only before** time `t`
2. Compute prediction at `t` using that data
3. Measure actual forward return at `t + h` (h = 7 / 63 / 252 days)
4. Compare predicted signal vs actual direction

### Metrics Computed

| Metric | Definition |
|--------|-----------|
| **Hit Rate** | % of BUY/SELL calls that were directionally correct |
| **Avg Return on BUY** | Mean forward return when signal was BUY |
| **Sharpe Ratio** | `(avg_return − risk_free) / std_return` on BUY calls |
| **vs Benchmark** | Alpha over NIFTY 50 buy-and-hold |
| **Score Calibration** | Hit rate by score bucket (60–70, 70–80, 80–100) |

### Execution

- **Weekly:** Sunday 7:30 AM IST (via GitHub Actions)
- **Duration:** ~40 minutes (medium + long horizons)
- **Storage:** PostgreSQL `val_runs` + `val_signals` tables
- **Retention:** 365 days

### Indicative Score Calibration (Nifty 100, Medium-Term)

| Score Bucket | Hit Rate | Beat Benchmark | Avg Alpha |
|--------------|----------|---------------|-----------|
| 80–100 (Strong BUY) | ~68% | ~62% | +4.2% |
| 70–79 (BUY) | ~62% | ~55% | +2.8% |
| 60–69 (Moderate BUY) | ~58% | ~50% | +1.5% |
| 55–59 (HOLD) | ~52% | ~48% | +0.3% |

---

## 15. Crypto Prediction Module

**File:** `backend/services/crypto_engine.py`

### Supported Assets

BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT

### Signals Used (No Fundamentals)

| Signal | Weight |
|--------|--------|
| Technical indicators (full suite) | Primary |
| Fear & Greed proxy (30-day vol / 90-day vol) | Secondary |
| On-chain proxy (price-volume accumulation) | Secondary |
| News sentiment (Google News + CoinTelegraph) | Supporting |
| Macro sensitivity (BTC/ETH correlated to S&P, VIX) | Adjustment |

> Quality factors and dynamic fundamental weights are not applied — crypto lacks P/E, ROE, cash flow data. Momentum and sentiment dominate.

---

## 16. Screener & Universe Management

**File:** `backend/services/screener_service.py`, `screener_data.py`

### Screener Filters

| Filter | Type | Description |
|--------|------|-------------|
| Market | US / IN | Filter by market |
| Signal | BUY / HOLD / SELL | Filter by signal |
| Min Market Cap | ₹ Cr | Size filter |
| Max P/E | Ratio | Valuation filter |
| Min ROE | % | Profitability filter |
| Sector | IT / Banking / Pharma / etc. | Sector filter |

### Heatmap

- Groups stocks by sector
- Shows sector-level average return, top movers, stock count
- Colour-coded by performance (green/red intensity)

### Top Movers

- Fetched in real-time from yfinance
- Ranked by absolute % change
- Separate for US, India, Crypto

---

## 17. API Reference

### Prediction

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions/{symbol}` | GET | Get prediction (async, 202 while computing) |
| `/api/predictions/debug/state` | GET | Internal cache/thread state (debug only) |

**Query params:** `market` (US/IN/CRYPTO), `horizon` (short/medium/long)

### Daily Picks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/picks/daily` | GET | Today's cached picks (instant) |
| `/api/picks/generate` | POST | Trigger pick generation (secret-protected) |

### Screener

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/screener/filter` | GET | Filter universe by fundamentals |
| `/api/screener/heatmap` | GET | Sector heatmap |
| `/api/screener/top-movers` | GET | Top 10 movers by % change |
| `/api/screener/crypto-movers` | GET | Top 10 crypto movers |

### Backtest & Validation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/backtest/run` | POST | Single-stock walk-forward backtest |
| `/api/validation/run` | POST | Full universe validation (background) |
| `/api/validation/status` | GET | Validation progress |
| `/api/validation/results` | GET | Validation metrics by horizon |

### Infrastructure

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (keep-alive ping) |

---

## 18. Frontend Pages & Components

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Top movers (US/IN/Crypto), market status, search |
| Stock Detail | `/stock/:symbol` | Full prediction, trade levels, factor breakdown |
| Daily Picks | `/picks` | Today's top BUY ideas by horizon + portfolio weights |
| Screener | `/screener` | Filter and explore the universe |
| Backtest | `/backtest` | Single-stock historical test |
| Watchlist | `/watchlist` | Saved stocks with live prices and signals |
| Alerts | `/alerts` | Price and signal alerts |
| Portfolio | `/portfolio` | Holdings with BUY/HOLD/SELL per position |

### Key Components

| Component | Description |
|-----------|-------------|
| `FactorAttributionWaterfall` | Horizontal bar chart showing composite score decomposition; click any bar to see underlying metrics |
| `ConfidenceMeter` | Colour-coded progress bar (0–100) |
| `ConfidenceBreakdown` | SVG gauge + 5-component confidence bars with tooltips |
| `BullBearCase` | Generated analyst-style bull/bear thesis bullets |
| `TradingViewWidget` | Embedded TradingView advanced chart (visual only; not connected to prediction engine) |
| `IndexBar` | Live NIFTY 50, SENSEX, VIX ticker strip |
| `SignalBadge` | BUY / HOLD / SELL badge with colour coding |
| `ScoreHistoryChart` | Composite score trend over time |
| `NewsCard` | Sentiment-tagged news article card |

---

## 19. Infrastructure & Deployment

### Hosting

| Layer | Provider | Plan |
|-------|----------|------|
| Backend API | Render.com | Free tier (512 MB RAM, 0.1 vCPU) |
| Frontend | Vercel | Hobby |
| Database | PostgreSQL (Render) or SQLite (local) | — |
| Auth | Supabase | Free tier |

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | `production` / `development` | `development` |
| `DATABASE_URL` | PostgreSQL connection string | SQLite fallback |
| `USE_POSTGRES` | `1` = Postgres, `0` = SQLite | `0` |
| `PICKS_SECRET` | Secret header for `/api/picks/generate` | Required in prod |
| `PICKS_UNIVERSE_LIMIT` | Cap stock count (set < 25 on constrained hosts) | 98 (full Nifty 100) |

### Backend Startup Sequence

1. Uvicorn starts FastAPI app
2. CORS middleware enabled (frontend HTTPS origin)
3. Prediction router with daemon-thread async pattern initialised
4. Universe refresh (async, 30s delay)
5. Keepalive loop (14-minute interval)
6. Outcome resolver (6-hour interval)
7. Warmup loop — pre-computes RELIANCE:IN:medium and AAPL:US:medium (150s delay, 90s gap)

---

## 20. Automation Workflows

**Directory:** `.github/workflows/`

### daily_picks.yml — Daily Picks Generation

```
Schedule: 0 3 * * 1-5  (3:00 AM UTC = 8:30 AM IST, Mon–Fri)
Action:   POST /api/picks/generate with x-secret header
Purpose:  Wake Render + trigger 10-min pick generation
Result:   Picks ready by 9:00 AM IST
```

### weekly_validation.yml — Model Accuracy Validation

```
Schedule: 0 2 * * 0  (2:00 AM UTC = 7:30 AM IST, Sunday)
Steps:    1. Ping /health to wake Render
          2. POST /api/validation/run?horizon=medium → wait 15 min
          3. POST /api/validation/run?horizon=long   → wait 25 min
          4. GET /api/validation/results → print summary
Output:   BUY hit rate %, alpha %, Sharpe per horizon
```

### keep_alive.yml — Render Free Tier Keep-Alive

```
Schedule: */10 * * * *  (every 10 minutes, 24/7)
Action:   GET /health
Purpose:  Prevent Render free tier from spinning down
```

---

## 21. Factor Weights by Horizon

### Short-Term (1–5 days)

| Factor | Weight | Rationale |
|--------|--------|-----------|
| Technical | 70% | Momentum and price action dominate short windows |
| Fundamental | 15% | Slow-moving signal, limited short-term relevance |
| Sentiment | 15% | News-driven price moves matter in 1–5 days |
| Quality | Not applied | Computation overhead; not predictive short-term |

### Medium-Term (2–4 weeks)

| Factor | Weight | Rationale |
|--------|--------|-----------|
| Technical | 40% | Trend still matters but less dominant |
| Fundamental | 45% | Growth and valuation start to drive returns |
| Sentiment | 15% | News flow still relevant over multi-week horizon |
| Quality | Included in composite | Institutional signals begin to matter |

### Long-Term (3–6 months)

| Factor | Weight | Rationale |
|--------|--------|-----------|
| Technical | 15% | Mean reversion reduces technical edge |
| Fundamental | 75% | Business quality and valuation dominate |
| Sentiment | 10% | Structural, not ephemeral, news matters |
| Quality | Included, weighted heavily | Piotroski, ROIC, cashflow most predictive |

---

## 22. Key Design Principles

1. **No Look-Ahead Bias** — Backtester uses only data available at the prediction date. Forward returns computed strictly after prediction timestamp.

2. **Full Explainability** — Every signal has reasoning bullets, factor breakdown, and confidence components. Nothing is a black box.

3. **Honest Risk-Adjusted Returns** — Target prices are not inflated. Trade levels (entry, stop, target) are mutually consistent with the signal.

4. **Sector-Aware Scoring** — Different valuation thresholds for banks (ROE > 15%, P/B < 1.5), NBFCs, and IT companies vs industrials.

5. **Institutional-Grade Signals** — Piotroski F-Score, Sharpe Ratio, ROIC, IC engine, Ledoit-Wolf covariance optimisation — same tools used by quant funds.

6. **Data Resilience** — Three-layer fallback chain: yfinance → screener.in → BSE API. If news is unavailable, weights redistribute gracefully.

7. **Memory-Efficient** — Designed for Render's 512 MB free tier. Cache capped at 300 entries with LRU eviction. Concurrent predictions use daemon threads, not asyncio tasks.

8. **Self-Improving** — Outcome logger tracks every prediction. IC engine retrains weekly. Factor weights evolve as the model sees more real-world outcomes.

9. **Real-Time Ready** — 15-minute prediction cache, async background computation, React Query polling for live data.

10. **Investor Transparency** — Every number in the UI is traceable to a specific calculation in the codebase. This document is kept current with every code change.

---

*This document is a living record of StockSense. It is updated with every significant change to the product.*
