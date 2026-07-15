# StockSense360 — Complete Product & Technical Documentation

> **Live Document** — Updated automatically as the product evolves.  
> Last updated: 2026-07-11 (Session 11)

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
17. [Paper Trading Module](#17-paper-trading-module)
18a. [Reading the UI — Signal Colors & Common Jargon](#18a-reading-the-ui--signal-colors--common-jargon)
18b. [Multibagger Screen](#18b-multibagger-screen)
18. [Alerts System](#18-alerts-system)
19. [API Reference](#19-api-reference)
20. [Frontend Pages & Components](#20-frontend-pages--components)
21. [Infrastructure & Deployment](#21-infrastructure--deployment)
22. [Automation Workflows](#22-automation-workflows)
23. [Persistence & Data Durability](#23-persistence--data-durability)
24. [Factor Weights by Horizon](#24-factor-weights-by-horizon)
25. [Key Design Principles](#25-key-design-principles)
26. [Changelog](#26-changelog)

---

## 1. Product Overview

> **Operational-status authority:** This document is a product and technical reference. For live release state, validation gates, feature flags, scheduler state, and operational blockers, see [`Engineering-Handbook/Operations/Current-Release-Status.md`](Engineering-Handbook/Operations/Current-Release-Status.md).

**StockSense360** is an AI-powered stock prediction and portfolio intelligence platform built for Indian and US equity markets. It combines institutional-grade quantitative signals with a consumer-friendly interface to deliver actionable BUY / HOLD / SELL signals with full explainability.

### What StockSense360 Does

- Generates **BUY / HOLD / SELL signals** for Nifty 100, US large-cap, and top cryptocurrencies
- Delivers Daily Picks — up to 6 BUY ideas per horizon (short / medium / long), screened from the NSE and US universes. India generates once daily (designed schedule: ~2 AM IST). US generates in two stages: a Pre-Open **base** run (06:00 UTC / 10:00 AM Dubai / 11:30 AM IST) followed by a separate, lightweight **Premarket Review** targeting ~6:00 AM America/New_York (backend acceptance window 6:00-7:30 AM ET) — see Product Integrity #007/#008. Automated GitHub Actions triggering is active for both markets and has natural-run completion evidence for both (2026-07-14); scheduled-trigger **timing** has repeatedly fired hours later than its nominal cron time and remains a separate, open reliability concern. See the Current Release Status register (Release 12B) for live operational state and evidence.
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
| Hosting | Railway (backend), Vercel (frontend) |
| Automation | GitHub Actions (cron jobs) |

---

## 3. Data Sources

| Source | Data Provided | Frequency | Used For |
|--------|--------------|-----------|----------|
| **yfinance** | Price, OHLCV, P/E, ROE, FCF, beta, analyst targets | Real-time | All markets |
| **screener.in** | 10-year financials, ROCE, CAGR, promoter %, pledge % | Daily | India fundamentals |
| **BSE API** | Fundamentals for renamed / merged stocks | Daily | India fallback |
| **NSE FII/DII API** | Daily institutional flows (₹ Cr) | Daily | India quality signal |
| **NSE Pledge API** | Promoter pledge % (quarterly disclosure) | Quarterly | India risk signal |
| **Yahoo Finance RSS** | News headlines per symbol | Real-time | Sentiment |
| **Google News RSS** | `{symbol} stock India` search results | Real-time | Sentiment fallback |
| **Economic Times RSS** | India economy & market news | Real-time | India sentiment |
| **MoneyControl RSS** | Stock & sector news | Real-time | India sentiment |
| **yfinance macro** | S&P 500, VIX, Crude, Gold, USD/INR, Nifty IT/Bank | 15-min cache | Global macro |

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

| Composite Score | Signal | Score Band | Confidence Calculation |
|----------------|--------|------------|----------------------|
| ≥ 90 | **BUY** | Exceptional Opportunity | `(score − 60) / 40 × 100%` |
| ≥ 75 | **BUY** | Strong Buy Candidate | `(score − 60) / 40 × 100%` |
| ≥ 60 | **BUY** | Good Watchlist Stock | `(score − 60) / 40 × 100%` |
| 45 – 59 | **HOLD** | Neutral — Monitor | `50 − abs(score − 52) × 2` |
| < 45 | **SELL** | Avoid | `(45 − score) / 45 × 100%` |

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
- **Max size:** 300 entries (memory-safety cap)
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

A stock is flagged **REJECTED** before scoring if **any** of these apply:
- ROE < −10% (severely destroying shareholder value)
- Profit Margin < −15% (deeply loss-making)
- Non-positive Operating Cash Flow on medium/long horizons (core business not generating cash)
- D/E ratio > 500% (extreme leverage, non-financial sector only)

> Previously ROE AND margin both had to be negative simultaneously — this was too lenient and has been corrected.

### Scoring Architecture (Per-Category Budgets)

The fundamental score uses **six independent capped buckets**, each scored separately, then summed with a base of 50. This prevents any single dimension from dominating and ensures the final score has meaningful discrimination across the full 0–100 range.

| Bucket | Cap | What It Measures |
|--------|-----|-----------------|
| Valuation | ±15 | P/E, P/B ratios vs market-calibrated thresholds |
| Profitability | ±15 | ROE, ROCE, profit margins |
| Growth | ±15 | Revenue + earnings CAGR (counted once via longest available window) |
| Balance Sheet | ±10 | D/E, OCF quality, Altman Z-Score, Sloan accruals |
| Governance | ±10 | Promoter holding, FII/DII flows, promoter pledge |
| Banking | ±10 | Net NPA, NIM (fires only for banks/NBFCs) |

**Total possible range:** 50 ± 65 → clamped to [0, 100]

#### Valuation bucket (cap ±15)

| Metric | Threshold (India / US) | Points |
|--------|----------------------|--------|
| P/E | < 18 IN / < 15 US (cheap) | +8 |
| P/E | < 30 IN / < 25 US (fair) | +3 |
| P/E | > 55 IN / > 50 US (expensive) | −8 |
| P/B | < 2.5 IN / < 2.0 US | +4 |
| P/B | > 8.0 IN / > 6.0 US | −4 |

#### Profitability bucket (cap ±15)

| Metric | Threshold | Points |
|--------|-----------|--------|
| ROE | > 20% | +7 |
| ROE | 10–20% | +3 |
| ROE | < 0% | −7 |
| Profit Margin | > 20% | +5 |
| Profit Margin | < 0% | −5 |
| ROCE | > 20% | +6 |
| ROCE | 12–20% | +2 |
| ROCE | < 6% | −4 |

#### Growth bucket (cap ±15) — revenue and earnings each counted once

Revenue uses 3Y CAGR (screener.in) if available, else TTM YoY (yfinance):

| Metric | Threshold | Points |
|--------|-----------|--------|
| 3Y Revenue CAGR | > 15% | +7 |
| 3Y Revenue CAGR | 8–15% | +3 |
| 3Y Revenue CAGR | < 0% | −5 |
| TTM Revenue Growth (fallback) | > 20% | +7 |
| TTM Revenue Growth | 5–20% | +3 |
| TTM Revenue Growth | < −5% | −5 |

Earnings uses longest available: 5Y CAGR (long horizon) → 3Y CAGR → TTM EPS growth:

| Metric | Threshold | Points |
|--------|-----------|--------|
| 5Y Profit CAGR (long only) | > 18% | +6 |
| 3Y Profit CAGR | > 20% | +6 |
| 3Y Profit CAGR | > 10% | +3 |
| 3Y Profit CAGR | < −10% | −5 |
| TTM EPS Growth (fallback) | > 20% | +5 |
| TTM EPS Growth | < −10% | −5 |
| Quarterly PAT trend | Accelerating | +3 |
| Quarterly PAT trend | Decelerating | −3 |

#### Balance Sheet bucket (cap ±10)

| Metric | Threshold | Points |
|--------|-----------|--------|
| D/E | > 300% | −7 |
| D/E | 150–300% | −3 |
| D/E | < 50% | +3 |
| Operating CF (screener) | Negative | −5 |
| Operating CF 3Y growth | > 30% | +4 |
| Operating CF 3Y growth | Positive | +2 |
| Altman Z-Score | Safe zone (medium/long) | +3 |
| Altman Z-Score | Grey zone | −4 |
| Altman Z-Score | Distress zone | −8 |
| Sloan Accruals ratio | < −5% (cash-backed) | +3 |
| Sloan Accruals ratio | > 10% (manipulation risk) | −5 |

#### Governance bucket (cap ±10) — India only

| Metric | Threshold | Points |
|--------|-----------|--------|
| FII + DII combined | > 50% | +4 |
| FII + DII combined | 25–50% | +2 |
| DII quarterly trend | Up > 3% (MF accumulation) | +3 |
| DII quarterly trend | Down > 3% | −3 |
| FII quarterly trend | Up > 3% | +2 |
| FII quarterly trend | Down > 3% | −2 |
| Promoter holding | > 55% | +2 |
| Promoter holding | < 25% | −2 |
| Promoter trend | Up > 2% (insider buying) | +3 |
| Promoter trend | Down > 3% (insider selling) | −4 |
| Promoter pledge | > 50% | −8 |
| Promoter pledge | 25–50% | −5 |
| Promoter pledge | 10–25% | −2 |
| Promoter pledge | 0% | +2 |

#### Banking bucket (cap ±10) — fires only for banks/NBFCs

| Metric | Threshold | Points |
|--------|-----------|--------|
| Net NPA | > 3% | −7 |
| Net NPA | 1.5–3% | −3 |
| Net NPA | < 0.5% | +4 |
| NIM | > 4% | +4 |
| NIM | < 2% | −3 |

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

**Score conversion:** `sentiment_score = 50 + (bullish − bearish) / (bullish + bearish) × 50`

> Neutral articles are excluded from the denominator. Previously neutrals diluted the score — 5 bullish + 5 neutral incorrectly scored the same as 5 bullish + 5 bearish. Now only labelled articles (bullish + bearish) count.

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
| Regime Certainty | BULL/BEAR vs SIDEWAYS trend strength | 15% | 18.75% |
| Historical Factor Reliability | Live IC values (needs 60+ outcomes) | 20% | 0% → fallback 50 |

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
| **Medium** | Analyst target (70%) + price projection (30%); BUY floor: `max(blend, price×1.05)` |
| **Long** | `analyst_target × (1+EPS_growth)²`; BUY floor: `max(target, price×1.15)` |

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

- Triggered: India ~3:26 AM IST, US per its own cron (via GitHub Actions → POST `/api/picks/generate`) — a multi-hour runway before market open by design.
- Generation time: **~60-90 minutes** (Session 10 change, see below — was ~10-20 minutes when the deep-scored pool was 50 stocks; the runway before market open was confirmed to comfortably absorb this).
- Results cached to disk: `backend/picks_cache.json` (and `_us.json` for US)
- API response: Instant (reads from cache)

### 9-Phase Pipeline

#### Phase 0 — Outcome Resolution
- Compare previous predictions against actual forward returns (1-day / 63-day / 252-day)
- Update outcome logger database with direction hits (correct/incorrect)
- Feed data into IC engine for retraining

#### Phase 1 — Universe Screening (Large/Mid/Small-cap stratified, Session 10)
- **Universe source**: `stock_fundamentals_cache` — the same nightly-refreshed table (screener.in-sourced for India, yfinance-derived for US) the Multibagger Screen already maintains — not a live Yahoo screener call. Previously used `yf.screen()` sorted by market cap descending with a hard cutoff, which was structurally Large+Mid cap only in both markets (India's old 250-cap matched SEBI's own rank convention for Large+Mid almost exactly; US's old $2,000M floor excluded true small-caps by definition) — real Small Cap stocks never reached the pipeline, regardless of screener health.
- **Tiering**: India uses SEBI's rank convention (Large = NSE rank 1-100, Mid = 101-250, Small = 251+); US uses the standard value convention (Large > $10B, Mid $2B-$10B, Small < $2B). Both apply a small-cap junk floor (₹100 Cr / $100M) to exclude micro-caps/shells.
- **Stratified pool of ~400**, split roughly 40/30/30 Large/Mid/Small — replaces the previous 250 (India) / anchor-100 (US degraded case). If a tier has fewer eligible symbols than its quota on a given night (e.g. a thin small-cap data night), the pool is honestly smaller than 400 rather than silently backfilled from another tier.
- Falls back to the same curated static lists as before (NIFTY-100 for India, a 100-symbol mega-cap anchor for US) if the cache is empty/too thin that night — same safety net, just triggered by a cache-health check instead of a screener exception.
- Run full prediction engine on the resulting ~400-stock pool (was Nifty 100 / a 50-stock momentum-narrowed shortlist)
- Sequential (`max_workers=1`, unchanged) to avoid Yahoo Finance rate-limiting on the per-stock OHLCV/news calls this phase still makes — this is the main driver of the longer generation time above
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
- Select top **6** **BUY** signals per horizon (composite score ≥ 60) — the code has always done 6; this doc previously said 5 in error.
- **Short-term (Session 10 change)**: confidence-priority with fill-down, not tier-aware. Candidates with confidence **> 80%** are selected first (alpha-ordered among themselves); if fewer than 6 clear that bar, the remainder is filled from the next-highest-confidence BUY candidates that still pass the existing quality gate. A genuinely weak-conviction day can show fewer than 6 (even 0) short-term picks — deliberately not padded with lower-confidence noise to hit a count, matching this pipeline's existing "an empty/short picks list is a legitimate outcome" convention.
- **Medium/Long-term (Session 10 change)**: a Large/Mid/Small tier quota (2/2/2 of the final 6) is enforced so the list can't collapse back to all-large-cap even though large caps often score higher alpha on average — the explicit reason the Phase 1 stratification above exists. If a tier is short on qualifying candidates, the remaining slots are topped up from the next-best alpha across any tier so the list still reaches 6 when the data supports it.
- Minimum 1 pick per horizon (medium/long only — short-term can legitimately show 0, see above)
- Empty picks from a prior run (0 BUY signals) are NOT treated as "complete" — startup catch-up will retry on next deploy

#### Phase 6 — Portfolio Optimisation
- Fetch 6-month daily returns for selected picks
- Covariance estimation: Ledoit-Wolf shrinkage at 25%
- Optimise: `max (alpha × w − λ × w^T Σ w)` via SLSQP
- Constraints: `Σw = 1.0`, `0 ≤ w_i ≤ 0.40` (max 40% per position)
- Risk aversion `λ`: doubled in BEAR_PANIC regime
- Fallback (if scipy unavailable): iterative alpha-proportional weights that correctly enforce the 40% cap

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

### Walk-Forward Methodology

For each business day `t` in Nifty 100 history:
1. Fetch price data available **only before** time `t`
2. Compute prediction at `t` using that data
3. Measure actual forward return at `t + h` (h = 7 / 63 / 252 days)
4. Compare predicted signal vs actual direction

> Backtest indicators are recomputed on a rolling historical window (`df.iloc[:i+1]`) at each signal date, preventing future price or volume data from leaking into indicator calculations at time *t*.

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

### Forward Return Windows

| Horizon | Forward Return Used | Outcome Resolution Wait |
|---------|--------------------|-----------------------|
| Short | return_5d (5 trading days) | 3 calendar days |
| Medium | return_20d (20 trading days ≈ 1 month) | 30 calendar days |
| Long | return_60d (60 trading days ≈ 3 months) | 90 calendar days |

> Partial returns are never logged — the outcome logger returns None if fewer than the required trading days have elapsed. This prevents truncated returns from contaminating IC training data.

### Validation BUY Threshold

Validation uses the same threshold as the live prediction engine: **composite ≥ 60 = BUY** for all horizons. Previously the thresholds were mismatched (validation used 65, live used 60), making validation metrics unmeasurable against the actual model.

### Indicative Score Calibration (Nifty 100, Medium-Term)

| Score Bucket | Hit Rate | Beat Benchmark | Avg Alpha |
|--------------|----------|---------------|-----------|
| 80–100 (Exceptional/Strong BUY) | ~68% | ~62% | +4.2% |
| 75–79 (Strong BUY) | ~62% | ~55% | +2.8% |
| 60–74 (Good Watchlist BUY) | ~58% | ~50% | +1.5% |
| 45–59 (HOLD) | ~52% | ~48% | +0.3% |

> Note: These figures reflect the post-Session 4 thresholds. Historical validation data accumulated under the old 70-threshold is being re-calibrated.

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

- Groups stocks by sector; sorted by sector avg change% (best sectors on top)
- **India: 25 sectors** — Banking, IT, Auto, Pharma, Energy, FMCG, Finance, Healthcare, Insurance, Chemicals, Cement, Metal & Mining, Defence, Realty, Telecom, Consumer Disc, Hotels & Travel, Food & Beverage, Media & Entmt, Textiles, Agro & Chemicals, Logistics, Paints, Infra, Capital Goods, Power, EV & New Energy
- **US: 29 sectors** — Mega Cap Tech, Semiconductors, Cloud & SaaS, Cybersecurity, Fintech, Finance, Insurance, Healthcare, Biotech, Med Devices, Energy, Clean Energy, EV, Consumer Disc, Consumer Stap, E-commerce, Social Media, Streaming & Media, Gaming, Aerospace & Defence, Industrials, Airlines, Cruise & Hotels, Restaurants, Retail, Telecom, Utilities, Realty, Materials, Crypto & Blockchain
- Up to **15 stocks per sector** (MAX_STOCKS = 15)
- Primary data source for India sectors: **NSE sector index APIs** (`SECTOR_TO_NSE_INDEX` mapping); Yahoo Finance used as fallback for symbols not in NSE indices and as primary source for all US sectors
- Grey tiles = symbol has no live data; all 353 India symbols audited and bad tickers corrected (Session 5)
- Colour-coded by performance (green/red intensity); loading status badge (Fetching / Refreshing / Live)

### Top Movers

- Fetched in real-time from yfinance
- Ranked by absolute % change
- Separate for US, India, Crypto

---

## 17. Paper Trading Module

**File:** `backend/api/routers/paper_trading.py`, `frontend/src/app/paper-trading/page.tsx`

A simulated trading environment where users can test stock calls without real money. All trades persist in PostgreSQL and survive Railway restarts.

### Database Schema

```sql
paper_portfolio (user_id TEXT PK, session_id TEXT, cash NUMERIC, updated_at TIMESTAMPTZ)
paper_trades    (id SERIAL PK, user_id TEXT, session_id TEXT, symbol TEXT, market TEXT,
                 quantity INT, entry_price NUMERIC, exit_price NUMERIC,
                 stop_loss NUMERIC, target_price NUMERIC, status TEXT,
                 signal TEXT, horizon TEXT, opened_at TIMESTAMPTZ, closed_at TIMESTAMPTZ)
```

### User Model

- Trades are scoped to **Supabase `user_id`** (stable UUID from `useAuth().user.id`)
- No more session_id / localStorage dependency — trades persist across all browsers and devices
- Starting virtual cash: **₹1,00,000 / $10,000** (depending on market)
- All positions are long only (no shorting)

### Trade Lifecycle

```
Open Trade  → entry_price captured at trade time, keyed to user_id
Live Price  → fetched real-time via yfinance / NSE
Unrealised P&L = (live_price − entry_price) × quantity
Close Trade → exit_price set, status = 'CLOSED'
Realised P&L = (exit_price − entry_price) × quantity
```

### Stop Loss & Target Price

- Optionally set per trade via inline edit (✎ icon)
- ATR-based defaults pre-filled when placing a trade from the stock detail page
- Both values always shown per position with % from entry
- UI highlights rows where price is within 2% of stop (yellow) or target (green)
- The Buy button is **not blocked** by AI prediction loading — trade is placeable immediately; stop loss/target suggestions update in the background

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/paper-trading/portfolio` | GET | Portfolio summary (pass `user_id` as query param) |
| `/api/paper-trading/buy` | POST | Open a new position (body: `user_id`, symbol, market, qty, price, …) |
| `/api/paper-trading/sell/{trade_id}` | POST | Close a position (body: `user_id`, price) |
| `/api/paper-trading/trade/{trade_id}` | PATCH | Edit stop_loss / target_price (body: `user_id`, …) |
| `/api/paper-trading/reset` | POST | Reset portfolio to starting cash (query: `user_id`) |

### Performance notes (Session 11)

- **Postgres connection pooling.** `_conn()` previously opened a brand-new `psycopg.connect()` (a full TCP+TLS handshake) on every single call, and most handlers (`get_portfolio` in particular) called it twice per request. Now backed by a process-wide `psycopg_pool.ConnectionPool` — `psycopg[pool]` was already a declared dependency, just unused until this fix.
- **`OpenTradeRow` no longer fetches its own quote.** The page already fetched a staggered batch of live quotes per open trade for action-queue sorting; each row *also* ran its own independent, un-staggered `useQuery` for the identical endpoint. With 30-40+ open positions common, that meant every position fired two separate requests to the same quote endpoint on mount. The row now receives its quote as a prop from the page's already-staggered fetch instead.
- **`useMarketOpen`/`usePrefersReducedMotion` lifted to the page.** Both were previously called once *per open trade row* despite returning the identical value for every row (Paper Trading only ever shows one market's positions at a time; reduced-motion is a single OS-level setting) — 40+ redundant `setInterval(30s)` timers and `matchMedia` listeners, each of which also has to be torn down on unmount. Computed once in `PaperTradingPage` and passed down.
- **Trade History code-split.** `ClosedTradeHorizonBlock`/`ClosedTradeRow` (the below-the-fold closed-trade history section, ~260 of this page's 1483 lines — by far the largest client page in the app) moved to `frontend/src/components/PaperTradeHistoryBlock.tsx`, loaded via `next/dynamic` so it ships as its own chunk instead of bloating the tab's initial JS payload.
- Net effect: Paper Trading was the one tab that felt noticeably slower to navigate into (and out of) than every other tab — traced to the combination above (duplicate per-row network/render work on the way in, 80+ redundant cleanup functions on the way out) rather than any single cause.

---

## 18. Portfolio Tracker

**File:** `backend/api/routers/portfolio.py`, `frontend/src/app/portfolio/page.tsx`

Manually-tracked real (or planned) holdings with live P&L — distinct from Paper Trading's AI-signal-driven simulated trades. Added in Session 8 (2026-06-23); previously stored entirely in the browser's `localStorage` with no cross-device sync, migrated to Postgres so a holding added on one device is visible on any other device for the same account. `localStorage` is now only a fast-access cache / offline fallback; on first load after the migration shipped, any pre-existing local-only holdings are pushed up to the server automatically.

### Database Schema

```sql
portfolio_holdings (id TEXT PK, user_id TEXT, symbol TEXT, market TEXT CHECK('IN','US'),
                     qty NUMERIC, avg_price NUMERIC, created_at TIMESTAMPTZ)
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/portfolio/{user_id}` | GET | All holdings for user |
| `/api/portfolio/{user_id}` | POST | Add a holding (body: `symbol`, `market`, `qty`, `avg_price`) |
| `/api/portfolio/{user_id}/{holding_id}` | PATCH | Edit qty / avg_price |
| `/api/portfolio/{user_id}/{holding_id}` | DELETE | Remove holding |
| `/api/stocks/sectors` | GET | Batch sector lookup (query: `symbols` comma-separated, `market`) — see "Sector allocation" below. Lives in the Stocks router, not Portfolio's own, since it's a general-purpose lightweight lookup, not portfolio-specific data. |

### Frontend Behavior

- IN and US holdings render in separate sections (never mixed — ₹ and $ totals can't be summed into one number).
- Inline edit (pencil icon) for Qty/Avg Buy, confirmed via checkmark or Enter, cancelled via X or Escape.
- Delete/edit await the backend response before updating local state — closes the same "resurrection" race class found in Alerts (a failed request used to leave the row alive server-side while the UI showed it changed/gone, with the next page load silently reverting it).
- Symbol entry uses the shared `StockSymbolField` predictive-search component (Session 8) instead of a bare text input.
- **Day's P&L** (Session 10): a separate amount + % column alongside the existing overall P&L, reusing the `change`/`change_pct` already returned by each holding's quote fetch — no additional API calls.
- **Portfolio Allocation chart** (`PortfolioAllocationChart.tsx`): a By Sector / By Stock toggle, lifted into `portfolio/page.tsx` (Session 10) so the same toggle also drives the holdings table's sector grouping instead of a second, separate "Group by Sector" button duplicating the same choice. Both views sort slices descending by value.
- **Sector allocation data source (Session 11 — reworked)**: sector used to come from `quality_factors.sector` on the per-holding AI signal fetch (`fetchSignalSummary`), meaning sector allocation was gated on the same staggered, potentially slow-on-a-cold-cache prediction computation that drives the Signal badge — a portfolio's allocation chart could sit on a misleadingly complete-looking bar for as long as that took. Sector now comes from `fetchSectorsBatch()` → `GET /api/stocks/sectors`, one lightweight batch request per market for the *entire* holdings list (not staggered, not per-holding), reading only the nightly-refreshed `stock_fundamentals_cache` table — the same cache Multibagger's screens already use. No prediction/scoring is invoked to learn a holding's sector.
- **Unresolved-vs-"Other" distinction (Session 11)**: a holding whose sector lookup hasn't resolved yet is never folded into "Other" (which now means only "resolved, and genuinely has no sector") nor rendered as a fake chart slice. It shows as a separate, visually muted "Resolving sector…" state — in the allocation chart as a line below the real slices, and in the grouped holdings table as its own bottom-most group heading (no percentage-of-portfolio figure, since that would misread as a real allocation number).
- **Sector group headings (Session 11)**: each resolved sector group's heading now shows holding count, total value, and % of the selected market's total portfolio value, e.g. `IT · 2 holdings · ₹4,500 · 50.0%`.
- **Holdings table totals footer (Session 11)**: a bold "Total" row at the bottom of the table (exactly one, in both grouped and ungrouped mode) showing Invested, Current Value, Day's P&L, and P&L for the currently displayed market only. Invested always sums every row (qty/avg price are known at add-time, independent of any live quote); Current Value/Day's P&L/P&L each sum only rows that have actually resolved a live price — a still-loading holding is left out of the sum rather than silently contributing a fabricated `0`, and the footer shows a small "· partial while prices load" note whenever any row hasn't resolved yet.
- **"Value" column renamed to "Current Value" (Session 11)** for clarity against the new Invested/Current Value/Day's P&L/P&L footer totals.
- Holdings table has a persistently-visible thin scrollbar on its horizontal scroll container (fixes a real mobile overflow bug where the table didn't fit the viewport at all).

---

## 18a. Reading the UI — Signal Colors & Common Jargon

A reference for what the colors, badges, and terms scattered across Portfolio, Paper Trading, Daily Picks, and Multibagger actually mean. Source of truth: `frontend/src/components/SignalBadge.tsx`.

### Signal badge colors

| Badge | Color | Meaning |
|---|---|---|
| **BUY** | Green | Confidence ≥ 60% — strong conviction |
| **BUY** | Gold/yellow (filled) | Confidence 45–59% — moderate conviction |
| **BUY** | Gray (outlined) | Confidence < 45% — technically a BUY call, but weak conviction |
| **HOLD** | Amber/gold | Composite score landed in the neutral 45–59 band — model found no clear edge either way |
| **REJECTED** | Amber/gold (same color as HOLD, different label) | The model never scored this stock at all — a hard quality gate rejected it before scoring, typically for severe fundamental red flags or data quality issues. Different from HOLD: HOLD means "scored and neutral," REJECTED means "refused to score." Check that stock's `rejection_reasons` for specifics. |
| **SELL** | Red | Composite score below the SELL threshold |
| **—** (dash) | — | Not "no signal exists" — the row's data is still queued behind other rows in a staggered batch (added to avoid overwhelming the browser's per-origin connection limit on pages with many holdings/trades). It resolves to a real badge once its turn comes up; if it never resolves after a minute or more, that's worth reporting as a bug. |

Color tokens: `bull` = green (`#22c55e`), `bear` = red (`#ef4444`), `neutral` = amber (`#f59e0b`). BUY is the only signal with confidence-graded shading; HOLD/REJECTED/SELL are always a single fixed shade regardless of confidence.

### Other terms that show up across the app

| Term | Where seen | Meaning |
|---|---|---|
| **Confidence %** (= Conviction) | Signal badges, prediction detail | How sure the model is about its own call — a measure of conviction, not a probability of being "right." Low confidence ≠ wrong, it means the underlying signals were mixed/weak. "Confidence" and "Conviction" are the exact same single number (`prediction.confidence`) — there is no separate conviction calculation anywhere in the backend; both words were used interchangeably in different UI spots before being standardized to "Confidence" everywhere. |
| **Invested** | Portfolio, Paper Trade | `Qty × Avg Buy Price` (or entry price for paper trades) — what you actually put in, unaffected by current price moves. |
| **Unr. P&L** | Paper Trade | "Unrealized P&L" — the gain/loss on an open position if you closed it right now at the current market price. Becomes "Realized P&L" only after you actually close the trade. |
| **Near stop loss** | Paper Trade | The current price has moved within a small buffer of the stop-loss price — a warning to consider closing, not an automatic close (this app has no real broker execution; stop-loss/target are advisory levels you act on manually). |
| **Mkt Price (last close when closed)** | Paper Trade | For OPEN trades this is the live price; for CLOSED trades it's frozen at whatever the price was at the last market close before/at closing, since a closed position has no further "live" price to track. |
| **Target Price** | Predictions, Paper Trade | The model's projected price for the selected horizon if the thesis plays out — not a guarantee, and not the same as the stop-loss. |
| **Horizon (short/medium/long)** | Everywhere | short = 1–10 days, medium = 1–3 months, long = 6 months–3 years. Each horizon re-weights the same underlying factors differently (see §9 Dynamic Weights) — the same stock can legitimately show different signals across horizons at the same time. |
| **Composite Score** | Prediction detail, score history | The single 0–100 number all the BUY/HOLD/SELL thresholds are based on — a weighted blend of technical, fundamental, sentiment, macro, and quality factors (see §4). |
| **Verdict (Multibagger)** | Multibagger screen | `elite_strong_buy` / `strong_buy` / `watchlist` / `watch` / `avoid` — a checklist-based pass/fail rating distinct from the BUY/HOLD/SELL prediction signal; a stock can be a Multibagger "watchlist" candidate while simultaneously showing a HOLD prediction signal, since they're answering different questions (long-term quality checklist vs. current-horizon timing call). `elite_strong_buy` is a stricter tier requiring ROCE>15%, D/E<50%, OCF>0, and sales growth>10% to all individually pass, not just a high overall score. `watch` means exactly one Anti-Loss red flag was found (two or more forces `avoid` regardless of score). |
| **Shortlisted (Multibagger)** | Multibagger screen | The top ~20% scorers within a screen, excluding any stock with an `avoid` verdict outright regardless of its raw score — a relative ranking marker, not a guarantee. |
| **REJECTED reasons** | Multibagger, Predictions | A specific list of which hard-gate checks failed (e.g. promoter pledge too high, negative equity) — click through if a stock you expected to see is missing or rejected. |
| **History tab** | Stock detail page | A day-by-day chart of this stock's AI score over time — "Composite Score" shows the single overall number trending; "Factor Breakdown" splits it into technical/fundamental/sentiment/quality so you can see which ingredient moved. Has its own Short/Medium/Long selector, separate from the main horizon tabs above it. **Data points only get added on a day this stock happens to be one of the ~400 candidates the nightly Daily Picks job deep-scores for that horizon** (Session 10: raised from ~50 as part of the Large/Mid/Small-cap stratification fix) — not the full universe, and not every day. A near-empty chart (one dot, or "No history yet") is normal and expected for most stocks, especially soon after this feature launched — it's not broken, it just hasn't accumulated history yet. |
| **Allocation** | Daily Picks | The suggested portfolio weight if buying *all* of that day's picks together as one basket — a separate mean-variance optimization step (Ledoit-Wolf shrinkage covariance, max 40% per position) that runs after the Top 6 are already selected. It answers "where should the marginal rupee go within this basket," not "how strong is this signal." **0% allocation does not mean the BUY call is weak** — it means this stock's predicted-return-to-risk contribution was crowded out by the other picks in that day's specific list (often the lowest-ranked of the 6, or highly correlated with a stronger pick already at its 40% cap). The Signal/Confidence shown on the same card is computed completely independently and is unaffected by this number. |

---

## 18b. Multibagger Screen

**Files:** `backend/api/routers/multibagger.py`, `backend/services/fundamentals_cache.py`, `backend/services/multibagger_scorecard.py`, `frontend/src/app/multibagger/page.tsx`

**First dedicated section for this feature** — previously only glossary mentions existed in §18a (Verdict, Shortlisted, REJECTED reasons rows), flagged as owed follow-up in Session 10 and written here.

### What it is

Three independent, hard-filter SQL screens run against `stock_fundamentals_cache` — a Postgres table refreshed nightly (screener.in for India via `fundamentals_refresh.py`, yfinance-derived for US via `us_fundamentals_refresh.py`; the same table Daily Picks' universe stratification now also reads from, see §13). Deliberately **not** merged into one screen ("combining loose + strict criteria into one screen produces zero/over-expensive results" — the code's own stated design principle) and **not** the AI's ML-weighted composite score used elsewhere in the app — this is a separate, fully transparent, rule-based checklist.

### The three screens

| Screen | Intent | Key IN thresholds | Key US thresholds |
|---|---|---|---|
| **Quality Compounders** | Core portfolio — stable, proven, suitable for 5-10 year holding | Market cap >₹2,000 Cr, avg ROE 5Y >18%, ROCE >15%, D/E <50%, promoter pledge <1% (`COALESCE`d to 0 when unreported — see the Session 10 fix below), promoter holding >35%, sales/profit growth 5Y >10%, P/E <35, EV/EBITDA <20, positive operating cash flow | Same shape, substitutes 3Y growth for 5Y (yfinance's free tier caps annual financials at 4 years, so a true 5Y figure isn't computable for US) |
| **Multibagger Discovery** | Future compounders pipeline — midcaps and emerging smallcaps with accelerating growth, looser on financial history by design | Market cap ₹300 Cr–₹20,000 Cr, sales/profit growth 3Y >15%, ROCE >12%, D/E <100%, pledge <2%, price-to-sales <5, P/E <50 | Same, using `market_cap_usd_m` |
| **10-Bagger Early Detection** | Pre-compounder screen — still messy, but improving fast; catches turnarounds and niche manufacturers before they're obvious | Market cap ₹300 Cr–₹15,000 Cr, sales/profit growth 3Y >20%, ROCE >10%, ROE >8%, D/E <100%, interest coverage >2×, pledge <2%, price-to-sales <4, P/E <60, OPM >8% | Same shape, no pledge check (no "promoter" concept in US filings) |

**A stock must pass every condition in a screen to appear at all** — a hard AND-filter, not a scored/ranked cutoff. `GET /api/multibagger/screen?screen=<name>&market=<IN|US>` runs instant SQL against the cache (no live scraping per request); `status="ok"` with `count=0` is a genuine, successfully-evaluated zero-result day, distinct from `status="unavailable"` (a computation failure, detail never exposed to the client).

### The Session 10 pledge-NULL fix, and why it mattered

Screener.in only renders a "Pledged percentage" row when a company actually has non-zero promoter pledge — a genuinely clean (no-pledge) company has this field `NULL` in the cache, not `0`. Every screen's raw `promoter_pledge_pct < N` condition failed on that `NULL` (SQL comparisons against `NULL` are always false), which meant **every India screen was excluding almost every clean company outright** — the opposite of the condition's intent — and all three screens returned zero results regardless of any other threshold. Fixed by wrapping every pledge condition in `COALESCE(promoter_pledge_pct, 0) < N`, so a missing pledge value is now correctly treated as clean. Verified live: `quality_compounder` 0→52, `multibagger_discovery` 0→167, `tenbagger_early` 0→73.

### The Scorecard — a second, independent layer on top of the raw screen

Every row that passes a screen additionally gets a transparent checklist score (`multibagger_scorecard.py`'s `compute_scorecard`) — 10 checks for US, 12 for India (2 extra: growth accelerating 3Y CAGR > 5Y CAGR, no promoter pledge — both need data structurally unavailable for US). Categories: Business Quality, Growth, Financial Safety, Valuation, plus IN-only Growth Trajectory/Governance checks.

- **Anti-Loss red flags** — a hard override, independent of the raw score: ROE well below its 5Y average, negative 3Y profit growth, negative operating cash flow, promoter pledge above the red-flag threshold, or high leverage. **Any one red flag caps the verdict at `watch`; two or more force `avoid`, regardless of how high the checklist score is otherwise.**
- **Verdict thresholds** scale proportionally to each market's check count (score ≥83% → `strong_buy`, ≥58% → `watchlist`, below → `avoid`) so a 10-check US scorecard and a 12-check IN one apply the same relative bar, not the same absolute one.
- **`elite_strong_buy`** — a stricter, all-must-pass promotion on top of an already-`strong_buy`/`watchlist` verdict: ROCE >15% AND D/E <50% AND positive operating cash flow AND sales growth >10%, every one individually, not just a high overall percentage. Never overrides `avoid`/`watch` — the Anti-Loss red-flag ceiling is a hard cap by design.
- **Business Quality Engine confirmation** — a second, independent promotion path: if the separate Business Quality Engine (`backend/services/business_quality_engine.py` — Epic 001, see `Documentation/Engineering-Handbook/EPICS/EPIC-001-Business-Quality-Intelligence-Closure.md`) already grades the stock `Quality Compounder` style at a genuinely strong score, that also promotes to `elite_strong_buy`. Two different pieces of independent positive evidence, either sufficient on its own.
- **Shortlisted** — the top ~20% of each screen's results by score (ties broken by fewer red flags, so a clean stock never loses a tie to an equally-scored flagged one), **excluding any `avoid`-verdict stock outright regardless of raw score** — prevents the direct self-contradiction of a "Shortlisted" flame icon next to an "Avoid" badge on the same row.

### Frontend behavior

- Screen selector (3 cards) + a results table sorted by the backend's own ranking (`profit_growth_3y_pct DESC` for Discovery/10-Bagger, `roe_5y_pct DESC` for Quality Compounders) with a visual divider between the shortlisted top ~20% and the rest.
- Click any row to expand the full checklist (✓/✗ per check) and any Anti-Loss red flags inline.
- Metrics shown differ by market (e.g. Promoter Holding for IN vs. Insider Holding for US) since those concepts aren't equivalent.
- `IN/US` toggle uses `placeholderData: keepPreviousData` so switching markets doesn't collapse the header mid-fetch (same fix applied to Daily Picks for the identical pattern).
- "Last refreshed" is shown in the viewer's own local timezone (detected via `Intl.DateTimeFormat`), not a single hardcoded market timezone.

---

## 19. Alerts System

**File:** `backend/api/routers/alerts.py`, `backend/services/price_alert_notifier.py`, `frontend/src/app/alerts/page.tsx`

Price-level alerts that trigger when a stock crosses a target price. All alerts persist in PostgreSQL.

### Database Schema

```sql
price_alerts (id TEXT PK, user_id TEXT, symbol TEXT, market TEXT, email TEXT,
              target_price NUMERIC, direction TEXT CHECK('above','below'),
              triggered BOOL DEFAULT FALSE, created_at TIMESTAMPTZ, triggered_at TIMESTAMPTZ)
```

### Alert Check Logic

- **Client-side:** frontend polls live quote every 5 seconds while the page is open; on trigger, PATCHes the alert to `triggered = true`. Stops working the moment the tab is closed/backgrounded.
- **Server-side (Session 8):** `_price_alerts_check_loop` in `main.py` runs `price_alert_notifier.check_and_notify()` every 90 seconds — scans all non-triggered alerts with an `email` on file, fetches the live quote, and emails the owner (same Resend account as invites/paper-trade notifications) once the threshold is crossed, marking it triggered server-side. No time-based cooldown needed — `triggered` itself is the dedup, since the query only selects non-triggered rows. Kill switch: set `PRICE_ALERTS_ENFORCEMENT=0` to disable just this background check without a code change; client-side polling is unaffected either way.
- Triggered alerts shown with timestamp; can be reset (re-arms for exactly one more notification) or deleted.
- Delete/reset await the backend response before updating local UI state (Session 8 fix) — previously a failed request could leave the row alive server-side while the screen showed it changed, with the next page load silently reverting it.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/alerts/{user_id}` | GET | All alerts for user |
| `/api/alerts/{user_id}` | POST | Create new alert |
| `/api/alerts/{user_id}/{alert_id}` | PATCH | Update (reset triggered, edit price) |
| `/api/alerts/{user_id}/{alert_id}` | DELETE | Remove alert |

---

## 20. API Reference

### Prediction

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/predictions/{symbol}` | GET | Get prediction (async, 202 while computing) |
| `/api/predictions/debug/state` | GET | Protected diagnostic endpoint — requires a matching `X-Secret` header for the configured non-empty `PICKS_SECRET`; returns aggregate operational counts only, with no raw cache or in-flight identifiers. See the Current Release Status register for rollout state. |

**Query params:** `market` (US/IN/CRYPTO), `horizon` (short/medium/long)

### Daily Picks

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/picks/daily` | GET | Today's cached picks (instant) |
| `/api/picks/generate` | POST | Trigger pick generation (secret-protected) |
| `/api/picks/performance` | GET | Live P&L of past picks vs benchmark |

**Query params for performance:** `horizon` (short/medium/long), `window_days` (default 90)

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

### Watchlist

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/watchlist/{user_id}` | GET | All saved stocks |
| `/api/watchlist/{user_id}` | POST | Add stock |
| `/api/watchlist/{user_id}/{symbol}` | DELETE | Remove stock |

### Alerts

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/alerts/{user_id}` | GET | All price alerts |
| `/api/alerts/{user_id}` | POST | Create alert |
| `/api/alerts/{user_id}/{alert_id}` | PATCH | Update alert (reset / edit) |
| `/api/alerts/{user_id}/{alert_id}` | DELETE | Delete alert |

### Paper Trading

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/paper-trading/portfolio` | GET | Portfolio summary (`?user_id=`) |
| `/api/paper-trading/buy` | POST | Open position |
| `/api/paper-trading/sell/{trade_id}` | POST | Close position |
| `/api/paper-trading/trade/{trade_id}` | PATCH | Edit SL / target |
| `/api/paper-trading/reset` | POST | Reset portfolio (`?user_id=`) |

### Infrastructure

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (keep-alive ping) |

---

## 21. Frontend Pages & Components

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Landing | `/` (unauthenticated) | Public marketing page — features, how-it-works, CTA adapts to login state |
| Dashboard | `/dashboard` | Movers (US/IN/Crypto), market status, live index bar with loading badge |
| Stock Detail | `/stock/:symbol` | Prediction, trade levels, factor breakdown, news, chart |
| Daily Picks | `/picks` | Top BUY ideas by horizon, portfolio weights, trust layer |
| Screener | `/screener` | Filter and explore the universe |
| Backtest | `/backtest` | Single-stock historical walk-forward test |
| Heatmap | `/heatmap` | Sector colour-coded snapshot (IN / US) with loading badge |
| Watchlist | `/watchlist` | Saved stocks with live prices and change% — user-scoped |
| Alerts | `/alerts` | Price alerts with live trigger detection — user-scoped |
| Portfolio | `/portfolio` | Holdings with BUY/HOLD/SELL per position |
| Paper Trade | `/paper-trading` | Simulated trading — open/close positions, P&L — user-scoped |
| Validation | `/validation` | Hit rate, Sharpe, alpha vs benchmark |

### Key Components

| Component | Description |
|-----------|-------------|
| `FactorAttributionWaterfall` | Score decomposition bar chart; click to drill down |
| `ConfidenceMeter` | Colour-coded progress bar (0–100) |
| `ConfidenceBreakdown` | SVG gauge + 5 confidence bars with tooltips |
| `BullBearCase` | Analyst-style bull/bear thesis bullets |
| `TradingViewWidget` | Embedded chart (visual only; not wired to engine) |
| `IndexBar` | NIFTY 50, SENSEX, VIX live strip |
| `SignalBadge` | BUY / HOLD / SELL badge with colour |
| `ScoreHistoryChart` | Composite score trend over time |
| `NewsCard` | Sentiment-tagged news card |
| `BacktestPanel` | Walk-forward results per horizon on Picks page |
| `LivePerformanceTracker` | Per-pick P&L — entry, return%, alpha vs Nifty |
| `PaperTradeModal` | Trade form (qty, horizon, pre-filled SL/target) |

### Daily Picks Trust Layer

The Picks page has a collapsible **"Show Real Accuracy"** panel with three layers:

1. **Backtest results** — real walk-forward hit rate, avg return, Sharpe, alpha vs Nifty per horizon
2. **Confidence calibration table** — empirical hit rate per score band (60–65, 65–70, 70–75, 75–80, 80–85, 85–91) so users can see if higher confidence = higher win rate
3. **Live P&L tracker** — every past daily pick with entry price, current return%, and alpha vs benchmark

### Pick Card UI

- Rank badge (#1–#5)
- Score band label (STRONG BUY / BUY / HOLD) with colour
- Sector tag
- Top 3 signals inline (▲ BULLISH, ▼ BEARISH, → NEUTRAL) without needing to expand
- Compact market regime bar

---

## 22. Infrastructure & Deployment

### Hosting

| Layer | Provider | Plan |
|-------|----------|------|
| Backend API | Railway | Hobby ($5/month, always-on, no cold starts) |
| Frontend | Vercel | Hobby |
| Database | PostgreSQL (Railway) or SQLite (local) | — |
| Auth | Supabase | Free tier |

> **Migrated from Render to Railway** (Session 5) — Railway Hobby plan eliminates the free-tier cold-start problem (30-second spin-up delays) and provides a persistent, always-on server.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | `production` / `development` | `development` |
| `DATABASE_URL` | PostgreSQL connection string | SQLite fallback |
| `USE_POSTGRES` | `1` = Postgres, `0` = SQLite | `0` |
| `PICKS_SECRET` | Secret header for `/api/picks/generate` | Required in prod |
| `PICKS_UNIVERSE_LIMIT` | Cap stock count for picks run | 25 |
| `PICKS_CANDIDATES` | Top N from Phase-0 momentum screen for deep prediction | **751** (set in Railway) |
| `SCREEN_BATCH_SIZE` | NSE bulk download batch size (memory safety) | 300 |
| `MIN_MCAP_CR` | Min market cap in ₹ Cr for NSE universe | 100 |
| `SCREENER_EMAIL` | screener.in login (Indian fundamentals) | Required |
| `SCREENER_PASSWORD` | screener.in password | Required |
| `FRONTEND_URL` | Vercel frontend URL for CORS | Must be set in prod |

### Backend Startup Sequence

1. Uvicorn starts FastAPI app
2. CORS middleware enabled (frontend HTTPS origin)
3. **Postgres schema initialised** (`init_db()` — creates all tables if not exist)
4. **screener.in login** — authenticated session established on boot (not lazily)
5. Prediction router with daemon-thread async pattern initialised
6. Universe refresh (async, 30s delay)
7. Keepalive loop (14-minute self-ping interval)
8. Outcome resolver (6-hour interval)
9. Warmup loop — pre-computes RELIANCE:IN:medium and AAPL:US:medium (150s delay, 90s gap)

---

## 23. Automation Workflows

**Directory:** `.github/workflows/`

### Daily Picks Generation — daily_picks_in.yml, daily_picks_us.yml, daily_picks_us_premarket.yml

India Daily Picks generates once daily (`daily_picks_in.yml`, cron `56 21 * * 0-4` = 21:56 UTC / 3:26 AM IST). US Daily Picks generates in two stages, each its own workflow:
- **Pre-Open base generation** (`daily_picks_us.yml`, cron `0 6 * * 1-5` = 06:00 UTC / 10:00 AM Dubai / 11:30 AM IST) — the heavy/full generation run.
- **Premarket Finalizer** (`daily_picks_us_premarket.yml`, cron `0 10 * * 1-5` and `0 11 * * 1-5` = two fixed-UTC candidates for EDT/EST, since GitHub Actions cron is UTC-only) — a lightweight review of the already-persisted base, targeting ~6:00 AM America/New_York with a 6:00-7:30 AM ET backend acceptance window. It never generates a base itself. See Product Integrity #007/#008.

Each trigger calls its Railway endpoint (`/api/picks/generate` or `/api/picks/premarket-finalize`) using an `X-Secret` header. All are live; scheduler dispatch is best-effort — observed actual trigger times have repeatedly landed hours after the nominal cron time (a GitHub Actions scheduling-reliability issue tracked separately, not a code defect in this repository). A GitHub Actions run reporting "success" certifies only that the trigger `POST` was accepted — not that the downstream job completed. See the Current Release Status register for live operational state and natural-run evidence.

### Multibagger Fundamentals Refresh — multibagger_refresh.yml, multibagger_refresh_us.yml

Nightly full-universe fundamentals refresh feeding the Multibagger Screen, one workflow per market:
- **India** (`multibagger_refresh.yml`, cron `0 17 * * 0-4` = 17:00 UTC) — ~2,300 NSE stocks via screener.in, ~1-2 hours.
- **US** (`multibagger_refresh_us.yml`, cron `0 8 * * 1-5` = 08:00 UTC) — ~5,300 common stocks via yfinance, ~5-6 hours. Scheduled to start after US Daily Picks' base run typically completes and to finish well before India's evening jobs; also protected by a durable, fail-closed conflict check against an active US Daily Picks job (`POST /api/multibagger/refresh?market=US` refuses to start while one is active) and a cooperative stop signal US Daily Picks can raise if the refresh is still running — see Product Integrity #008.

### Model Validation Scheduling

Model validation runs through an in-process Railway scheduler (`_validation_schedule_loop`) that cycles the Nifty 100, Midcap, and US universes. The former GitHub Actions validation workflow has been retired.

### keep_alive.yml — Health Monitoring

`keep_alive.yml` pings the Railway backend's `/health` endpoint every 10 minutes as a health monitor. Railway does not require anti-sleep pinging; this workflow is documented as monitoring rather than cold-start prevention.

---

## 24. Persistence & Data Durability

The hosting platform's ephemeral disk means files written locally are wiped on every restart/redeploy. All user-facing and learning data is stored in PostgreSQL to survive this.

### What Lives in Postgres

| Data | Table | Survives Restart |
|------|-------|-----------------|
| Price alerts | `price_alerts` | ✅ Yes |
| Watchlist | `watchlist` | ✅ Yes |
| Paper trades & portfolio | `paper_trades`, `paper_portfolio` | ✅ Yes |
| Daily picks cache | `daily_picks_cache` | ✅ Yes |
| Validation results | `val_runs`, `val_signals` | ✅ Yes |
| Alpha engine predictions | `predictions` | ✅ Yes |
| Outcome resolution | `outcomes` | ✅ Yes |
| IC history | `factor_ic_history` | ✅ Yes |
| Regime log | `regime_log` | ✅ Yes |
| Score snapshots | `score_snapshots` | ✅ Yes |

### What Is Transient (acceptable)

| Data | Storage | Why Acceptable |
|------|---------|---------------|
| Trained ML models (`meta_model_*.pkl`, `regime_kmeans.pkl`) | Local file | Auto-retrains from Postgres on next run — one cycle of degraded weights, no user data lost |
| API response caches (quotes, heatmap, movers) | In-memory (TTL) | Market data; freshly fetched anyway |

### screener.in Session

- Login fires at Railway boot (not lazily on first request)
- Session refreshed every 6 hours
- `SCREENER_EMAIL` + `SCREENER_PASSWORD` must be set as Railway environment variables
- Login logs: `[startup] screener.in login succeeded/failed` — check Railway logs after deploy

---

## 25. Factor Weights by Horizon

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

## 26. Key Design Principles

1. **No Look-Ahead Bias** — Backtester uses only data available at the prediction date. Forward returns computed strictly after prediction timestamp.

2. **Full Explainability** — Every signal has reasoning bullets, factor breakdown, and confidence components. Nothing is a black box.

3. **Honest Risk-Adjusted Returns** — Target prices are not inflated. Trade levels (entry, stop, target) are mutually consistent with the signal.

4. **Sector-Aware Scoring** — Different valuation thresholds for banks (ROE > 15%, P/B < 1.5), NBFCs, and IT companies vs industrials.

5. **Institutional-Grade Signals** — Piotroski F-Score, Sharpe Ratio, ROIC, IC engine, Ledoit-Wolf covariance optimisation — same tools used by quant funds.

6. **Data Resilience** — Three-layer fallback chain: yfinance → screener.in → BSE API. If news is unavailable, weights redistribute gracefully.

7. **Memory-Efficient** — Cache capped at 300 entries with LRU eviction (memory-safety cap). Concurrent predictions use daemon threads, not asyncio tasks.

8. **Self-Improving** — Outcome logger tracks every prediction. IC engine retrains weekly. Factor weights evolve as the model sees more real-world outcomes.

9. **Real-Time Ready** — 15-minute prediction cache, async background computation, React Query polling for live data.

10. **Investor Transparency** — Every number in the UI is traceable to a specific calculation in the codebase. This document is kept current with every code change.

11. **Postgres-First Persistence** — All user data (watchlist, alerts, paper trades, picks, validation, alpha engine) is stored in PostgreSQL. The hosting platform's ephemeral disk is never trusted for user-facing state.

---

## 27. Changelog

### Session 13 — 2026-07-12

Frontend copy/UX audit and correction, prompted by inconsistent horizon wording noticed across the app. **Frontend copy/UX only** — no backend, Prediction Engine, Daily Picks generation, RCI, scheduler, or horizon API-contract changes.

**Horizon wording standardized (landing page, Market Overview, Daily Picks, Paper Trading):**

- Audit found three different day/week/month ranges describing the same three horizons across pages: the landing page and Market Overview said "1–10 Days"/"1–3 Months" (Market Overview additionally showed "6M – 3 Years" for Long Term, which doesn't match the 3–6 month horizon actually scored anywhere else); Daily Picks and Paper Trading said "1–5 days" (missing "trading"); only the Stock Detail page's tab labels (text only, no range shown) were already consistent.
- Added `frontend/src/utils/horizons.ts` as the single source of truth: **Short Term · 1–5 trading days**, **Medium Term · 2–4 weeks**, **Long Term · 3–6 months**. Landing page (`page.tsx`) now imports it directly; Market Overview (`dashboard/page.tsx`), Daily Picks (`picks/page.tsx`), and Paper Trading (`paper-trading/page.tsx`) had their literal range strings corrected to match (each keeps its own local array for page-specific fields like routing keys/accent colors, but the `period`/`sub` text is now byte-identical everywhere).
- "Core Investment" (6 months–3 years, future/planned only) is deliberately not part of this list — it was never live anywhere in the app (confirmed via a full-codebase search) and this change does not add it as a live horizon tab, Daily Picks tab, or Stock Detail API contract.
- The Stock Detail page's `HORIZON_LABEL` (7 trading days / 3 months / 12 months, used only as the Backtest tab's "Forward Window" metric) was deliberately left untouched — it reflects an actual backend `forward_window_days` value, not marketing copy, and changing it without backend verification could misrepresent real backtest methodology.

**Market Overview (`dashboard/page.tsx`) top section compacted:**

- The three horizon cards (each a full-height bordered block) were replaced with compact single-line pill chips using the corrected canonical wording, with the fuller description moved to a hover tooltip. Reduces the vertical height of the top section without removing any information.
- Audited for a "Fear & Greed" huge standalone graphic per a user report — none exists on this page. The only Fear/Greed-labeled element in the app is a compact `ConfidenceMeter` row already titled "Market Sentiment (Fear/Greed)" on the crypto Stock Detail page's Signal Breakdown, not Market Overview — left as-is since it was already compact and already methodology-labeled, not a large graphic.

**Multibagger page repositioned as a research screen, not an investment call (`multibagger/page.tsx`):**

- Title changed "Multibagger Screen" → "Multibagger Research Screens". Added an explicit banner: "These are long-term research screens, not buy/sell calls... Review the Stock Detail signal, risks, valuation and portfolio fit before acting," plus a sentence noting these screens may feed a future Core Investment research step without implying Core Investment itself is live.
- Verdict labels renamed to avoid reading as a trade call: "Elite Strong Buy" → "Elite Candidate", "Strong Buy" → "Strong Candidate", "Watchlist" → "Research Watchlist", "Watch" → "Watch / Risk Flag", "Avoid" unchanged. Only the display label changed — the underlying backend verdict keys (`elite_strong_buy`/`strong_buy`/`watchlist`/`watch`/`avoid`) are untouched.
- Quality Compounders' description changed from "suitable for 5-10 year holding" (a specific holding-period claim) to "Potential long-term compounder candidates; requires deeper thesis validation." Scorecard checklist, anti-loss red flags, shortlisted top ~20%, stock links, market selector, and refresh timestamp are all preserved unchanged.

**Verified:** new `horizons.test.ts` (canonical wording, confirms no "6M"/"3 Years" string and no Core Investment horizon) and `multibagger-copy.test.ts` (candidate labels, no "Strong Buy" substring, no "5-10 year holding" substring) — both pass; full 133-test frontend suite passes; `tsc --noEmit` clean; `next build` succeeds. Live-verified in a running dev session: landing page renders the exact canonical horizon text, Multibagger page renders the new title/banner/labels, no console errors, no horizontal overflow at 375px or 1280px width. Market Overview's compacted chips were not re-verified live in that same session (the page requires an authenticated session this local dev environment didn't have) — covered by the shared `horizons.ts` module and its test instead, since Market Overview imports that exact module.

**Not touched:** backend, Prediction Engine, Daily Picks generation, RCI, scheduler, feature flags, horizon API/enum contracts, Railway/Vercel config.

**Stock Detail unsupported-data truthfulness and recovery hardening** (same-day follow-on, three commits: `0e02d55`, `299046c`, `5b8dd5c`):

- **India intraday chart-interval disclosure (`0e02d55`).** Investigated whether Indian stocks could get minute-level TradingView intervals by testing both the `NSE:` and `BSE:` exchange prefixes directly against TradingView's public anonymous embed. **Finding: the `NSE:` prefix hard-fails** ("This symbol is only available on TradingView") for every Indian symbol tested — confirming the existing `BSE:`-based mapping (renamed from the misleadingly-named `NSE_OVERRIDE` to `BSE_MAP`) is the correct, working path, not an artificial restriction. Real intraday (1m/5m/15m/1h) candle rendering could not be reliably confirmed for `BSE:` either in an anonymous embed. **No behavior change to the chart itself** — Indian stocks remain on 1D/1W/1M only — but a new, truthful user-facing disclosure was added ("Intraday chart intervals are unavailable for this Indian symbol in the embedded chart") so the limitation is stated rather than silently absent. **No claim is made that any Indian symbol supports intraday intervals.** US and crypto intervals unchanged.
- **Prevent fabricated signals on unavailable/unsupported data (`299046c`) — a correctness and trust fix.** Previously, an unsupported or data-unavailable symbol could reach the prediction UI with a missing `signal` field silently defaulted to a fabricated "HOLD," a blank confidence bar, and an **unrestricted Paper Trade button** — i.e. a user could act on a signal that was never actually computed. Fixed on both ends: the **backend** now rejects unsupported IN/US symbols with a structured `404` (`SYMBOL_NOT_SUPPORTED`) before any cache/compute logic runs, and translates a cached error result into `503` (`DATA_PROVIDER_UNAVAILABLE`) instead of a bare `200`; the **frontend** adds a typed `PredictionError` plus runtime validation on every `200` body (`signal`, `confidence`, `symbol`, `market`, `horizon`, `current_price`), so a malformed or error-shaped response can never render as BUY/HOLD/SELL. Stock Detail now distinguishes "Symbol not supported" from "Analysis temporarily unavailable," a quote failure renders "Price unavailable" instead of an indefinite loading state, and **Paper Trade is hidden whenever no validated prediction and price exist.** No scoring formula, threshold, confidence calculation, or Daily Picks logic changed — this is purely about never presenting missing/errored data as a real signal.
- **Unsupported-symbol recovery routing and copy accuracy (`5b8dd5c`).** The recovery button on an unsupported-symbol page was labelled "Back to Dashboard" but linked to `/` (the public landing page), which itself required a second click through its own "Go to Dashboard" CTA — now routes directly to `/dashboard` in one click. Also corrected the accompanying copy to describe **StockSense360's own coverage** (NSE for India, a supported US universe for US) rather than implying anything about which real-world exchange the symbol is listed on — a genuine, real company can still be legitimately BSE-only; the old copy read as a false claim about the security itself rather than an honest statement of this platform's coverage.
- **Not touched by any of the three:** backend scoring/ranking (except the explicit `299046c` unavailable-data guards above), Daily Picks generation, RCI, scheduler, Prediction Engine model logic.

### Session 12 — 2026-07-11

A same-day follow-on to Session 11's Portfolio work, closing out the Signal column's "permanent blank" problem and simplifying the sector-grouped table's heading/subtotal duplication. **Portfolio UX/runtime hotfixes only** — not the Epic 007 (`MASTER-ROADMAP.md` §11) Portfolio and Watchlist Intelligence initiative, which remains Planned / Not Started; nothing here adds portfolio-aware recommendations, allocation advice, or any new intelligence. No Daily Picks, Prediction Engine scoring, RCI, scheduler, or backend behavior changed.

**Portfolio — quote rows self-heal instead of getting stuck (`8e11877`):**

- Live quote rows that failed to resolve (a transient provider hiccup, a slow cold fetch) previously had no path back to a resolved state short of a full page reload. Added a jittered `refetchInterval` to the quote query so a stuck row retries itself in the background rather than requiring the user to notice and refresh manually.

**Portfolio — Signal column no longer shows a permanent blank (`1e8b2ef`, `9c1c803`, `1e94027`):**

- Root cause (already diagnosed in an earlier session, closed here): `PredictionEngine`'s in-memory `_pred_cache` has only a 15-minute TTL, so a symbol Daily Picks scored overnight showed a real signal for 15 minutes and then reverted to blank for the rest of the day. A `score_snapshots` Postgres fallback (persisted nightly by Daily Picks for every deep-scored candidate) closed most of this gap, but a holding that was never one of Daily Picks' ~400 nightly candidates and has never had its own Stock Detail page viewed genuinely has no signal anywhere in the system — not a caching bug, a coverage gap.
- Closed the remaining gap with a portfolio-scoped, concurrency-bounded (limit 3) refresh, reusing the existing single-symbol `/signal` endpoint and the existing cache-only `/signals/cached-batch` endpoint — no new backend endpoint, no full-universe computation. Triggered by the existing "Refresh missing signals" button (which now also retries previously-failed symbols and shows live progress) and once automatically per market per browser session (a module-level `Set`, not component state, so it doesn't re-fire on every tab revisit).
- A holding now always shows one of: a resolved BUY/HOLD/SELL badge, a compact `Updating…` state while its refresh is in flight, `Unavailable` if a refresh attempt failed, or `Unsupported` if the symbol was rejected outright (HTTP 404) — never an undifferentiated "—" forever. The Portfolio Allocation chart's BUY/HOLD/SELL counts already excluded `signal === null` rows before this change, so no chart-level change was needed; these in-flight/failed states still correctly fall outside all three buckets.

**Portfolio — sector display normalization for Wellness/Healthcare-style listings (`38469be`):**

- A holding like JSLL (Jeena Sikho Lifecare) is filed by screener.in under a broad `Consumer Services` sector with `Wellness` as its narrower industry — accurate but not useful for portfolio grouping, since it reads as a generic consumer-services stock rather than the healthcare/wellness business it actually is. Added `normalizeDisplaySector()` (`frontend/src/utils/sectorDisplay.ts`), a pure display-layer function matching industry-name keywords (wellness, hospital, healthcare, medical care, diagnostic, pharma) to a normalized "Healthcare" display sector — generic keyword matching, not a JSLL-specific hardcode, so any similarly-filed stock gets the same treatment. The raw sector/industry values are never modified or discarded; they remain available for a tooltip on the sector's subtotal row.

**Portfolio — merged the sector heading row into its subtotal row (`9492fb8`):**

- Session 11 added a sector heading (`IT · 2 holdings · ₹4,500 · 50.0%`) followed by a separate "Sector Total" subtotal row — functionally duplicating the same holding-count/value/percentage information twice and costing a full extra row per sector group. The heading row is now removed entirely; its holding-count and %-of-portfolio context (never the total value itself, which already lives in the subtotal row's own numeric columns) is folded directly into the subtotal label: `"{sector} Total · {holdingCount} holdings · {percent}%"` (e.g. `"Healthcare Total · 2 holdings · 42.4%"`, `"Resolving sector… Total · 5 holdings"` with no percentage for the still-resolving bucket). Grand Total, totals math, and the "partial while prices load" note are unchanged.
- **This supersedes Session 11's sector-heading description above** (`IT · 2 holdings · ₹4,500 · 50.0%`) — that heading row no longer exists.

**Verified:** 33 targeted `HoldingsTable` tests + full 126-test frontend suite passing, `tsc --noEmit` clean, `next build` succeeding, plus live visual confirmation by the user in a running dev session (Signal column showing compact `Unavailable` states instead of permanent blanks, no console errors, no layout overflow). The sector-grouped view's live rendering could not be exercised in that same session (no authenticated backend locally to produce sector data) — that specific behavior is covered by the automated tests, not a live screenshot.

**Not touched:** Daily Picks, Daily Picks generation, Prediction Engine scoring, RCI, scheduler, feature flags, broker integrations, Railway/Vercel config, backend code.

### Session 11 — 2026-07-11

A focused Portfolio + Paper Trading performance/UX session, driven by two live user reports: Paper Trading feeling noticeably laggier to navigate into (and out of) than every other tab, and the Portfolio Allocation chart getting stuck showing a fake "Loading… 100.0%" sector bar. No Daily Picks, Prediction Engine scoring, RCI, or scheduler changes.

**Paper Trading — root-caused and fixed the tab-transition lag (4 commits: `5cccf97`, `1e80d79`, `7eb7fc0`, `d80aa3d`):**

- Confirmed via direct code reading (not guesswork) that `get_portfolio` opened two brand-new `psycopg.connect()` calls per request (`_ensure_portfolio` plus the handler body) — each a full TCP+TLS handshake to Postgres, with no pooling despite `psycopg[pool]` already being a declared dependency. Replaced `_conn()`'s direct `connect()` with a process-wide `psycopg_pool.ConnectionPool`.
- Found `OpenTradeRow` ran its own independent, un-staggered `useQuery` for live quotes — duplicating a fetch the page already made (staggered, for action-queue sorting) to the identical endpoint. With 30-40+ open positions common, that was up to 80+ concurrent requests firing on mount instead of 40. Row now receives its quote as a prop.
- Found `useMarketOpen`/`usePrefersReducedMotion` were each called once *per open-trade row* despite returning the identical value for every row — 40+ redundant `setInterval(30s)` timers and `matchMedia` listeners, each needing its own cleanup on unmount. This is the mechanism behind the *outbound* lag specifically: navigating away had to synchronously run 80+ cleanup functions before the next route (and its nav highlight) could commit. Both hooks lifted to the page, called once.
- Confirmed via `wc -l` that Paper Trading's page component was by far the largest client page in the app (1483 lines — ~2x Portfolio, ~1.4x Daily Picks). Code-split the below-the-fold Trade History section (`ClosedTradeHorizonBlock`/`ClosedTradeRow`, ~260 lines) into `frontend/src/components/PaperTradeHistoryBlock.tsx`, loaded via `next/dynamic` so it's no longer part of the tab's initial JS payload.

**Navigation — active-tab highlight lagging behind the actual route (`546aa4f`):**

- A live screenshot showed the URL and page content already on `/paper-trading` while the nav bar's highlighted/bordered tab was still "Validation." Root cause: `NavLinks.tsx`/`MobileNav.tsx` applied `transition-colors` (a ~150ms fade) unconditionally to every nav link, including the active↔inactive swap itself — so a navigation could render with the destination page's content already fully loaded while the previous tab's border/background hadn't finished fading out. Fixed by scoping `transition-colors` to only the inactive/hover branch (which tab is current is state, not a hover affordance — it must snap instantly), plus keying each link list on `pathname` as a belt-and-suspenders remount.

**Portfolio — decoupled sector allocation from AI signal loading (`ff4438f`):**

- Root cause of the "Loading… 100.0%" bug: sector came from the per-holding signal fetch's `quality_factors.sector`, gated on the same staggered, potentially slow (cold-cache) AI prediction that drives the Signal badge. A prior same-day hotfix kept unresolved holdings out of "Other" by injecting a synthetic `{sector: "Loading…", ...}` entry directly into the chart's `sectorSlices` array — but `PortfolioAllocationChart` computed its own local "has real sector data" check from that same array without excluding the placeholder, so it counted "Loading…" as real data and rendered it as a single fake 100% bar.
- Fixed at the source, not just the symptom: new `GET /api/stocks/sectors` batch endpoint + `services.fundamentals_cache.get_sectors_batch()`, reading only the nightly-refreshed `stock_fundamentals_cache` table (read-only, no prediction/scoring, no feature flags). Portfolio now fetches sectors via one batch request per market instead of waiting on the staggered signal queries. `sectorSlices` holds only genuinely-resolved sectors now; unresolved value/count is tracked separately and rendered as a distinct "Resolving sector…" state, never as a chart slice and never folded into "Other."
- Verified with 10 new backend tests (including one that patches `PredictionEngine.predict` to raise, confirming the sectors endpoint never touches it) and 14 new/updated frontend tests.

**Portfolio — holdings table totals (`ce7352a`):**

- Added a market-specific grand-total footer row (Invested, Current Value, Day's P&L, P&L) to the holdings table, appearing exactly once in both grouped and ungrouped mode. Current Value/Day's P&L/P&L each sum only rows that have actually resolved a live price — a still-loading holding contributes nothing to the sum rather than a fabricated `0`, and the footer shows a "· partial while prices load" note whenever any row hasn't resolved. Renamed the "Value" column header to "Current Value" for clarity.
- Sector group headings also gained holding count, total value, and % of the selected market's portfolio value (e.g. `IT · 2 holdings · ₹4,500 · 50.0%`).

**Not touched:** Daily Picks, Daily Picks generation, Prediction Engine scoring, RCI, scheduler, feature flags, broker integrations, Railway/Vercel config. INR and USD are never combined — every total/footer/chart is computed from a single market's already-filtered rows.

### Session 10 — 2026-07-10

A shorter, fix-focused session across Portfolio, Stock Detail, Daily Picks, and the Multibagger Screen — no new Epics, mostly closing real bugs a user flagged from live screenshots plus one root-cause data bug found while investigating a "why is this empty" report. Also includes a same-day follow-up: Daily Picks' Large/Mid/Small-cap stratification and short-term confidence priority.

**Daily Picks — Large/Mid/Small-Cap Stratification and Short-Term Confidence Priority:**

- Following the Multibagger pledge-NULL investigation below, a user asked which India market-cap tiers Daily Picks actually covers, suspecting Large-cap-only. Confirmed via direct code reading: `_get_universe_by_mcap` sorted `yf.screen()` results by market cap descending with a hard 250-symbol cutoff — by SEBI's own rank convention (Large = rank 1-100, Mid = 101-250), this was structurally Large+Mid only. US was worse: a hard `$2,000M` floor built into the query itself excluded true US small-caps by definition, not just by rank pressure. Worse still, the real deep-scored pool was smaller yet — `_bulk_screen`'s momentum-narrowing step truncated to `_N_CANDIDATES` (default 50, shared across all 3 horizons) before the actual `PredictionEngine` ever saw a candidate.
- Fixed by replacing the Yahoo-screener-based universe discovery entirely with `stock_fundamentals_cache` — the same nightly-refreshed table (screener.in for India, yfinance-derived for US) the Multibagger Screen already maintains, previously unused by Daily Picks. New `fundamentals_cache.get_ranked_universe()` returns the full cached universe ordered by market cap; `daily_picks.py`'s new `_assign_cap_tiers()` (rank-based for India per SEBI convention, value-based for US: Large >$10B, Mid $2B-$10B, Small <$2B) and `_stratified_sample()` build a ~400-symbol pool split roughly 40/30/30 across tiers, replacing the old 250/anchor-100 cutoffs. `_N_CANDIDATES` raised from 50 to 400 to match, so the momentum-narrowing step no longer discards the stratification just built.
- Phase 5 selection now differs explicitly by horizon, per the user's own stated priorities: **short-term** ignores tier entirely and instead prioritizes confidence — candidates with **>80% confidence** are selected first (alpha-ordered within that group), with fill-down to lower-confidence candidates only when fewer than 6 clear the bar (never padded with fabricated picks — a weak-conviction day can legitimately show fewer than 6, even 0). **Medium/long-term** enforces a 2/2/2 Large/Mid/Small tier quota (topped up from leftover alpha-ranked candidates if a tier is short) so the list can't collapse back to all-large-cap even though large caps often score higher alpha on average.
- Cost stated plainly, not hidden: Phase 1 still runs sequentially (`max_workers=1`, unchanged — that throttle is for yfinance rate-limiting on per-stock calls, unrelated to this change) so 50→400 candidates is roughly an 8x increase in Phase 1's dominant cost, pushing total generation time from ~10-20 minutes to an estimated ~60-90 minutes. Confirmed acceptable: both markets' schedules leave a multi-hour runway before market open.
- Verified via 21 new/updated backend regression tests (tier-boundary edge cases for both markets' conventions, stratified-sampling honesty when a tier is short, short-term fill-down ordering including a zero-high-confidence day, tier-quota top-up), plus a deliberate-break-then-restore sanity check on the tier-quota re-sort step. Two test files testing the now-removed Yahoo screener retry/pagination mechanism (`test_in_screener_retry_and_observability.py`, `test_daily_picks_screener_count_limit.py`) were deleted rather than left stale. Full backend suite green (1517 tests). Not verified via a live triggered generation run — per standing protocol, the production `/api/picks/generate` endpoint is never called directly; the change rides the next natural scheduled run, to be checked read-only afterward.

**Multibagger — All Three India Screens Were Returning Zero Results:**

- A user cross-checked a manual screener.in query (11 conditions, market cap/ROE/ROCE/growth/debt/pledge/PE/EV-EBITDA/interest-coverage/current-ratio) that returned 12 real India stocks, against this app's Multibagger tool, which showed nothing at all under any India screen.
- Root cause, confirmed by directly scraping several known clean-pledge stocks (KPITTECH, KAYNES, SUZLON, RATNAMANI) and inspecting the raw field values: `promoter_pledge_pct` is only set when screener.in's shareholding table has a "Pledge" row at all — and screener.in only renders that row for non-zero pledge. A clean (no-pledge) stock therefore has this column `NULL`, not `0`. Every India screen's `promoter_pledge_pct < N` SQL condition silently failed on that `NULL` (SQL comparisons against `NULL` are always false), excluding almost every clean company — the exact opposite of the condition's intent — and driving `quality_compounder`, `multibagger_discovery`, and `tenbagger_early` to 0 results each, independent of any other threshold.
- Fixed in `backend/services/fundamentals_cache.py` by wrapping every pledge condition in `COALESCE(promoter_pledge_pct, 0) < N`, matching how `multibagger_scorecard.py`'s own checklist already (correctly) treated a missing pledge value as clean. Verified live post-deploy: `quality_compounder` 52 results, `multibagger_discovery` 167, `tenbagger_early` 73 (all were 0 before).

**Portfolio — Day's P&L, Sector Allocation, and a Real Mobile Overflow Bug:**

- Added a "Day's P&L" column (amount + %) alongside the existing overall P&L, reusing the `change`/`change_pct` already returned by the per-holding quote fetch — no new API calls.
- Added a "By Sector" / "By Stock" toggle to the Portfolio Allocation chart (`PortfolioAllocationChart.tsx`), grouping/summing holdings by sector (reusing sector data already computed inside `predict()` and now additionally exposed on the lightweight `/signal` endpoint's summary, again no new compute). Both the by-stock and by-sector slices are sorted descending by value.
- Fixed a real bug where India and US Portfolio views looked structurally different: the sector toggle used a `> 1 distinct sector` threshold to decide whether to show sector view at all, which stayed hidden for large India portfolios where most holdings were still resolving sector data (everything briefly sitting in "Other"). Relaxed to `> 0`, and fixed a related stale-default bug where the toggle's initial `useState` value froze at whatever was true on the very first render, before any sector data existed, and never reconsidered — now re-evaluates every render until the user explicitly picks a toggle.
- Fixed a genuine mobile bug: the holdings table overflowed the viewport width on narrow screens. Added a persistently-visible thin scrollbar to the table's scroll container and `flex-wrap` to the header row.
- Fixed the Day's P&L / overall P&L numbers wrapping onto two lines with inconsistent spacing, and an empty Signal column that should have shown a "computing" state instead of looking blank.

**Stock Detail — Confidence-Graded Colors, Wrong-Horizon Navigation, and a Hidden Chart:**

- The AI Signal panel showed a flat green "BUY" regardless of confidence (e.g. a 7% confidence BUY looked identical to a 90% one). Refactored to mirror `SignalBadge`'s existing convention: BUY is confidence-tiered (green ≥60%, yellow 45–59%, gray <45%); HOLD/SELL stay a single fixed shade, since only a weak BUY is the case worth visually flagging.
- The same flat-green problem existed independently on the Daily Picks card's "AI Confidence" bar and its expanded `ScoreBar` row — fixed with the same confidence-tiered color/gradient helpers.
- Fixed real broken navigation: opening a stock from a specific Daily Picks horizon tab (e.g. Medium Term) landed on the Stock Detail page's Short Term tab instead of the horizon the user came from. The picks card's `router.push` now passes `?horizon=`, and the Stock Detail page reads it (falling back to Short Term only if absent/invalid), matching the existing `?market=` pattern.
- The History tab's "Factor Breakdown" chart rendered completely empty (no lines, no legend, no console error) for every stock. Two independent root causes, both fixed in `ScoreHistoryChart.tsx`: (1) the branch wrapped `<Legend/>` and the mapped `<Line>` elements in a React Fragment, which Recharts' internal children-type scan doesn't traverse the way it flattens a plain array — switched to returning an array; (2) Recharts' default line-draw entrance animation can get stuck at its invisible first frame if `requestAnimationFrame` never progresses in a given tab — disabled via `isAnimationActive={false}` on every line in both the Composite Score and Factor Breakdown views, which also removes the animation's dependency on a live rAF loop entirely. Also changed the Composite Score line to always show its values via `LabelList` instead of requiring a hover.

**Documentation note:** this app had never had a dedicated Multibagger section in this document (only glossary mentions in §18a) — flagged here rather than silently left out. **Resolved same-day**: see §18b Multibagger Screen, written immediately after this fix.

### Session 9 — 2026-06-24

A very long session: resolved a real production infrastructure incident (duplicate Render+Railway deployments writing to the same database), fixed four separate event-loop-blocking bugs causing the slowness reports across Portfolio/Paper Trade/Heatmap/Crypto, closed three "signal contradicts its own data" gaps in the prediction and Multibagger engines, fully separated the Learning Alpha Engine by market (IN and US had never actually trained independently despite Daily Picks ranking both), and closed two critical Supabase security findings (Row-Level Security disabled on every table).

**Infrastructure Incident — Duplicate Render + Railway Deployments:**

- Render kept sending "exceeded memory limit" / repeated restart emails for a service the team believed was already fully migrated to Railway. Investigation found Render's `stocksense-api` service was never actually decommissioned — confirmed via its own logs that it was independently re-running the full Daily Picks generation pipeline and background prediction threads, completely unrelated to the Railway deployment serving real traffic.
- **Root cause, fully traced:** `_catchup_picks()` in `api/main.py`'s startup `lifespan()` runs on every process boot — if today's picks don't exist yet in Postgres, it generates them. Render and Railway share the *same* Supabase Postgres database (confirmed: identical connection string on both dashboards). Every OOM-triggered restart on Render re-ran this startup catchup; if the timing raced ahead of Railway's legitimate scheduled run, it kicked off a second full generation pass, consuming enough memory to OOM again — a genuine self-sustaining crash loop, not external traffic.
- Confirmed via the Railway Network tab that the live frontend's `NEXT_PUBLIC_API_URL` was already correctly pointed at Railway — the Render service had zero legitimate purpose left. Suspended (later safe to fully delete) via the Render dashboard; verified the live site continued working normally with zero behavior change.
- Side-effect noticed during the investigation: `postgres_store.py`'s connection pool doesn't set `prepare_threshold=None` like every other direct-connection call site in the codebase, which is why a `prepared statement "_pg3_0" already exists` error showed up in the Render logs — Supabase's transaction-mode pooler (port 6543) doesn't reliably support psycopg's default prepared-statement caching across multiple concurrent client processes. Noted as a small follow-up; not separately fixed this session since the duplicate-deployment removal eliminates the only known case of two processes contending for the pool simultaneously.

**Four Event-Loop-Blocking Bugs Found — the Real Cause of "Slow" Reports:**

Methodically audited every `async def` in `backend/services` and `backend/api/routers` for one specific anti-pattern: a function declared `async def` that calls a *real* blocking network operation directly instead of through `loop.run_in_executor(...)`. A single such call freezes Python's one event loop for its *entire* duration — not just slowing the request that triggered it, but stalling every other concurrent request on the process, including completely unrelated ones (a bare `/health` check measured 13 seconds during one load test, simply queued behind an unrelated blocked call).

- **`MarketDataService.get_quote()`** (`market_data.py`) — the yfinance `fast_info` fallback/enrichment calls ran inline. This is what made Portfolio (one quote request per holding) and Paper Trading (one quote request per open trade, recurring every ~28s) feel slow as holdings/trades grew past a handful. Confirmed via direct load testing against production: 8 concurrent fresh quote requests took **15.4 seconds total** before the fix (all resolving within milliseconds of each other — the signature of serialized, not parallel, execution) versus **1.69 seconds** after.
- **`MarketDataService.get_ohlcv()`** — same pattern, used by the chart on every stock detail page.
- **`crypto_engine.predict_crypto()`** — the worst case: a rate-limit retry loop with `time.sleep()` directly inline, meaning a single throttled crypto request could freeze the *entire backend* for several seconds per retry attempt, compounding across attempts.
- **`news_sentiment.NewsSentimentService.get_macro_news()`** — called several sequential blocking `feedparser.parse()` RSS fetches directly; the per-stock news path was already correctly wrapped, only the macro feed (`/api/news/macro/us`, `/api/news/macro/india`) had this bug.
- All four fixed identically: moved the blocking call into a plain sync helper method, dispatched via `loop.run_in_executor(None, ...)` with a timeout, matching the pattern already used correctly elsewhere in each file.
- **Load-tested the fix directly against production** afterward: 100 concurrent fresh quote requests succeed 100% of the time (with growing but acceptable latency, up to ~12-17s in the worst case); the real ceiling where requests start failing (false "symbol not found" errors from all three data-source fallbacks getting saturated at once — not a crash) is somewhere between 100 and 150 concurrent fresh requests. Re-verified by re-querying one of the "failed" symbols in isolation immediately after — it succeeded instantly, confirming the failures were transient saturation, not a real bug.

**Three "Signal Contradicts Its Own Data" Gaps Closed:**

A user-flagged real paper trade (MU, +82% confidence BUY that dropped 13.18% the next session) led to tracing exactly why a high-conviction signal could still produce a bad outcome. The composite score and confidence weren't wrong — strong fundamentals genuinely supported the call, and the model *did* flag the risk that materialized (`"High beta (2.17) — amplified downside in market corrections"`) — but that risk only cost a small score penalty, never enough to flip a confident-looking badge. Three concrete instances of this same root pattern were found and fixed:

1. **Unfavorable risk/reward never demoted confidence.** `_trade_levels()` already computes a real `risk_reward_ratio` (MU's was 0.36 — risking $146.65/share to make $52.59/share) but never fed it back into the signal. Added `_apply_risk_reward_adjustment()` in `prediction_engine.py`: when risk exceeds reward for a BUY/SELL signal, confidence is capped at 30 (pushing the badge into the gray/weak tier) and an explicit plain-language warning is added to both the reasoning list and bear case. Composite score and BUY/HOLD/SELL classification are deliberately left untouched, so Daily Picks ranking and the IC learning engine — which key off composite score, not confidence — are unaffected.
2. **Severe promoter pledge (IN) had the identical gap.** Pledge over 50% is already described in the fundamental-score reasoning as *"severe margin call risk; avoid,"* but that text only cost -8 points inside a ±10-capped governance bucket — easily absorbed by strong fundamentals elsewhere. Added `_apply_pledge_adjustment()`, same demotion-to-30 treatment, fires only for IN market + BUY signal + pledge > 50%.
3. **Multibagger's shortlist ranking ignored its own red-flag verdict.** `annotate_and_rank()` ranked the "Shortlisted" (top ~20%) tier purely by raw checklist score, completely ignoring `verdict`/red flags — reproduced with a synthetic case where a stock with 5 separate red flags and an explicit `verdict: avoid` still got `shortlisted: true`, tied on score with (and ranked ahead of, due to Python's stable sort preserving list order on ties) clean stocks with zero red flags. The frontend renders the shortlist flame icon and verdict badge independently per row, so this could show "Shortlisted" directly next to "Avoid" on the same row. Fixed: red flag count is now a secondary sort key (clean stocks win ties), and shortlist eligibility excludes any "avoid"-verdict stock outright regardless of raw score.

**Daily Picks: One Dead Feature, One Gate Gap:**

- `_predict_stock()` read `trade.get("risk_reward")`, but `_trade_levels()` returns the field as `risk_reward_ratio` — the key never existed. The "R:R 1:X" badge on every single Daily Picks card has been silently dead since it was built (the `&&` short-circuits on `undefined`); the stock detail page was unaffected since it reads the correct key. Fixed the key name.
- The picks-specific quality gate only required `confidence >= 25%` — but the two confidence-demotion fixes above demote to exactly 30, which clears that floor. A stock explicitly flagged with bad risk/reward or severe pledge could still land in the curated "Top 6" list. Added an explicit check excluding any pick whose reasoning carries a "Risk/Reward" or "Governance Risk" indicator, regardless of confidence — a curated best-ideas list should hold a higher bar than "not pure noise."

**"Unknown" Sector Badge — Real Root Cause Found, Not Just Patched Again:**

- The sector badge intermittently showing literal "Unknown" text (previously partially addressed in Session 8 area, see task #8 history) turned out to have two separate causes, both fixed this time: (1) the final fallback path in `sector_strength_score()` hardcoded the literal string `"Unknown"` instead of `None` when no sector text was available at all — even though the frontend already correctly hides the badge on a falsy value, "Unknown" is a non-empty string, so it always rendered; (2) the live screener.in scrape that's supposed to supply the fallback text is cached in-memory for 4 hours **including failures** — a single rate-limited request during the nightly ~150-call batch poisons that symbol's result for the rest of the window, and a *different* subset of stocks hits this each night, matching the "fixed once, reappearing for different stocks" report.
- Fixed the literal-string issue directly (return `None`, let the existing frontend conditional hide the badge). For the underlying data gap, added a second fallback layer: `stock_fundamentals_cache` (the Postgres table already maintained nightly for the Multibagger screen, persists across restarts) is now checked before giving up — a stock's sector classification doesn't change day to day, so once it's been successfully resolved once by the nightly refresh job, today's live scrape failing no longer matters.

**Learning Alpha Engine — IN and US Were Never Actually Separate:**

A deeper audit (explicitly requested after confirming the above fixes) found the entire IC engine / meta-model / outcome-resolution pipeline had no concept of market at all, despite Daily Picks ranking both IN and US stocks with it.

- `predictions`/`outcomes` tables had no `market` column. `outcome_logger._fetch_return` hardcoded the NSE `.NS` ticker suffix unconditionally — for a US stock this builds `"AAPL.NS"`, which doesn't exist on Yahoo Finance, so every US outcome resolution silently failed forever. US predictions were logging fine; their outcomes could never resolve.
- **Net effect:** the IC engine's live IC and the meta-model were trained only on the IN outcomes that ever resolved, yet that single learned weighting was applied to rank both markets — US Daily Picks were effectively being ranked by India-calibrated factor weights, never validated against a single real US outcome. This is also exactly what the "AI Engine: Learning…" badge on the Daily Picks page reflects (`meta_model: any(r.get("meta_alpha") is not None for r in top_buy)`) — US could never progress past "Learning…" no matter how long the app ran, since it could never accumulate the 100 resolved outcome pairs needed to train.
- Fixed end-to-end: added `market` column (default `'IN'`) to both Postgres and SQLite schemas, with a guarded one-time constraint migration (the old `UNIQUE(symbol, horizon, pred_date)` had to widen to include market, or an IN and US outcome for the same symbol/date would collide). Every function in the chain (`log_prediction`, `log_outcome`, `get_training_data`, `get_unresolved_predictions`, `count_training_rows`) now takes and respects `market`, on both store backends. `outcome_logger` resolves both markets with the correct ticker suffix per market.
- **`ic_engine.ACADEMIC_PRIOR_IC` now has separate prior tables per market** — IN unchanged (Nifty research), added distinct US priors sourced from real published research (Jegadeesh-Titman momentum, Fama-French 5-factor, AQR Quality-Minus-Junk), since US large-caps absorb sentiment faster and have different analyst-coverage dynamics than IN mid/small-caps. Also fixed a latent NaN-propagation bug found along the way: a zero-variance factor sample now falls back to the academic prior instead of poisoning the weight with NaN.
- **`meta_model` now trains and persists a separate pickle per (market, horizon)** instead of one shared model per horizon. `weight_adapter.run_adaptation(market)` retrains one market's IC cache + meta-models per cycle; Daily Picks calls it once per market in its own background thread. Regime KMeans retraining stays shared across markets by design — it's a genuinely global macro signal (VIX, DXY, US10Y, both Nifty and S&P trend), not a per-market one.
- Verified directly: IC weights and raw IC values are now provably distinct between IN and US with zero live data (correctly falling back to each market's own academic prior).
- **Known follow-up, out of scope this session:** `score_snapshots` (backs the per-stock History tab chart, not the learning/ranking loop) still isn't market-tagged — low risk given current symbol universes don't collide between markets, but worth revisiting if it ever causes a visible chart bug. Also flagged but not fixed: trained meta-model `.pkl` files are saved to local disk, which Railway/Render wipes on every restart — the exact same problem `postgres_store.py`'s own docstring already documents for the old SQLite setup, meaning trained models are lost on every redeploy and only return once enough new outcomes re-accumulate.

**Critical Security Finding — Row-Level Security Disabled on Every Table:**

- Supabase's automated Security Advisor flagged two critical issues: a publicly-accessible table with RLS disabled, and sensitive columns exposed without access restriction. Verified by full inventory across every schema-creation site in the codebase (`postgres_store.py`, `fundamentals_cache.py`, `alerts.py`, `portfolio.py`, `validation_engine.py`) — **all 18 tables** had RLS disabled, including ones with real personal data: `terms_acceptance` (name, mobile, IP address), `paper_portfolio` and `price_alerts` (email), `watchlist`/`portfolio_holdings`/`signal_feedback`/`nps_responses` (user IDs). Anyone with the project's URL and anon key — normally embedded in frontend JavaScript by Supabase's own design — could read, edit, or delete any of this directly through the auto-generated PostgREST API, completely bypassing the FastAPI backend's own access control.
- Fixed by adding `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` (idempotent) to every table-creation site. No policies needed: this backend connects as the `postgres` role via Supabase's pooler (`postgres.<project-ref>` username format), which has `BYPASSRLS` by default in every Supabase project specifically so enabling RLS for the public REST API doesn't affect direct/admin access. Verified live post-deploy with a full write→read→delete cycle — zero behavior change for the app, public REST hole closed.
- **Process gap found and fixed along the way:** `validation_engine.py`'s schema init (`_init_db()`) only ran lazily on first call to a handful of functions, never from `main.py`'s startup `lifespan()` like every other table. This meant the RLS fix for `val_runs`/`val_signals` sat deployed but inert in production until something happened to call `/api/validation/results` — confirmed live via Supabase's Security Advisor still showing those two tables as vulnerable after the rest had cleared. Exported a public `init_db()` wrapper and added it to the startup lifespan so future schema changes to that file take effect on deploy, not on incidental first use.
- One remaining item from the same Advisor scan is **not** code-fixable: "Leaked Password Protection" (rejects known-breached passwords via HaveIBeenPwned at signup) is gated behind Supabase's Pro plan — confirmed by attempting to enable it on the dashboard and receiving an explicit plan-tier error. Documented as an accepted low-severity gap rather than upgrading purely for this one feature.

**Smaller UI Fixes:**

- US Daily Picks displayed its generation time in ET (`"8:30 AM ET"`) while IN showed IST — converted to the actual cron trigger's IST equivalent (`"6:00 PM IST"`, derived from the fixed `12:30 UTC` GitHub Actions schedule, not affected by US daylight saving) so both markets show one consistent timezone.
- Switching the IN/US toggle on Daily Picks visibly shifted the header's button cluster — root cause was a missing `placeholderData: keepPreviousData` on the page's query, so `data` briefly went `undefined` mid-switch, collapsing the "Updated X ago" badge and regime panel. Fixed, and proactively applied the same fix to Multibagger's two queries since it has the identical toggle pattern.
- Multibagger Screen's IN/US toggle was moved above the title per one round of feedback, then reverted back onto the same line as the title (matching Daily Picks/Heatmap/Screener's standard layout) per a follow-up correction.
- Documented the Signal badge color grading (BUY's three confidence-graded shades, HOLD vs. REJECTED distinction, the dash meaning "queued in a staggered batch" not "no signal") in a new §18a, plus a glossary for Invested/Unr. P&L/Near stop loss/Mkt Price (last close when closed)/Composite Score/Multibagger Verdict-Shortlisted/REJECTED reasons — added after a user asked how to read a screenshot of mixed badge colors and confirmed there was no existing reference for it.

**Quality Gate Refined for Order-Book-Driven Businesses (founder-supplied framework):**

- A real external "multibagger" list cross-checked against our predictions found 3 of 9 stocks (HFCL, ideaForge, Apollo Micro Systems — all defense/telecom-equipment companies) hard-REJECTED, every one for the identical reason: "Non-positive operating cash flows." Real pattern, not noise — order-book-driven businesses carry large unbilled receivables against growing government contracts, showing as negative OCF while genuinely healthy.
- Founder supplied the exact framework to fix it: a large order book should be a *multiplier* on an already-decent business, not a substitute for business quality (Strong Buy needs order book + ROCE>15% + positive OCF + low debt; Potential Turnaround is order book + weak ROCE/margins but improving execution; Avoid is large order book + high debt + negative cash flow + poor execution).
- Order Book/Revenue ratio itself isn't available from any source we have (not screener.in, not yfinance) — implemented the closest proxies instead: revenue growth (>15%) as a stand-in for order-book execution, ROCE, debt-to-equity, and the already-computed `eps_trend` signal as the "improving execution" proxy. `_quality_gate()` no longer hard-rejects on negative OCF alone — requires strong revenue growth AND contained leverage (D/E<150) AND (decent ROCE OR improving earnings trend) before granting the exception; high debt or no growth still correctly triggers the "Avoid" rejection.
- Verified against the real previously-rejected stocks: HFCL and Apollo Micro now score BUY (confidence 30%/45% — explicitly demoted further by the risk/reward and valuation flags already in place, not blindly confident), ideaForge lands at BUY with confidence 18% (the unfavorable risk/reward and negative-ROE flags both fire). None show as a confident green badge — the gate change only controls whether a stock gets scored at all, not whether it's called a strong buy.

**"Elite Strong Buy" Tier Added to Multibagger (founder-supplied formula):**

- Founder's literal formula: ROCE>15% AND Debt/Equity<50% AND OCF>0 AND Sales growth>10% AND Order Book/Revenue>3x. The first four were already individually scraped; Order Book/Revenue dropped again for the same data-availability reason as above — explicitly omitted rather than faked.
- This overlaps almost entirely with checks already in the scorecard (ROCE>15%, D/E<50%, OCF>0 are exact matches) — what's new is treating all four as one all-must-pass rule that elevates the verdict, rather than just contributing to an overall score percentage. A stock could clear the existing "strong_buy" threshold on aggregate score while being mediocre on any one of the four specifically; this tier requires every one individually.
- Promotes `strong_buy`/`watchlist` to `elite_strong_buy` when all four pass; never overrides `avoid`/`watch` — the existing Anti-Loss red-flag ceiling stays a hard cap by design.
- **Bonus fix found while wiring this in:** the frontend's verdict→color map only had entries for `strong_buy`/`watchlist`/`avoid` — `watch` (a value the backend has always been able to return for exactly one red flag) had no entry, so `v.color` on the resulting `undefined` would have crashed the entire results table whenever it occurred. Added both `watch` and `elite_strong_buy` to the map, and widened the verdict type in `api.ts` to match what the backend can actually return.

**Stock Detail Page Crash on Hard-Rejected Horizons — Found and Fixed:**

- Reported live: a stock that hard-rejects at medium/long horizon (e.g. ATLANTAA, correctly rejected for genuinely weak ROCE 1.9%/13% revenue growth — below the new turnaround-exception thresholds above) crashed the entire stock detail page with a generic browser error when switching to that horizon tab, while Short Term (not rejected) worked fine.
- Root cause: a rejected prediction returns a minimal payload (only `symbol/market/horizon/signal/rejection_reasons/confidence/current_price`) — every other field (`reasoning`, `trade_levels`, `technical`, etc.) is `undefined`. The page's render branch called `prediction.reasoning.slice(0, 4).map(...)` with no optional chaining, the one field access in the file that didn't have one. The branch guard (`prediction?.signal ? (...) : ...`) also didn't help, since `"REJECTED"` is itself a truthy string.
- Fixed by adding an explicit branch for `signal === "REJECTED"` before the generic branch, rendering the actual `rejection_reasons` in a plain message instead of crashing. This affects any stock hard-rejected at any horizon, not just ATLANTAA.

**Portfolio Signal Loading — Measured, Then Sped Up:**

- Investigated "Signal column still slow" with direct load testing against production: 5 fresh predictions resolve in 9s, 8 in 12s — confirming the backend itself wasn't the remaining bottleneck (predictions genuinely take a few seconds each — a real multi-step pipeline, not reducible the way the earlier quote event-loop bugs were).
- Two real, fixable inefficiencies found stacked on top of that real compute time: (1) `fetchPrediction`'s poll loop waited a flat 5s between every attempt regardless of how close the prediction actually was to finishing — now polls every 2s for the first 4 attempts, falling back to the server's suggested 5s afterward; (2) the signal batch size was 6, set cautiously before today's event-loop fixes shipped — raised to 8 given the load test showed no degradation, cutting a 38-holding portfolio's sequential batches from 7 down to 5. Both compound; benefits the stock detail page too, since it shares `fetchPrediction` with Portfolio.

**Daily Picks: Stale Entry Zones Now Flagged, Not Silently Shown:**

- User-reported, confirmed live: a Daily Picks card's entry zone/target/stop differed noticeably from the same stock's live stock-detail-page numbers. Not a calculation bug — Daily Picks is a frozen snapshot from generation time (once or twice daily); the stock had genuinely moved ~6.6% since. Every number on the card is computed against the generation-time price with zero live refresh and no staleness indicator at the card level.
- Added a live quote fetch per Daily Picks card (quotes proven safe at far higher concurrency than the max 6 cards rendered at once here, since only one horizon tab renders at a time). When the live price has moved outside the stated entry zone, the Entry Zone box now shows strikethrough + "(passed)" and a card-level warning explains why, instead of presenting an already-invalidated zone as current.

**Daily Picks Copy Audit — Several Stale Claims Found and Fixed:**

- "Top 5 BUY ideas" (README ×2, landing page) — the actual selection has been top 6 since this was built, copy never caught up.
- "Every morning at 9 AM IST" (README, landing page, telegram bot docstring) — wrong for both markets; real times are 2 AM IST (IN) and 6 PM IST (US).
- picks page meta description said "from Nifty 100" — actual screen covers the full NSE universe (2,392 stocks) plus the full US universe.
- README's hosting table and live-link line still referenced Render — leftover from before the Render/Railway resolution earlier this session; updated to Railway (backend) and Supabase (database, the actual Postgres provider).

**Transparency Pass — Eliminating "Looks Contradictory But Isn't" Pairings:**

- Confirmed via a full backend search that "Confidence" and "Conviction" are the exact same single number (`prediction.confidence`) — no separate conviction calculation exists anywhere; "conviction" only appears as plain English inside reasoning-text strings. Standardized the two remaining "Conviction" labels on the stock detail page to "Confidence."
- Daily Picks' "Allocation" badge (a separate mean-variance portfolio-optimization step that runs *after* the Top 6 are selected) showing "0%" on a "Strong Buy Candidate" card read as a direct contradiction. Added a tooltip explaining it answers a different question ("where should the marginal rupee go across today's whole basket," not "how strong is this signal"), softened 0%'s color from yellow to neutral gray, and added an inline note clarifying the Signal above is computed independently.
- Extended the same audit to Portfolio, Multibagger, and Paper Trade: Portfolio's bare "Signal" header now has a tooltip clarifying it's a forward-looking call independent of your P&L/cost basis; Multibagger's page copy now explains the new Elite Strong Buy tier's all-four-must-pass requirement; Paper Trade's "Signal" column (confirmed via the schema to be frozen at trade-open time, never re-evaluated) was renamed to "Entry Signal" with a tooltip pointing to the stock's own page for the current call.

**Critical: log_prediction Silently Failing for Nearly Every Symbol:**

- Spotted live in Railway logs (checking on an unrelated question): `"Failed to log prediction for HFCL: bind message supplies 31 parameters, but prepared statement requires 1"` — repeating for nearly every symbol in a single 15-minute window.
- This is the exact error pattern traced earlier today during the Render/Railway duplicate-deployment investigation, where it was attributed to two processes contending for the same Postgres connection pool — and at the time, assumed fixed by removing the duplicate Render deployment rather than fixed directly. That assumption was wrong: these logs are Railway alone, Render long suspended, proving the bug is standalone. Root cause: `postgres_store.py`'s connection pool was the one place in the codebase that doesn't set `prepare_threshold=None` — every other direct `psycopg.connect()` call site already has it. Supabase's transaction-mode pooler (port 6543) can hand the same underlying server connection to a different logical query between transactions; without disabling psycopg's auto-prepare behavior, a statement name minted for a small query can later collide with a different query needing far more parameters.
- This was silently undermining the IN/US learning-engine separation shipped earlier today — the IC engine and meta-model can't train on outcomes that were never successfully logged. Fixed by adding `prepare_threshold=None` to the pool's connection kwargs, matching every other connection in the codebase.
- Same log review also surfaced a benign container restart (clean shutdown/restart, almost certainly an auto-redeploy from one of the many pushes this session, not a crash) and a separate, lower-urgency finding: NSE's homepage is returning 403 on startup ("API calls may be unauthenticated") — degraded, not broken, since the existing fallback chain (Finnhub, then yfinance) covers for it.

**Paper Trade Notification System — Verified Still Active:**

- Walked through the full mechanism end-to-end on request: a background loop (`_paper_trade_notify_loop`, every 15 minutes) scans open paper trades with a target/stop set and an email on file, emails the owner via Resend when price is within 2% of (or has crossed) either level, deduped via a 6-hour cooldown per trigger. Confirmed `paper_portfolio.email` auto-refreshes on every Paper Trading page load, so no separate opt-in is needed.
- The one dependency that couldn't be verified by reading code alone — whether `RESEND_API_KEY` is actually set on Railway — was confirmed present via a dashboard screenshot. Mechanism is fully wired and should be functioning as designed; full end-to-end confirmation (an actual email arriving) would need a live test with a trade nudged near its target/stop.

### Session 8 — 2026-06-23

A long session covering a surgical bug-hunt pass across the multi-market changes, a thorough mobile/desktop CSS layout audit, and a real architecture fix (Portfolio finally syncing across devices). Documentation batching was changed mid-session from "after every push" to "once per day" per explicit founder feedback — this entry covers everything from that point through end of session in one pass instead of ~22 separate changelog entries.

**Multi-Market Scoring Bugs Found and Fixed:**

- **Sector/relative-strength scoring was India-only for every US stock.** `sector_strength_score()` looked symbols up in a static curated India-only map, so any US symbol always returned "Unknown" with a neutral score. Separately, `relative_strength_score()` always compared against Nifty 50 regardless of market — a US stock's "relative strength" was being computed against a rupee-denominated index. Fixed: US sector now comes from yfinance's own GICS `info["sector"]` field (works for any US stock, no curated list needed) compared against SPDR Select Sector ETFs vs. the S&P 500; IN behavior is byte-for-byte unchanged. Bonus: the NSE Finance sector index ticker (`^CNXFINANCE`) was found delisted/404ing on yfinance — swapped to `NIFTY_FIN_SERVICE.NS`.
- **US Top Gainers/Losers showed far fewer than 10 names each.** The live-request fallback scanned ~50 of the 340+ curated US large-cap symbols one at a time via Finnhub (free tier, no bulk endpoint, hard timeout budget) — ~85% of the universe was never checked. Fixed by adding a periodic background job (`_us_movers_refresh_loop`, every 3 min) that scans the full universe via one bulk `yf.download()` call (~30s, fine for a background job, too slow for a live request) and pre-warms the cache.
- **Invite-link handling had two more gaps** beyond the earlier fix: it only processed hashes with `type=invite`/`type=recovery` exactly (re-inviting an email with an existing partial `auth.users` row can produce a different `type=`), and a reused/expired link (`#error=...` instead of tokens) gave zero indication anything was wrong. Both fixed — any hash with valid tokens is now processed regardless of type, and an error hash redirects to `/login?notice=invite_expired` with a clear message.

**Alerts System Hardened to Match Paper Trading's Standard:**

- **Server-side enforcement added.** Alerts previously only fired client-side via a 5s poll — closing the tab silently stopped monitoring with no backstop. New `services/price_alert_notifier.py` + `_price_alerts_check_loop` (every 90s) scans non-triggered alerts with an email on file and notifies via the same Resend account used for invites/paper-trade alerts. Kill switch: `PRICE_ALERTS_ENFORCEMENT=0` env var disables just this background check without a code change.
- **Resurrection race fixed** — delete/reset fired the API call and updated local UI state unconditionally; a failed network call left the row alive in Postgres while the screen showed it gone, and the next page load's GET would silently bring it back. Now awaits the request first, only committing the local change on success (same bug class found and fixed independently in Portfolio later this session).
- **Screener filter unit mismatch + silent failures**: `min_roe` was compared against yfinance's raw ROE fraction while the output field is `*100`-scaled (not wired to the frontend yet, but a landmine); bare `except: pass` replaced with logged skip counts.

**Surgical CSS/Layout Audit (mobile + desktop):**

- **Stock detail page header** didn't stack on mobile — the AI signal panel's 140px minimum width left almost no room for price/badges on a ~375px phone. Now stacks vertically below `sm:`.
- **Two tables (Watchlist, Alerts) were missing `overflow-x-auto`** wrappers that every other table in the app already had — verified all `<table>` usages app-wide to confirm these were the only gaps.
- **Daily Picks and Dashboard header rows overflowed past the screen edge** on narrower viewports — both had an outer row that correctly wrapped, but an *inner* row (toggle + buttons + status badge) with no `flex-wrap`/`overflow-x-auto` of its own, so 3+ children forced onto one unbreakable line wider than the viewport. Fixed both, plus added a second safety layer (`flex-wrap` on the parent too) since in some browsers a child's `overflow-x-auto` doesn't get factored into the parent's own wrap decision.
- **Desktop navbar market-status block overflowed at laptop widths.** `MarketStatusInline` (NSE/NYSE/Crypto status + "Opens/Closes at..." text) was `shrink-0` with no width cap — US/Crypto's longer status text pushed the whole navbar row past the viewport right at the `lg:` breakpoint where it first appears. Since this lives in the shared root layout, it affected every page at once. Capped to `max-w-[42vw]` with its own internal scroll.
- **A genuine regression, self-corrected:** added `overflow-x: hidden` on `html`/`body` as a "safety net" against page-wide horizontal scroll — this didn't fix anything, it just removed the user's ability to scroll right to reach content that was still overflowing (worse, not better). Reverted, then found and fixed the actual overflow sources instead.
- **Missing header icons**: Market Overview, Market Heatmap, and Stock Screener were the only 3 of 9 main pages with no icon before the title (every other page already had one) — added `LayoutDashboard`, `Flame`, `Filter` respectively.
- **Market toggle styling unified** across Daily Picks, Dashboard, Heatmap, and Paper Trading (previously: compact segmented pills on some pages, larger individually-bordered pills with full country names on others) — all now use the same compact segmented-control style. Also standardized the three form-field market selectors (Alerts, Portfolio, Backtest).
- **Unresolved, flagged not fixed**: a navbar avatar/search-bar overlap reported on both Android and iOS, in what looks like a constrained in-app webview (Safari View Controller / Chrome Custom Tabs) rather than the full browser app — ruled out backdrop-blur compositing, global CSS, viewport meta conflicts, and the flexbox math itself (which cannot produce real box overlap as written) across multiple passes. Locked `maximumScale: 1` on the viewport as a defensive measure. Asked the founder to confirm whether it reproduces in the full standalone browser before further investigation, since computer-use/browser-automation tools were unavailable in this environment to verify directly.

**Predictive Search Added to Portfolio and Alerts:**

- Both pages had a bare text `<input>` for entering a symbol — no autocomplete, unlike the global search bar and Watchlist's add-stock field. Extracted the universe-loading + local-matching logic (previously duplicated between `SearchBar.tsx` and `watchlist/page.tsx`) into a shared `useStockSearch` hook + `StockSymbolField` component; picking a suggestion also auto-syncs the page's IN/US toggle to the selected stock's actual market.
- **Known gap, deferred to next session**: the autocomplete only matches against a static curated `stock_universe.json` (~1,585 US tickers), not the full market — confirmed RKT (Rocket Companies) and UUUU (Energy Fuels) are missing. Manual entry + Enter still works as a fallback even without a match.

**Market Selection Now Persists:**

- Every page with a market toggle (Daily Picks, Dashboard, Screener, Backtest, Alerts, Portfolio, Heatmap, Paper Trading) reset to "IN" on every refresh, with no memory of the user's choice. New `useMarketPreference` hook persists to one shared `localStorage` key across all 8 pages — picking "US" on one page carries the preference to the others, not just the same page on reload. Switched the internal read from `useEffect` to `useLayoutEffect` to eliminate a visible flash of the wrong market before the stored value applied.

**Watchlist Split by Market:**

- Was rendering all stocks (IN + US mixed) in one combined table with a Market column — inconsistent with Portfolio and Paper Trading, which both already group by market. Split into separate "🇮🇳 Indian Stocks" / "🇺🇸 US Stocks" sections, same pattern as the other two pages.

**Portfolio: Inline Edit + Cross-Device Sync (the big one):**

- **Inline edit added** for Qty and Avg Buy — previously the only way to fix a typo'd value was deleting and re-adding the holding. Pencil icon toggles to editable inputs, confirmed with a checkmark/Enter or cancelled with X/Escape (same pattern as Paper Trading's open-positions table).
- **Root architecture gap found and fixed**: Portfolio was the one feature still stored entirely in the browser's `localStorage` — Watchlist, Alerts, and Paper Trading all already synced per-user through Postgres, but a holding added on desktop was invisible on any other device for the same account, since nothing was ever sent to a server. New `portfolio_holdings` table + full CRUD endpoints (`backend/api/routers/portfolio.py`), mirroring the `_ensure_table()`-on-startup pattern already used by Alerts. `localStorage` is now just a fast-access cache; on first load after this shipped, any holdings already sitting in local storage get migrated up to the server automatically so existing users don't lose them. Delete/edit also await the backend call before updating local state, same fix as the Alerts resurrection bug above.

### Session 7 — 2026-06-22

**Dashboard Tab Renamed "Gold & Silver" → "Commodities":**

- More professional label, and leaves room to add more commodity instruments later (e.g. oil, platinum) without relabeling the tab again. `COMMODITY` internal key/type was already generic — only the two display strings (tab label, section heading) needed updating.

**Crypto Fundamentals Check + Dead-End Tab Removal:**

- Verified crypto's actual signal engine (`crypto_engine.py`) already correctly skips fundamentals entirely — technical + fear/greed (volatility proxy) + on-chain proxy (volume-based) only, explicitly documented as "no fundamentals." (An initial direct test of `PredictionEngine.predict()` for a CRYPTO market was misleading — that's dead code for crypto in production; `predictions.py`'s router actually dispatches CRYPTO to `predict_crypto()` instead.)
- The real issue was UI, not logic: the **Fundamentals tab** (screener.in data) rendered unconditionally on every stock detail page, but screener.in only covers Indian companies — for US, Crypto, and the tracking-only commodity ETFs it could only ever show "available for Indian (NSE) stocks only." Filtered it out of `HORIZON_TABS` for any market other than IN, since there's no scenario where it shows real data otherwise.

**Gold & Silver Dashboard Tab:**

- GLD/SLV/GOLDBEES/SILVERBEES were only reachable via search before this — no discoverable spot on the app's main landing page. Added a 4th market tab on the Dashboard (`frontend/src/app/dashboard/page.tsx`) next to India/USA/Crypto, showing live price + day change for all four as simple cards, same pattern as the existing Crypto tab's grid.
- No index bar (no natural index for 2 commodities — same simplification already made for Crypto) and no Quick Access/horizon-info sections (not meaningful for a fixed 4-symbol list) — just the price cards plus a note that these are tracking-only with no AI signal.

**Gold/Silver Tracking Support — Fixed Fabricated Analysis:**

- **Caught live:** after adding GLD/SLV/GOLDBEES/SILVERBEES to the universe, visiting their stock detail page produced a confident SELL signal whose bear case claimed *"Underperforming the Nifty 50 benchmark"* for a US gold ETF, plus invented valuation/earnings claims — despite `confidence_breakdown.data_completeness` being `0`. The fundamentals/sentiment/quality pipeline was fabricating plausible-sounding analysis from data that didn't exist.
- **Fixed in `PredictionEngine.predict()`:** added an early-return branch for `TRACKING_ONLY_SYMBOLS` (`stock_universe.py`) that computes the signal from real technical indicators only (RSI/MACD/EMA/ADX/volume — legitimate for any price series) and skips the entire fundamentals/sentiment/quality/bull-bear-case pipeline, returning a `tracking_only: true` flag and an honest explanatory note instead.
- **Frontend:** stock detail page now shows a clear "price-tracking instrument" banner and hides Bull/Bear Case, Factor Attribution, and Academic Quality Signals when `tracking_only` is set.
- **Bonus fix:** this surfaced two latent null-pointer crashes (`prediction.fundamental_score.score` / `sentiment_score.score` accessed without a null guard) that would have thrown for *any* symbol with missing fundamental data, not just the new commodity ETFs — fixed with optional chaining.
- Added `GLD`/`SLV` (US) and `GOLDBEES`/`SILVERBEES` (India) ETF tickers to both `backend/services/stock_universe.py` and `frontend/public/stock_universe.json`, so they're searchable and usable in Watchlist, Alerts, and the stock detail page (price + chart) via existing infra.
- **Deliberately scoped to tracking only** — no AI BUY/SELL signal, no entry in Daily Picks. Fundamental factors (P/E, ROE, Piotroski score) that drive equity scoring don't meaningfully apply to a commodity ETF; full signal support would need a separate technical+macro-only model, considered and explicitly deferred rather than built half-heartedly into the existing equity-scoring engine.
- Both lists are marked as manual additions outside the universe auto-generator's source (S&P/NASDAQ-100 + all NSE equities) — they'll need to be re-added if `scripts/generate_stock_universe.py` is ever re-run.

**Full Indian Holiday Calendar + Muhurat Trading:**

- **Gaps found:** the frontend's `marketHours.ts` was missing several *fixed-date* NSE holidays (Ambedkar Jayanti Apr 14, Maharashtra Day May 1, Christmas Dec 25, Good Friday) — it already had the Easter-algorithm machinery for US holidays but wasn't applying it to India. The lunar/regional holiday list (`NSE_EXTRA_HOLIDAYS`) was completely empty. The backend's market-hours check (duplicated in `paper_trading.py` and `screener_service.py`) had **zero** holiday awareness — not even the fixed ones.
- **Fixed:** added the missing fixed holidays (including Good Friday via the existing Easter computation) to `nseMarketHolidays()`; populated `NSE_EXTRA_HOLIDAYS` with the verified 2026 list (Holi, Ram Navami, Mahavir Jayanti, Bakri Eid, Moharram, Ganesh Chaturthi, Dussehra, Diwali, Guru Nanak Jayanti) sourced from NSE's official circular via Zerodha's mirror; wired it into the `nextEvent()` lookahead too (previously ignored).
- **Muhurat trading** — the special ~1hr evening session NSE/BSE run on Diwali Laxmi Pujan despite that date otherwise being a holiday — is now modeled via a `MUHURAT_SESSIONS` override list. 2026 falls on Sunday Nov 8; exact timing is a **placeholder** until NSE publishes the official window (~2 weeks before Diwali) — needs a follow-up update closer to that date.
- **Backend/frontend sync:** extracted a shared `backend/services/market_hours.py` mirroring the frontend logic exactly (same Easter computation, same 2026 extra-holiday list) and pointed both `paper_trading.py` and `screener_service.py` at it, removing two inline duplicates that were drifting out of sync with the frontend and with each other.
- **Operational note:** `NSE_EXTRA_HOLIDAYS` (both the TS and Python copies) needs a manual refresh every December for the following year, from NSE's official circular at nseindia.com/resources/exchange-communication-holidays.

**Two Manuals Added:**

- `StockSense360_Technical_Handbook.docx` — internal architecture/AI-engine/infra reference for engineers and the founder.
- `StockSense360_User_Guide.docx` — end-user/investor-facing walkthrough of every page, with 16 real screenshots captured from production (also doubles as a visual confirmation that this session's fixes shipped correctly).
- Both manuals later updated again in this session to reflect the market-hours gating, holiday calendar, Commodities tab, and tracking-only-instrument changes below (the manuals had drifted behind the markdown/PDF changelog).

**US Market Support Added to Daily Picks:**

- **Root question:** Daily Picks only ever covered NSE India — not a technical limitation, just never wired up. `PredictionEngine.predict()` already accepted a `market` parameter and worked correctly for US stocks (used on every US stock detail page); the daily-picks batch job (`daily_picks.py`) was the only piece hardcoded to `market="IN"`.
- **Backend:** generalized the bulk momentum screener (`_bulk_screen_nse` → `_bulk_screen(market, ...)`), the mcap-floor screener (NSE exchange code `NSI` vs US exchange codes `NMS`/`NYQ`/`NGM`/`ASE`/`PCX`), the regime-detection proxy ticker (`RELIANCE` → `AAPL` for US), the per-stock prediction call, and the disk/Postgres cache — all now take a `market` argument instead of assuming India. Added a 100-ticker US mega-cap fallback list (mirrors the existing Nifty 100 fallback role) for when the live screener call fails.
- **Postgres:** added a `market` column to `daily_picks_cache` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) so `save_picks_to_db`/`load_picks_from_db` keep independent history per market instead of one market overwriting the other's cache row.
- **API:** `/api/picks/daily`, `/api/picks/status`, and `/api/picks/generate` all accept a `market=IN|US` query param (default `IN`, validated against an allow-list). The `_generating`/`_last_error` module state in `daily_picks.py` changed from a single bool/string to a dict keyed by market, so an IN run and a US run can't trip each other's "already running" guard.
- **Cron:** GitHub Actions workflow (`daily_picks.yml`) now runs two scheduled jobs — India at 20:30 UTC (2 AM IST, unchanged) and US at 12:30 UTC (~8:30 AM ET, comfortably before the 9:30 AM ET open) — each calling `/api/picks/generate` with its own `market` param.
- **Frontend:** Daily Picks page (`/picks`) gets an IN/US toggle next to the existing horizon tabs. Switching markets changes the query key (separate cache per market), currency symbol (₹/$), number locale (`en-IN`/`en-US`), display timezone for "Updated at," and the market passed through to the stock-detail link and the Paper Trade modal — all previously hardcoded to India.
- **Known limitation, not fixed in this pass:** the live picks-performance tracker (`/api/picks/performance`) and score-snapshot history key only on `(symbol, horizon)`, with no market column — if a US ticker and an NSE ticker ever share the same symbol, their historical performance rows could blend. Low real-world risk (ticker collisions across the two universes are rare) but worth a follow-up if it's ever seen in practice.

**Separate USD Ledger for US Paper Trading:**

- **Bug caught during the US Daily Picks review:** paper trading had exactly one `cash` column on `paper_portfolio`, denominated in ₹10,00,000, shared across every trade regardless of market. Buying a $100 US stock would have deducted "100" from that same rupee pool — silently treating dollars as rupees, with no currency conversion or separation at all.
- **Fixed:** added a `cash_usd` column (`ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS cash_usd DOUBLE PRECISION NOT NULL DEFAULT 10000.0` — $10,000 starting balance, not a currency-converted equivalent of the ₹10,00,000 IN balance, just a separate round-number virtual pool). `paper_trading.py`'s buy/sell/edit/reset endpoints now resolve which cash column to debit/credit based on the trade's `market` field instead of always touching `cash`.
- **Reset endpoint** now takes an optional `market=IN|US|ALL` param (defaults to `ALL` for backward compatibility) so a user can wipe just one market's trades/cash without touching the other.
- **Frontend:** the Paper Trading page (`/paper-trading`) gets an IN/US toggle (same pattern as Daily Picks and Dashboard). Cash, invested amount, unrealized/realized P&L, and the reset confirmation are all scoped to the selected market — open/closed trades are filtered by market before any totals are summed, so dollars and rupees are never added together.
- **Follow-up same session:** bumped the US starting balance from $10,000 to **$100,000** per founder request, with a one-time Postgres backfill (`UPDATE paper_portfolio SET cash_usd = 100000.0 WHERE cash_usd = 10000.0`) so the handful of portfolios created in the brief window the old default was live also get the new amount.

**US Daily Picks Catch-Up Recovery (matching India's):**

- **Root question:** with two scheduled cron runs now (IN at 2 AM IST, US at ~8:30 AM ET), does a missed/crashed run recover on its own for both markets? It didn't — the existing catch-up safety net in `api/main.py` (`_catchup_picks`, fires on server startup, regenerates today's picks if the scheduled cron never completed) was IN-only.
- **Fixed:** generalized `_catchup_picks()` to take `(market, tz_offset_hours, trigger_hour, settle_secs)` instead of hardcoding IST/2 AM, and scheduled a second instance for US — checks after 9 AM (fixed UTC-5 offset, matching the same non-DST-aware simplification already used by `picks_generated_today("US")`) on weekdays, regenerates if no US picks exist for today yet.
- **Bonus fix:** the validation catch-up task (`catchup_task`) was missing from the FastAPI lifespan's shutdown cancellation list — a pre-existing leak unrelated to picks, fixed while touching the same block.

**US Market-Hours DST Bug Fixed:**

- **Found while explaining the IN/US holiday-handling comparison:** `backend/services/market_hours.py` computed Eastern Time using a hardcoded `UTC-4` offset, with its own comment admitting *"approximation, ignores EST/EDT switch"* — while the frontend's `marketHours.ts` correctly used the IANA `America/New_York` zone (auto-adjusting for daylight saving via `Intl`). The two only agreed during EDT (roughly mid-Mar–early Nov); during EST months the backend's `is_market_open("US")` — which gates paper trading buy/sell orders — was off by an hour from the real market clock and from what the frontend displayed.
- **Fixed:** switched `market_hours.py`, `daily_picks.py`'s `picks_generated_today("US")`, and the US Daily Picks catch-up scheduler in `main.py` to `zoneinfo.ZoneInfo("America/New_York")` instead of a fixed offset — all three (and the frontend) now agree year-round. Verified directly: `ZoneInfo` correctly returns `-05:00` for a January date and `-04:00` for a July date.
- India's side was never affected — IST has no daylight-saving rule, so a fixed `+05:30` offset is always correct.

**Market-Hours Gating for Paper Trading:**

- **Root issue:** Buy/Sell could execute instantly at any time of day using a stale last-close quote when the market was closed — unrealistic (a real market can gap through that exact price by next open) and not a good look for a tool positioning itself as a serious research platform.
- **Frontend:** `PaperTradeModal` now checks `getMarketStatus(market)` (the same utility already driving the navbar's market-status pills), shows a "{market} market is closed — opens at ..." banner, and disables the submit button while closed.
- **Backend:** mirrored the same check in `paper_trading.py`'s `/buy` and `/sell` endpoints (a local `_is_market_open()`, same pattern already used in `screener_service.py`) — a direct API call would otherwise bypass the frontend-only gate entirely.

**Paper Trade Target/Stop-Loss Proximity Notifications:**

- **New `services/trade_notifier.py`** — a background loop (every 15 min, registered in `main.py`) scans all OPEN paper trades with a `target_price` or `stop_loss` set, fetches the live quote, and emails the owner once price is within 2% of (or has crossed) either level. Each trigger is deduped via `target_notified_at`/`stop_notified_at` columns + a 6-hour cooldown, so a price hovering near the line doesn't spam the same email repeatedly.
- **Sends via Resend's HTTP API directly** (not through Supabase's SMTP) from `alerts@stocksense360.com` — a distinct sender from `invites@` so users can tell notification types apart. Requires a `RESEND_API_KEY` env var on the Railway backend service (reuses the same Resend account/API key set up for invite emails).
- **`paper_portfolio.email` column** stores the user's email captured from the frontend (`useAuth().user.email`) on every `/buy` and `/portfolio` call — no Supabase admin API call needed on the backend.
- **Browser popup notifications** — `OpenTradeRow` fires a `Notification()` once per (trade, kind) per session when price nears target/stop, gated on `Notification.permission === "granted"`. New "Enable Notifications" button added to the Paper Trading page header (browser permission prompts require a user gesture).

**Daily Picks Generation Crash Fix:**

- **Root cause found** — the 2 AM IST daily picks cron run crashed every single day on the short-term overbought-RSI quality gate in `daily_picks.py`: `" ".join(r.get("reasoning", []))` assumed `reasoning` was a list of plain strings, but it's actually a list of structured dicts (`{"indicator":..., "reason":...}`) built in `prediction_engine.py` for the factor-breakdown UI. The crash threw `TypeError: sequence item 0: expected str instance, dict found`, caught by the top-level crash handler, which silently saved an empty fallback payload (`{"short": [], "medium": [], "long": []}` + an `error` field) instead of real picks — so the Daily Picks page showed either a stale "Generating picks…" spinner (during catch-up retries) or "No BUY signals found today" (a misleading message; it wasn't that no signals existed, the run never got far enough to find any).
- **Fixed:** extract `item.get("reason", "")` from each dict before joining, with a defensive fallback for plain strings.
- **Verified live** via `/api/picks/status` — confirmed the crashed run's `error` field, then watched a fresh catch-up-triggered run complete with `last_error: null` after the fix deployed.

**Daily Picks Target-Price / Upside Methodology (clarified, not a bug):**

- Confirmed via `prediction_engine.py::_estimate_target()`: every **BUY** signal's target price has a hard-coded floor relative to current price — medium-horizon floors at `price * 1.05`, long-horizon at `price * 1.15`, short-horizon via an ATR-based move. This means **every BUY pick is guaranteed to show positive upside by design**, not because each stock's underlying analyst-target/trend math happened to be positive. If several BUY picks' natural projections fall below the floor, they'll cluster at exactly the same floored upside % (e.g. several stocks all showing "+5.0% upside" identically) — that's expected behavior, not a calculation bug, but worth knowing when interpreting the displayed upside numbers as "genuine" per-stock projections.

**Validation Universe Bug — India Results Were Permanently Shadowed:**

- **Root cause found** — `val_runs` had no `universe` column. `get_latest_results()` / `get_per_stock_results()` only filtered by `horizon`, picking whichever run had the highest `id` (i.e. most recent). Since the daily validation schedule always runs `nifty100 → midcap → us` in that order, the **US run is always the most recent** for any given horizon — so the Validation page always displayed US results, and India (nifty100/midcap) results, though present in the database, were never shown.
- **Fixed:** added a `universe` column to `val_runs` (Postgres: idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS`, since the table already existed in production and `CREATE TABLE IF NOT EXISTS` alone is a no-op on existing tables; SQLite: `ALTER TABLE` wrapped to ignore "duplicate column" on repeat init). `get_latest_results()`/`get_per_stock_results()` now accept and filter by `universe` (default `nifty100`).
- **API:** `/api/validation/results`, `/results/stocks`, `/results/stock/{symbol}` all now accept a `universe` query param.
- **Frontend:** Validation page now has a Nifty 100 / Midcap / US selector above the horizon tabs. All "vs Nifty" benchmark text is now dynamic — shows "vs S&P 500" when viewing the US universe instead of incorrectly saying Nifty for US data.

**Railway Redeploy Scoping:**

- **Root cause found** — Railway redeployed the backend service on every push to `main`, regardless of which files changed, including pure frontend and documentation commits. Each restart re-runs the startup "catch-up" check in `main.py`, which can kick off a brand-new ~10-15 minute full picks-generation run if it lands in a window before the day's legitimate 2 AM IST cron run has finished persisting — producing duplicate/wasted runs and a confusing "Generating picks…" spinner shown against an already-complete day's data.
- **Fixed:** added `railway.json` at repo root with `build.watchPatterns: ["backend/**"]`, so the backend service only redeploys when backend code actually changes. Frontend and docs-only pushes no longer restart it.

**Invite Registration Fix:**

- **Root cause found** — invite links (and password-reset links) authenticated the user for one Supabase session via magic-link code exchange, then dropped them straight onto `/accept-terms`. The user never set a password. On their next visit, `/login` only offers email + password sign-in with no Sign Up option (by design — invite-only app) — so an invited user with no password had no way back in.
- **New `/auth/set-password` page** — shown right after the invite/reset link authenticates the session; lets the user create a real password (min 6 chars, confirm match) via `supabase.auth.updateUser({ password })`, then continues to `/accept-terms`. Shows a clear "link expired" message if there's no active session.
- **`/auth/callback/route.ts` updated** — now redirects to `/auth/set-password?next=/accept-terms` instead of straight to `/accept-terms`. This covers both the invite flow and the forgot-password flow (previously forgot-password also had no way to actually set the new password after clicking the reset link).
- **Login page footer clarified** — explains invited users should look for an invite email with a link rather than expecting a Sign Up form.
- **Operational note:** Supabase Auth → URL Configuration must have `<site-url>/auth/callback` in the allowed Redirect URLs list for invite/reset links to work at all. If invites still fail after this fix, check that setting in the Supabase dashboard.
- **Production domain config fixed in Supabase** — Site URL updated from the default `*.vercel.app` to `https://stocksense360.com`; Redirect URLs now include `stocksense360.com`, `stocksense360.in`, `www.stocksense360.in`, and the vercel.app fallback, each with `/auth/callback`.
- **Second root cause found** — Supabase's dashboard **"Invite user"** button (and password-reset emails) don't support a custom redirect target; they always send the user to the bare **Site URL root** using the older **implicit/hash-based** flow: `https://stocksense360.com/#access_token=...&type=invite`. Hash fragments never reach the server, so the server-side `/auth/callback/route.ts` (which only handles `?code=...`) never even saw these — the user landed authenticated-but-unnoticed on the public homepage with no path forward.
- **`InviteHashRedirect` added to `providers.tsx`** — runs on every page load app-wide; checks `window.location.hash` for `access_token` + `type=invite`/`type=recovery`.
- **Third root cause found** — `@supabase/ssr`'s `createBrowserClient` (used in `lib/supabase.ts`) does **not** auto-detect/establish a session from hash-fragment tokens the way the classic `supabase-js` client does (no `detectSessionInUrl`). The first version of `InviteHashRedirect` only checked for the hash and redirected to `/auth/set-password` — it never actually called `setSession()`, so the page found no real session and would show "link expired" even on a valid, unused invite token.
- **Fixed:** `InviteHashRedirect` now parses `access_token` + `refresh_token` out of the hash with `URLSearchParams` and calls `supabase.auth.setSession({ access_token, refresh_token })` before navigating to `/auth/set-password?next=/accept-terms`.
- **Operational note — invite tokens are single-use:** clicking an invite/reset link consumes the token at Supabase's `/verify` endpoint regardless of whether the app does anything useful with the result. Any invite clicked before this fix shipped needs a **fresh invite resent** — the same link won't work twice.

**Branded Invite Emails (Custom SMTP):**

- **Custom SMTP via Resend** configured in Supabase → Authentication → Emails → SMTP Settings, sending from `invites@stocksense360.com` instead of Supabase's shared default sender — fixes deliverability and removes the "looks like spam" concern with invite emails.
- **Domain verification:** `stocksense360.com` added to Resend with DKIM (TXT), MX, and SPF (TXT) records added in GoDaddy DNS. Domain-level status can lag behind individual record checkmarks — re-trigger verification from the Resend domain detail page if status shows "Not Started" despite green record checkmarks.
- **Branded HTML invite template** (`supabase_invite_email_template.html` in repo root) pasted into Supabase's "Invite user" email template — dark themed, StockSense360 logo, "Team StockSense360" sender framing (no individual name), clear one-time-link messaging.
- **Diagnosing SMTP failures:** Supabase's "Failed to invite user" toast doesn't show the real cause — query `auth_logs` in Logs → Explorer (`select cast(timestamp as datetime) as timestamp, event_message, metadata from auth_logs order by timestamp desc limit 10`) to see the actual GoTrue/SMTP error (e.g. domain not verified, bad credentials).

**Per-User Terms Cookie Bug:**

- **Root cause found** — the `ss_terms=v1.0` cookie set after accepting the Terms of Use disclaimer was **not scoped to a specific user** (`path=/` with a fixed name). Any user authenticating in a browser that had *previously* accepted terms as a *different* account would see the cookie, skip `/accept-terms` entirely, and land straight on `/dashboard` — never asked for name/mobile/country or shown the legal disclaimer.
- **Fixed in two places:** `accept-terms/page.tsx` (both the read-check and the write-after-accept) and `useAuthGuard.ts` (the app-wide route guard) now use a cookie scoped per user: `ss_terms_${user.id}=v1.0`. The guard fix matters more — it could previously let a user bypass the disclaimer-acceptance redirect entirely on a shared browser, not just skip the profile form.

### Session 6 — 2026-06-20

**User Feedback System:**

- **Signal thumbs up/down** — users can rate each AI signal (BUY/HOLD/SELL) directly on the stock detail page. Widget renders below the Paper Trade button; shows current vote highlighted in bull/bear colour. Votes are upserted per `(user_id, symbol, market, horizon)` so toggling works cleanly.
- **Monthly NPS survey** — `NpsPopup` component appears globally (bottom-right) after a user has voted on at least one signal and then every 30 days thereafter. 0–10 score card + optional free-text comment. Colour-coded (green ≥9, yellow 7–8, red ≤6).
- **New backend router** (`backend/api/routers/feedback.py`) with 4 endpoints:
  - `POST /api/feedback/signal` — upsert thumbs vote
  - `GET /api/feedback/signal/{symbol}` — fetch user's existing vote
  - `GET /api/feedback/signal/summary/{symbol}` — aggregate approval % across all users
  - `POST /api/feedback/nps` — submit NPS score + comment
  - `GET /api/feedback/nps/due` — returns `{due: bool}` based on 30-day cadence
- **DB schema additions** (already in `postgres_store.py` SCHEMA_SQL):
  - `signal_feedback` table: per-user signal votes with UNIQUE constraint and ON CONFLICT upsert
  - `nps_responses` table: per-user NPS scores with timestamps for 30-day cadence check

**Look-ahead Bias Fix (Validation Engine):**

- `_backtest_stock()` now recomputes indicators on a rolling window (`df.iloc[:i+1]`) at each signal date instead of on the full historical DataFrame. This eliminates future-price leakage into EMA-200, MACD, and OBV at time t.
- `MIN_WARMUP` raised 50 → 200 bars to ensure EMA-200 is valid before scoring begins.
- US symbols detected via `is_us` flag (`.NS` suffix no longer blindly appended).
- Validation hit rates will be modestly lower but accurately reflect real-time model performance.

**Validation Coverage Expansion:**

- Added `NSE_MIDCAP` universe (100 non-Nifty-100 NSE stocks) to `validation_engine.py`.
- Added `US_BASKET` universe (48 S&P 500 stocks spanning all GICS sectors).
- Railway cron (`_validation_schedule_loop` in `main.py`) now cycles all 3 universes (`nifty100`, `midcap`, `us`) back-to-back with 5-minute gaps between each.
- Deleted `daily_validation.yml` GitHub Action (was double-running alongside Railway cron).
- `/api/validation/run` endpoint accepts `universe` query param (`nifty100` | `midcap` | `us`).

### Session 5 — 2026-06-20

**User Identity & Persistence:**

- **All features migrated to Supabase `user_id`** — watchlist, alerts, paper trading, and StockContextMenu all previously used a hardcoded `USER_ID = "default"` or a localStorage `session_id`. Every feature is now scoped to the authenticated Supabase user UUID (`useAuth().user.id`), making data persist correctly across all browsers and devices.
- **Paper trading backend rewritten** — all 5 API endpoints migrated from `session_id` (random localStorage UUID) to `user_id`. `BuyRequest`, `SellRequest`, `EditRequest` models updated; `_ensure_portfolio()` queries by `user_id`. Old trades with `user_id = NULL` are legacy-only.
- **Dashboard watchlist fixed** — was fetching `/api/watchlist/default`; now uses `userId` from `useAuth`.

**Heatmap Expansion & Quality:**

- **India: 18 → 25 sectors** — added Healthcare, Insurance, Chemicals, Cement, Metal & Mining, Defence, Realty, Telecom, Consumer Disc, Hotels & Travel, Food & Beverage, Media & Entmt, Textiles, Agro & Chemicals, Logistics, Paints, Infra, Capital Goods, Power, EV & New Energy
- **US: 13 → 29 sectors** — added Cybersecurity, Fintech, Biotech, Med Devices, Clean Energy, EV, Consumer Stap, E-commerce, Social Media, Streaming & Media, Gaming, Airlines, Cruise & Hotels, Restaurants, Retail, Telecom, Utilities, Realty, Materials, Crypto & Blockchain
- **MAX_STOCKS raised 10 → 15** — wider coverage per sector
- **Full symbol audit** — all 353 India heatmap symbols bulk-tested via Yahoo Finance. 37 bad symbols identified; confirmed replacements applied (ATGL, ASTERDM, BAYERCROP, CANFINHOME, DALBHARAT, GUJGASLTD, ICICIPRULI, KNRCON, LEMONTREE, MFSL, MTARTECH, NH, ORIENTCEM, TEJASNET, TIPSFILMS, VTL, VIJAYA, VINATIORGA, WAAREEENER, WELSPUNLIV, ZENSARTECH, ETERNAL). Symbols with no Yahoo Finance listing removed (SPICEJET, GREENKO, HEXAWARE, KEYSTONE, etc.)
- **ZOMATO → ETERNAL** — company rebranded to Eternal on NSE
- **NSE `SECTOR_TO_NSE_INDEX` expanded** — 13 new sector → NSE index mappings added for primary data sourcing via NSE APIs

**UX Fixes:**

- **Landing page auth confusion fixed** — page now detects login state via `useAuth`; CTA switches from "Sign In" to "Go to Dashboard" when logged in; bottom CTA shows user email
- **Loading status badge** added to Market Overview (dashboard) and Heatmap — three states: blue "Fetching…" (first load), yellow "Refreshing…" (background poll), green wifi "Updated HH:MM:SS" (live)
- **M%26M / URL-encoding fix** — NSE symbols with `&` (M&M, M&MFIN) were displaying as `M%26M` in the stock page header. Fixed with `decodeURIComponent` on the stock page params and `encodeURIComponent` when building `/stock/` URLs in heatmap, context menu, and picks pages
- **Paper Trade modal unblocked** — Buy button was disabled until `fetchPrediction` completed (10–30s). Prediction now runs in background for stop loss/target suggestions only; button is immediately available

**Infrastructure:**

- **Migrated backend from Render → Railway Hobby** ($5/month, always-on, no cold starts)
- **`PICKS_CANDIDATES=751`** set in Railway environment — expands the momentum screen candidate pool

### Session 4 — 2026-06-19

**Forensic Audit & Critical Fixes (9 issues resolved):**

- **BUY threshold lowered 70 → 60** — the 70-point threshold was structurally unreachable for most NSE stocks on neutral/bearish market days. New thresholds: BUY ≥ 60, HOLD 45–59, SELL < 45. Score bands updated to match exactly (no more "Good Watchlist Stock" label on stocks treated as HOLD).
- **Fundamental score per-category budgets** — replaced unbounded additive accumulation (theoretical max ~215) with six capped buckets: valuation ±15, profitability ±15, growth ±15, balance sheet ±10, governance ±10, banking ±10. Scores now discriminate meaningfully across the full 0–100 range.
- **Growth double-counting fixed** — revenue and earnings growth were previously counted 3–5× simultaneously (TTM + 3Y CAGR + 5Y CAGR + trend, all additive). Now each counted once via the longest available window; quarterly trend is a capped supplement.
- **Quality gate fixed** — OCF check used Python `or` which treated zero cash flow as falsy; now uses explicit `is None`. Rejection logic split into independent OR conditions (was AND — too strict).
- **Sentiment denominator corrected** — neutral articles no longer dilute bullish signal. Denominator is now `bullish + bearish` only.
- **Race condition on `_generating` flag** — concurrent POST `/picks/generate` requests could both pass the guard simultaneously. Fixed with `threading.Lock`.
- **Optimizer fallback max_weight enforcement** — the fallback could allocate 100% to one stock when alphas were skewed. Fixed with iterative clipping loop.
- **Long-horizon IC training** — long horizon was training on 20D returns (1 month) despite a 3-6 month stated horizon. Now uses `return_60d`. Outcome logger waits 90 calendar days before resolving long-horizon predictions.
- **Partial returns never logged** — outcome logger previously logged partial forward returns when the window hadn't elapsed, corrupting IC training data. Now returns `None` until the full window is complete.
- **Validation thresholds synced** — validation BUY threshold (was 65) now matches live system (60) for all horizons.
- **`picks_generated_today()` logic fixed** — a prior run that produced 0 BUY signals (before threshold fix) saved an empty payload with today's date, causing the startup catch-up to skip regeneration. Now requires at least one actual pick to count as "done today".
- **NSE Daily Picks expanded** — universe expanded from Nifty 100 (96 stocks) to all NSE-listed stocks screened by market cap ≥ ₹100 Cr (~500-600 stocks). Two-phase pipeline: Phase-0 bulk momentum screen → Phase-1 deep prediction on top 50 candidates only (memory-safe batching of 300).
- **Documentation fully updated** — README and STOCKSENSE_DOCUMENTATION.md updated to reflect all threshold, formula, and architecture changes.

**Live test results (2026-06-19):**
- `/health`: ✅ ok
- `/api/predictions/TCS?market=IN&horizon=medium`: ✅ Score 78, BUY, Strong Buy Candidate
- `/api/screener/top-movers?market=IN`: ✅ 10 gainers, 10 losers
- `/api/validation/results?horizon=medium`: ✅ Hit rate 56.6%, avg return 3.75%
- `/api/picks/status`: ✅ Generating (startup catch-up triggered correctly)

### Session 3 — 2026-06-18

**New Features:**
- **Paper Trading module** — full simulated trading with open/close positions, stop-loss/target tracking, unrealised and realised P&L, Postgres persistence
- **Price Alerts system** — Postgres-backed price level alerts with live trigger detection
- **Daily Picks Trust Layer** — real backtest results, confidence calibration table per score band, live P&L tracker for past picks
- **Pick Card UI overhaul** — rank badges (#1–#5), sector tags, top 3 signals visible inline, compact regime bar

**Fixes:**
- Watchlist migrated from ephemeral JSON file to Postgres — no longer disappears on Render restart
- screener.in login now fires at startup (not lazily) — data available from first request
- screener.in login enhanced: tries both `username`/`email` field names, logs every step
- Daily picks cron now waits for Render to be healthy before triggering — prevents silent cold-start failures
- Paper trading open positions: stop loss/target hint row now always visible (shows "not set — click ✎ to add one" when unset, for consistent layout)
- Open positions now always show SL/target hint row consistently regardless of signal type

**Performance Improvements:**
- `get_fundamentals`: replaced 3×sleep(3) blocking retry with `asyncio.wait_for(timeout=8s)`
- OHLCV data: added 5-minute in-process cache (was completely uncached)
- Quote enrichment: removed redundant second yfinance `fast_info` call
- Heatmap cache TTL: 3 min → 5 min
- Dashboard refetch interval: 60s → 120s, staleTime aligned
- Heatmap frontend refetch: 3 min → 5 min, staleTime aligned
- Stock detail quote refetch: 30s → 60s
- `refetchOnWindowFocus: false` added across all major queries

### Session 2 — (prior)

- Walk-forward validation engine with Postgres storage
- Learning Alpha Engine (IC weights, regime clustering, meta-model, outcome logger)
- Screener.in authenticated scraping for Indian fundamental data
- Portfolio page with BUY/HOLD/SELL signals per holding
- History tab with horizon selector on portfolio page
- Backtest async fix (non-blocking event loop)
- Prediction cache size cap (300 entries, LRU eviction)
- Hammer & Morning Star candlestick pattern bug fixes

### Session 1 — (initial)

- Core prediction engine (technical, fundamental, sentiment, quality, macro)
- Stock detail page with full factor breakdown
- Dashboard with top movers (US/IN/Crypto)
- Heatmap page (sector-wise colour-coded)
- Screener with filters
- Watchlist with live prices
- Daily Picks engine (9-phase Learning Alpha pipeline)
- GitHub Actions automation (daily picks, weekly validation, keep-alive)
- Render + Vercel deployment

---

*This document is a living record of StockSense360. It is updated with every significant change to the product.*
