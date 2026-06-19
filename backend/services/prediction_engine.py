import asyncio
import logging
import random
import time
import yfinance as yf

log = logging.getLogger(__name__)
import pandas as pd
import numpy as np
from services.technical_indicators import compute_indicators, get_signal_summary
from services.news_sentiment import NewsSentimentService
from services.global_context import get_global_context
from services.quality_factors import compute_all_quality_factors

MARKET_SUFFIX = {"US": "", "IN": ".NS"}
_news_svc = NewsSentimentService()

# Market regime index tickers
REGIME_TICKER = {"US": "^GSPC", "IN": "^NSEI"}

# ── Caches ────────────────────────────────────────────────────────────────────
_PRED_TTL = 15 * 60    # 15 minutes
_REGIME_TTL = 30 * 60  # 30 minutes
_CACHE_MAX = 300        # cap at 300 entries to prevent OOM on free-tier 512MB Render

def _cache_set(cache: dict, key: str, value: tuple) -> None:
    """Insert into cache, evicting the oldest entry when cap is reached."""
    if key not in cache and len(cache) >= _CACHE_MAX:
        oldest = min(cache, key=lambda k: cache[k][0])
        del cache[oldest]
    cache[key] = value

# Prediction cache: { "SYMBOL:MARKET:HORIZON" -> (timestamp, result) }
_pred_cache: dict[str, tuple[float, dict]] = {}

# Market regime cache: { "IN"|"US" -> (timestamp, result) }
_regime_cache: dict[str, tuple[float, dict]] = {}


def _market_regime(market: str) -> dict:
    """
    Checks whether the broad market is in an uptrend or downtrend.
    Uses S&P 500 for US, NIFTY 50 for India.
    Returns: {"trend": "BULL"/"BEAR"/"SIDEWAYS", "score_adj": int}
    score_adj is applied to the composite score (+8 in bull, -8 in bear).
    """
    cached = _regime_cache.get(market)
    if cached and (time.time() - cached[0]) < _REGIME_TTL:
        return cached[1]
    try:
        ticker = yf.Ticker(REGIME_TICKER.get(market, "^GSPC"))
        for attempt in range(3):
            try:
                df = ticker.history(period="6mo")
                break
            except Exception as e:
                if "rate" in str(e).lower() and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                else:
                    raise
        if len(df) < 50:
            return {"trend": "SIDEWAYS", "score_adj": 0, "reason": "Insufficient regime data"}

        close = df["Close"]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        ema200 = close.ewm(span=200).mean().iloc[-1] if len(df) >= 200 else close.ewm(span=len(df)).mean().iloc[-1]
        current = close.iloc[-1]

        # 3-month momentum
        ret_3m = (current - close.iloc[-63]) / close.iloc[-63] if len(df) >= 63 else 0

        if current > ema50 and ret_3m > 0.03:
            trend = "BULL"
            adj = 8
            reason = f"Market in uptrend — {ret_3m*100:.1f}% gain over 3 months"
        elif current < ema50 and ret_3m < -0.03:
            trend = "BEAR"
            adj = -8
            reason = f"Market in downtrend — {ret_3m*100:.1f}% over 3 months"
        else:
            trend = "SIDEWAYS"
            adj = 0
            reason = "Market moving sideways"

        result = {"trend": trend, "score_adj": adj, "reason": reason}
        _cache_set(_regime_cache, market, (time.time(), result))
        return result
    except Exception:
        return {"trend": "SIDEWAYS", "score_adj": 0, "reason": "Could not fetch market data"}


SCORE_BANDS = [
    (90, "Exceptional Opportunity"),   # BUY — very high confidence
    (75, "Strong Buy Candidate"),       # BUY — high confidence
    (60, "Good Watchlist Stock"),       # BUY — moderate confidence (threshold: BUY ≥ 60)
    (45, "Neutral — Monitor"),          # HOLD
    (0,  "Avoid"),                      # SELL
]

def _score_label(score: int) -> str:
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "Avoid"


def _dynamic_weights(df: pd.DataFrame, horizon: str, regime: dict | None = None) -> dict:
    """
    Adjusts weights based on both volatility AND market regime:

    Volatility adjustment:
      High vol  → trust fundamentals more (technicals get noisy)
      Low vol   → technicals are more reliable

    Regime adjustment (applied on top of vol adjustment):
      BULL      → boost technicals + relative strength (momentum pays)
      BEAR      → boost fundamentals + quality/FCF (defensiveness pays)
      SIDEWAYS  → boost valuation weight (mean reversion pays)
    """
    base_weights = {
        "short":  {"tech": 0.70, "fund": 0.15, "sentiment": 0.15},
        "medium": {"tech": 0.40, "fund": 0.45, "sentiment": 0.15},
        "long":   {"tech": 0.15, "fund": 0.75, "sentiment": 0.10},
    }[horizon]

    vol_regime = "NORMAL"
    vol_20d = 0.0

    try:
        returns = df["Close"].pct_change().dropna()
        vol_20d = float(returns.iloc[-20:].std() * np.sqrt(252))

        if vol_20d > 0.35:
            shift = 0.10
            base_weights = {
                "tech": max(0.10, base_weights["tech"] - shift),
                "fund": min(0.80, base_weights["fund"] + shift),
                "sentiment": base_weights["sentiment"],
            }
            vol_regime = "HIGH"
        elif vol_20d < 0.15:
            shift = 0.08
            base_weights = {
                "tech": min(0.75, base_weights["tech"] + shift),
                "fund": max(0.05, base_weights["fund"] - shift),
                "sentiment": base_weights["sentiment"],
            }
            vol_regime = "LOW"
    except Exception:
        pass

    # ── Regime-aware weight adjustment ───────────────────────────────────────
    regime_trend = (regime or {}).get("trend", "SIDEWAYS")
    regime_shift = 0.08

    if regime_trend == "BULL":
        # Bull market: momentum and technicals pay — boost tech, trim fundamentals
        base_weights["tech"] = min(0.80, base_weights["tech"] + regime_shift)
        base_weights["fund"] = max(0.05, base_weights["fund"] - regime_shift)

    elif regime_trend == "BEAR":
        # Bear market: quality and fundamentals are defensive anchors
        base_weights["fund"] = min(0.85, base_weights["fund"] + regime_shift)
        base_weights["tech"] = max(0.05, base_weights["tech"] - regime_shift)

    # SIDEWAYS: default weights are fine — valuation-based mean reversion
    # is handled by the quality factor valuation sub-score

    # Normalise so weights sum to 1.0
    total = sum(base_weights[k] for k in ("tech", "fund", "sentiment"))
    if total > 0:
        for k in ("tech", "fund", "sentiment"):
            base_weights[k] = round(base_weights[k] / total, 4)

    return {
        **base_weights,
        "vol_regime": vol_regime,
        "vol_20d": round(vol_20d * 100, 1),
        "regime_applied": regime_trend,
    }


def _compute_risk_penalty(info: dict, df: pd.DataFrame, quality: dict | None = None) -> tuple[int, list[str]]:
    """
    Step 3 of the framework: compute a risk penalty to subtract from raw score.
    Risk factors reduce the score — they never add to it.

    Returns (penalty_points, penalty_reasons).
    """
    penalty = 0
    reasons: list[str] = []

    try:
        # High debt
        de = info.get("debtToEquity")
        if de is not None and de > 300:
            penalty += 8
            reasons.append(f"High debt-to-equity ({de:.0f}%) — financial fragility risk")
        elif de is not None and de > 200:
            penalty += 4
            reasons.append(f"Elevated debt-to-equity ({de:.0f}%) — leverage risk")

        # High beta
        beta = info.get("beta")
        if beta is not None and beta > 2.0:
            penalty += 6
            reasons.append(f"High beta ({beta:.2f}) — amplified downside in market corrections")
        elif beta is not None and beta > 1.6:
            penalty += 3
            reasons.append(f"Above-average beta ({beta:.2f}) — sensitive to market swings")

        # Negative FCF
        fcf = info.get("freeCashflow")
        if fcf is not None and fcf < 0:
            penalty += 5
            reasons.append("Negative free cash flow — company burning cash; survival risk in downturns")

        # Negative ROE (loss-making)
        roe = info.get("returnOnEquity")
        if roe is not None and roe < -0.05:
            penalty += 5
            reasons.append(f"Negative ROE ({roe*100:.1f}%) — destroying shareholder value")

        # Max drawdown from risk_management sub-score
        if quality:
            risk_bd = quality.get("breakdown", {}).get("risk_management", {})
            risk_sc = risk_bd.get("score", 50) if isinstance(risk_bd, dict) else 50
            if risk_sc < 35:
                penalty += 5
                reasons.append("Poor risk profile — high drawdown and/or low risk-adjusted returns")
            elif risk_sc < 45:
                penalty += 2

        # Earnings volatility — high EPS std dev signals unpredictability
        if len(df) >= 60:
            try:
                returns = df["Close"].pct_change().dropna()
                quarterly_vol = returns.resample("QE").std() if hasattr(returns.index, "freq") else None
                # Simpler proxy: rolling 63-day vol std
                vol_series = [returns.iloc[max(0,i-63):i].std() for i in range(63, len(returns), 21)]
                if len(vol_series) >= 4:
                    vol_consistency = np.std(vol_series) / (np.mean(vol_series) + 1e-10)
                    if vol_consistency > 0.5:
                        penalty += 4
                        reasons.append("High earnings/return volatility — unpredictable stock behaviour increases execution risk")
                    elif vol_consistency > 0.35:
                        penalty += 2
            except Exception:
                pass

    except Exception:
        pass

    return min(penalty, 30), reasons  # cap at 30 — allows extremely risky stocks to be penalised fairly


class PredictionEngine:
    async def predict(self, symbol: str, market: str, horizon: str) -> dict:
        # ── Prediction cache — skip ALL work on repeated requests ───────────────
        cache_key = f"{symbol}:{market}:{horizon}"
        cached = _pred_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _PRED_TTL:
            return cached[1]

        suffix = MARKET_SUFFIX.get(market, "")
        loop = asyncio.get_running_loop()

        # ── Round 1: fetch price history + ticker.info + regime in parallel ─────
        period = {"short": "6mo", "medium": "2y", "long": "5y"}[horizon]

        def _fetch_history():
            for attempt in range(3):
                try:
                    df = yf.Ticker(symbol + suffix).history(period=period)
                    if not df.empty:
                        return df
                    # Empty but no exception — crumb/session issue; reset and retry
                    if attempt < 2:
                        try:
                            yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                        except Exception:
                            pass
                        time.sleep(2 + attempt * 2)
                except Exception as e:
                    err_str = str(e).lower()
                    if attempt < 2:
                        if "crumb" in err_str or "401" in err_str or "unauthorized" in err_str:
                            try:
                                yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                            except Exception:
                                pass
                        time.sleep(2 + attempt * 2)
                    else:
                        raise
            return yf.Ticker(symbol + suffix).history(period=period)  # final attempt, let it fail naturally

        def _fetch_info():
            for attempt in range(2):  # max 2 attempts — screener fills gaps anyway
                try:
                    info = yf.Ticker(symbol + suffix).info
                    # Guard against None or non-dict (yfinance NoneType bug)
                    if not isinstance(info, dict):
                        info = {}
                    if len(info) > 5:  # valid info has many fields
                        return info
                    # Sparse info — crumb/session issue, refresh and retry once
                    if attempt == 0:
                        try:
                            yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                        except Exception:
                            pass
                        time.sleep(2)
                except Exception as e:
                    err_str = str(e).lower()
                    if attempt == 0:
                        if "crumb" in err_str or "401" in err_str or "unauthorized" in err_str or "nonetype" in err_str:
                            try:
                                yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                            except Exception:
                                pass
                        time.sleep(2)
                    else:
                        log.warning("[predict] _fetch_info failed for %s%s: %s", symbol, suffix, e)
                        return {}
            return {}

        try:
            df, info, regime = await asyncio.wait_for(
                asyncio.gather(
                    loop.run_in_executor(None, _fetch_history),
                    loop.run_in_executor(None, _fetch_info),
                    loop.run_in_executor(None, _market_regime, market),
                ),
                timeout=45.0,
            )
        except asyncio.TimeoutError:
            return {"error": "Data fetch timed out — Yahoo Finance took too long. Try again in a moment."}

        # ── Screener.in enrichment (India only) ──────────────────────────────
        # Fills missing ROE, ROCE, promoter holding, revenue/profit growth from
        # screener.in — more reliable for Indian stocks than yfinance alone.
        if market == "IN":
            try:
                from services.screener_data import augment_info_with_screener
                info = await loop.run_in_executor(None, augment_info_with_screener, info, symbol)
            except Exception as e:
                print(f"[screener] augmentation failed for {symbol}: {e}")

            # ── BSE fallback for merged/renamed companies ─────────────────────
            # When yfinance returns minimal data (< 5 key fields), try BSE API
            # which always has current data from official exchange filings.
            _yf_key_fields = ("trailingPE", "returnOnEquity", "revenueGrowth",
                               "profitMargins", "earningsGrowth", "beta")
            _yf_filled = sum(1 for k in _yf_key_fields if info.get(k) is not None)
            if _yf_filled < 3:
                try:
                    from services.bse_data import get_bse_fundamentals
                    bse_info = await loop.run_in_executor(None, get_bse_fundamentals, symbol)
                    if bse_info:
                        # Merge BSE data: BSE fills gaps, yfinance values take priority
                        merged = dict(bse_info)
                        merged.update({k: v for k, v in info.items() if v is not None})
                        info = merged
                        print(f"[bse] fallback filled {len(bse_info)} fields for {symbol}")
                except Exception as e:
                    print(f"[bse] fallback failed for {symbol}: {e}")

        _SHORT_TTL_TS = lambda: time.time() - (_PRED_TTL - 120)  # 2-min TTL for errors

        if df.empty:
            err = {"error": "No price data returned — Yahoo Finance may be rate-limiting. Try again in a moment."}
            _cache_set(_pred_cache, cache_key, (_SHORT_TTL_TS(), err))
            return err

        # Drop incomplete rows (NaN close) — last bar may be partial on live market
        df = df.dropna(subset=["Close"])
        if df.empty or len(df) < 20:
            err = {"error": "Insufficient price history (need at least 20 days)"}
            _cache_set(_pred_cache, cache_key, (_SHORT_TTL_TS(), err))
            return err

        df = compute_indicators(df)
        tech_signal = get_signal_summary(df)
        ratio_score = self._fundamental_score(info, horizon, market)

        # ── Hard quality gate — reject fundamentally broken stocks ──────────────
        gate_passed, gate_reasons = self._quality_gate(info, df, horizon)
        if not gate_passed:
            return {
                "symbol": symbol,
                "market": market,
                "horizon": horizon,
                "signal": "REJECTED",
                "rejection_reasons": gate_reasons,
                "confidence": 0,
                "current_price": round(float(df["Close"].iloc[-1]), 2) if not df.empty else None,
            }

        # ── Round 2: news + global_ctx + quality + deep_fund all in parallel ────
        async def _get_news():
            try:
                return await _news_svc.get_news_with_sentiment(symbol, market, 10)
            except BaseException as e:
                print(f"[news] failed for {symbol}: {e}")
                return {"articles": []}

        def _get_global_ctx():
            if market != "IN":
                return {}
            try:
                return get_global_context(symbol)
            except BaseException as e:
                print(f"[global_ctx] failed for {symbol}: {e}")
                return {}

        def _get_quality():
            # Quality factors now run for IN and US (not Crypto — no financials)
            if market == "CRYPTO":
                return {}
            try:
                return compute_all_quality_factors(symbol, yf.Ticker(symbol + suffix), df, info, horizon)
            except BaseException as e:
                print(f"[quality] failed for {symbol}: {e}")
                return {}

        def _get_global_ctx_safe():
            try:
                return _get_global_ctx()
            except BaseException as e:
                print(f"[global_ctx] failed for {symbol}: {e}")
                return {}

        def _get_deep_fund():
            if horizon not in ("medium", "long"):
                return None
            try:
                return self._deep_fundamental_score(yf.Ticker(symbol + suffix), horizon)
            except BaseException as e:
                print(f"[deep_fund] failed for {symbol}: {e}")
                return None

        news_data, global_ctx, quality, deep_score_raw = await asyncio.gather(
            _get_news(),
            loop.run_in_executor(None, _get_global_ctx_safe),
            loop.run_in_executor(None, _get_quality),
            loop.run_in_executor(None, _get_deep_fund),
        )

        sentiment_score = self._aggregate_sentiment(news_data["articles"])

        # Blend deep fundamentals for medium/long
        if deep_score_raw is not None:
            blend = 0.3 if horizon == "medium" else 0.6
            blended = round(ratio_score["score"] * (1 - blend) + deep_score_raw["score"] * blend)
            fund_score = {
                "score": max(0, min(100, blended)),
                "reasons": ratio_score["reasons"] + deep_score_raw["reasons"],
                "deep_available": deep_score_raw["available"],
            }
        else:
            fund_score = ratio_score

        # Analyst consensus + 52W position (fast — computed from info/df already fetched)
        analyst_score = self._analyst_score(info, market)
        week52_score = self._week52_score(df, info)

        # Dynamic weights based on volatility + market regime
        weights = _dynamic_weights(df, horizon, regime=regime)

        (signal, confidence, reasoning, score_band, factor_contributions, composite_score,
         confidence_score, confidence_band, confidence_components) = self._composite_signal(
            tech_signal, fund_score, sentiment_score, horizon, weights, regime,
            global_ctx=global_ctx, analyst_score=analyst_score, week52_score=week52_score,
            quality=quality, df=df, info=info, symbol=symbol, market=market,
        )

        from services.case_generator import generate_bull_bear_case
        bull_case, bear_case = generate_bull_bear_case(
            quality=quality, fund=fund_score, technical=tech_signal,
            sentiment=sentiment_score, analyst_score=analyst_score,
            week52_score=week52_score, info=info,
        )

        current_price = float(df["Close"].iloc[-1])
        atr_series = (df["High"] - df["Low"]).rolling(14).mean().dropna()
        atr = float(atr_series.iloc[-1]) if not atr_series.empty else current_price * 0.02

        target_price = self._estimate_target(
            current_price, signal, confidence, horizon, df, info
        )
        trade_levels = self._trade_levels(current_price, signal, target_price, atr, horizon)

        import datetime as _dt
        result = {
            "symbol": symbol,
            "market": market,
            "horizon": horizon,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "data_timestamp": df.index[-1].isoformat() if hasattr(df.index[-1], "isoformat") else str(df.index[-1]),
            "signal": signal,
            "confidence": confidence,
            "score_band": score_band,
            "composite_score": composite_score,
            "factor_contributions": factor_contributions,
            "confidence_score": confidence_score,
            "confidence_band": confidence_band,
            "confidence_breakdown": confidence_components,
            "bull_case": bull_case,
            "bear_case": bear_case,
            "current_price": round(current_price, 2),
            "target_price": target_price,
            "trade_levels": trade_levels,
            "reasoning": reasoning,
            "technical": tech_signal,
            "fundamental_score": fund_score,
            "sentiment_score": sentiment_score,
            "market_regime": regime,
            "global_context": {
                "score": global_ctx.get("score"),
                "levels": global_ctx.get("levels", {}),
                "changes": global_ctx.get("changes", {}),
            } if global_ctx else None,
            "quality_factors": {
                "score":             quality.get("score"),
                "sector":            quality.get("sector"),
                "piotroski":         quality.get("piotroski"),
                "altman_z":          quality.get("altman_z"),
                "altman_zone":       quality.get("altman_zone"),
                "accruals_ratio":    quality.get("accruals_ratio"),
                "buffett_passed":    quality.get("buffett_passed"),
                "buffett_total":     quality.get("buffett_total"),
                "buffett_checklist": quality.get("buffett_checklist"),
                "breakdown":         {k: v.get("score") for k, v in quality.get("breakdown", {}).items()},
            } if quality else None,
            "weights_used": {k: v for k, v in weights.items() if k in ("tech", "fund", "sentiment", "vol_regime")},
        }
        _cache_set(_pred_cache, cache_key, (time.time(), result))
        return result

    def _trade_levels(self, price: float, signal: str, target: float, atr: float, horizon: str) -> dict:
        """
        Trade level logic:
        - Take profit ALWAYS equals the model's price target — the two must stay
          consistent (the "Target Price" shown in the prediction panel and the
          "Take Profit" in the trade card are the same forecast).
        - Stop loss: ATR-based, but tightened toward the target (never below a
          noise floor) so the risk/reward clears MIN_RR honestly. We adjust risk,
          never fabricate reward beyond what the model actually forecasts.
        - Entry zone: narrow band around current price based on signal direction.
        """
        MIN_RR = 1.5  # target R:R — achieved by tightening the stop, not stretching the target

        profit_distance = abs(target - price)

        # Step 1 — base (noise-appropriate) stop distance + a floor we won't tighten past
        if horizon == "short":
            base_sl = atr * 1.5                                  # ~1-2 weeks noise
            sl_floor = atr * 1.0                                 # keep at least 1 ATR of buffer
            trailing_stop_pct = None
        elif horizon == "medium":
            base_sl = max(profit_distance * 0.5, atr * 3.0)      # 3-month swings
            sl_floor = atr * 2.0
            trailing_stop_pct = 12.0
        else:
            base_sl = max(profit_distance * 0.4, atr * 5.0)      # long-term trend
            sl_floor = atr * 3.0
            trailing_stop_pct = 20.0
        base_sl = min(base_sl, price * 0.25)                     # never more than 25% away

        # Step 2 — tighten the stop toward MIN_RR, but never below the noise floor.
        # If the forecast move is too small to reach MIN_RR even at the floor, we
        # surface the honest (sub-1.5) R:R rather than faking the take-profit.
        sl_for_rr = profit_distance / MIN_RR if profit_distance > 0 else base_sl
        sl_distance = max(min(base_sl, sl_for_rr), sl_floor)
        sl_distance = min(sl_distance, price * 0.25)

        # Take profit is the model target — unchanged across signals.
        take_profit = round(target, 2)

        if signal == "BUY":
            entry_low   = round(price - atr * 0.3, 2)
            entry_high  = round(price + atr * 0.1, 2)
            stop_loss   = round(price - sl_distance, 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(take_profit - price, 2)

        elif signal == "SELL":
            entry_low   = round(price - atr * 0.1, 2)
            entry_high  = round(price + atr * 0.3, 2)
            stop_loss   = round(price + sl_distance, 2)
            risk        = round(stop_loss - price, 2)
            reward      = round(price - take_profit, 2)

        else:  # HOLD — neutral range around current price
            entry_low   = round(price - atr * 0.5, 2)
            entry_high  = round(price + atr * 0.5, 2)
            stop_loss   = round(price - sl_distance, 2)
            risk        = round(price - stop_loss, 2)
            reward      = round(abs(take_profit - price), 2)

        rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        return {
            "signal": signal,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop_pct": trailing_stop_pct,
            "risk_per_share": round(risk, 2),
            "reward_per_share": round(reward, 2),
            "risk_reward_ratio": rr_ratio,
        }

    def _deep_fundamental_score(self, ticker, horizon: str) -> dict:
        """
        Analyses actual financial statements — Income Statement, Balance Sheet, Cash Flow.
        Only called for medium and long term (skipped for short term to keep latency low).
        Returns score 0-100 + list of key findings.
        """
        score = 50
        reasons = []

        try:
            fin = ticker.financials          # P&L — columns = annual periods
            bs  = ticker.balance_sheet       # Balance Sheet
            cf  = ticker.cashflow            # Cash Flow
        except Exception:
            return {"score": 50, "reasons": [], "available": False}

        if fin is None or fin.empty:
            return {"score": 50, "reasons": [], "available": False}

        try:
            # ── Revenue trend (P&L) ──────────────────────────────────────────
            rev_row = None
            rev_vals: list = []  # initialize here so later references are always safe
            for label in ["Total Revenue", "Revenue"]:
                if label in fin.index:
                    rev_row = fin.loc[label].dropna()
                    break
            if rev_row is not None and len(rev_row) >= 2:
                rev_vals = rev_row.sort_index().values  # oldest → newest
                rev_growth_1y = (rev_vals[-1] - rev_vals[-2]) / abs(rev_vals[-2]) if rev_vals[-2] != 0 else 0
                if rev_growth_1y > 0.20:
                    score += 12
                    reasons.append(f"Revenue grew {rev_growth_1y*100:.1f}% YoY — strong top-line growth")
                elif rev_growth_1y > 0.08:
                    score += 6
                    reasons.append(f"Revenue grew {rev_growth_1y*100:.1f}% YoY — steady growth")
                elif rev_growth_1y < -0.05:
                    score -= 10
                    reasons.append(f"Revenue declined {rev_growth_1y*100:.1f}% YoY — top-line pressure")

                # Revenue acceleration: latest growth > prior growth
                if len(rev_vals) >= 3:
                    rev_growth_2y = (rev_vals[-2] - rev_vals[-3]) / abs(rev_vals[-3]) if rev_vals[-3] != 0 else 0
                    if rev_growth_1y > rev_growth_2y + 0.05:
                        score += 6
                        reasons.append("Revenue growth accelerating — momentum building")
                    elif rev_growth_1y < rev_growth_2y - 0.05:
                        score -= 6
                        reasons.append("Revenue growth decelerating — losing momentum")

            # ── Operating Income / EBITDA margin (P&L) ──────────────────────
            op_row = None
            for label in ["Operating Income", "EBIT"]:
                if label in fin.index:
                    op_row = fin.loc[label].dropna()
                    break
            if op_row is not None and rev_row is not None and len(op_row) >= 1:
                op_vals = op_row.sort_index().values
                rev_latest = rev_vals[-1] if rev_row is not None else None
                if rev_latest and rev_latest > 0:
                    op_margin = op_vals[-1] / rev_latest
                    if op_margin > 0.25:
                        score += 10
                        reasons.append(f"Operating margin {op_margin*100:.1f}% — highly profitable")
                    elif op_margin > 0.10:
                        score += 4
                        reasons.append(f"Operating margin {op_margin*100:.1f}% — healthy")
                    elif op_margin < 0:
                        score -= 10
                        reasons.append(f"Operating loss — margin {op_margin*100:.1f}%")

            # ── Free Cash Flow (Cash Flow statement) ─────────────────────────
            screener_cf = info.get("_screener_data") or {}
            ocf_vals = capex_vals = None

            if cf is not None and not cf.empty:
                ocf_row = capex_row = None
                for label in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
                    if label in cf.index:
                        ocf_row = cf.loc[label].dropna()
                        break
                for label in ["Capital Expenditure", "Capital Expenditures"]:
                    if label in cf.index:
                        capex_row = cf.loc[label].dropna()
                        break
                if ocf_row is not None and len(ocf_row) >= 1:
                    ocf_vals = list(ocf_row.sort_index().values)
                    capex_vals = list(capex_row.sort_index().values) if capex_row is not None else None

            # Fall back to screener.in cashflow for Indian stocks (yfinance often empty)
            if ocf_vals is None and screener_cf.get("operating_cf_annual_cr"):
                ocf_vals = [v for v in screener_cf["operating_cf_annual_cr"] if v is not None]
                inv_cf = screener_cf.get("investing_cf_annual_cr") or []
                capex_vals = [abs(v) for v in inv_cf if v is not None] if inv_cf else None

            if ocf_vals and len(ocf_vals) >= 1:
                fcf_latest = ocf_vals[-1]
                if capex_vals is not None and len(capex_vals) >= 1:
                    fcf_latest = ocf_vals[-1] - abs(capex_vals[-1])

                if fcf_latest > 0:
                    score += 10
                    reasons.append("Positive free cash flow — company generates real cash")
                    if len(ocf_vals) >= 2:
                        fcf_prev = ocf_vals[-2] - (abs(capex_vals[-2]) if capex_vals is not None and len(capex_vals) >= 2 else 0)
                        if fcf_latest > fcf_prev * 1.10:
                            score += 6
                            reasons.append("Free cash flow growing — strengthening cash generation")
                else:
                    score -= 8
                    reasons.append("Negative free cash flow — burning more cash than it generates")

            # ── Balance Sheet: Liquidity & Debt ─────────────────────────────
            if bs is not None and not bs.empty:
                curr_assets = curr_liab = total_debt = cash = None
                for label in ["Current Assets", "Total Current Assets"]:
                    if label in bs.index:
                        curr_assets = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Current Liabilities", "Total Current Liabilities"]:
                    if label in bs.index:
                        curr_liab = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Total Debt", "Long Term Debt"]:
                    if label in bs.index:
                        total_debt = bs.loc[label].dropna().sort_index().values[-1]
                        break
                for label in ["Cash And Cash Equivalents", "Cash"]:
                    if label in bs.index:
                        cash = bs.loc[label].dropna().sort_index().values[-1]
                        break

                # Determine sector before applying current ratio penalty — banks/insurance
                # structurally operate below 1.0 current ratio due to leverage model.
                # info is referenced by existing code on this method (line 686), so we use same pattern.
                try:
                    _sector_str = (info.get("sector") or "").lower()
                except Exception:
                    _sector_str = ""
                _is_financial = any(k in _sector_str for k in ("financial", "bank", "insurance"))

                if curr_assets and curr_liab and curr_liab > 0:
                    current_ratio = curr_assets / curr_liab
                    if current_ratio > 2.0:
                        score += 8
                        reasons.append(f"Current ratio {current_ratio:.1f}x — strong liquidity")
                    elif current_ratio > 1.2:
                        score += 3
                        reasons.append(f"Current ratio {current_ratio:.1f}x — adequate liquidity")
                    elif current_ratio < 1.0 and not _is_financial:
                        score -= 10
                        reasons.append(f"Current ratio {current_ratio:.1f}x — liquidity risk")

                # Net debt check
                if total_debt and cash and rev_row is not None:
                    net_debt = total_debt - cash
                    rev_latest = rev_vals[-1] if rev_row is not None and len(rev_vals) >= 1 else None
                    if rev_latest and rev_latest > 0 and net_debt > rev_latest * 3:
                        score -= 8
                        reasons.append("Net debt exceeds 3x revenue — highly leveraged")
                    elif net_debt < 0:
                        score += 6
                        reasons.append("Net cash position — more cash than debt")

        except Exception:
            pass  # partial data is fine — use whatever we extracted

        return {"score": max(0, min(100, score)), "reasons": reasons, "available": True}

    def _fundamental_score(self, info: dict, horizon: str, market: str = "US") -> dict:
        # Per-category buckets prevent any single dimension from dominating.
        # Each bucket has its own ±cap; the total score is assembled at the end.
        # Base = 50; each bucket contributes [-cap, +cap] around zero.
        valuation_pts  = 0   # cap ±15: PE, P/B, EV/EBITDA
        profitability  = 0   # cap ±15: ROE, ROCE, margins
        growth_pts     = 0   # cap ±15: revenue growth + earnings growth (combined, not additive)
        balance_sheet  = 0   # cap ±10: D/E, OCF, Altman, Sloan
        governance     = 0   # cap ±10: promoter, FII/DII, pledge
        banking_pts    = 0   # cap ±10: NPA, NIM (banks only)
        reasons = []

        # ── VALUATION bucket (cap ±15) ────────────────────────────────────────
        pe = info.get("trailingPE")
        if pe is not None:
            pe_cheap   = 18 if market == "IN" else 15
            pe_fair    = 30 if market == "IN" else 25
            pe_stretch = 55 if market == "IN" else 50
            if pe < pe_cheap:
                valuation_pts += 8
                reasons.append(f"Low P/E ({pe:.1f}) — attractively valued")
            elif pe < pe_fair:
                valuation_pts += 3
                reasons.append(f"Reasonable P/E ({pe:.1f})")
            elif pe > pe_stretch:
                valuation_pts -= 8
                reasons.append(f"High P/E ({pe:.1f}) — stretched valuation")

        screener_d = info.get("_screener_data", {}) or {}
        book_value = screener_d.get("book_value") or info.get("bookValue")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if book_value and current_price and book_value > 0:
            pb = current_price / book_value
            pb_cheap = 2.5 if market == "IN" else 2.0
            pb_rich  = 8.0 if market == "IN" else 6.0
            if pb < pb_cheap:
                valuation_pts += 4
                reasons.append(f"Low P/B ratio ({pb:.1f}x) — trading below asset value")
            elif pb > pb_rich:
                valuation_pts -= 4
                reasons.append(f"High P/B ratio ({pb:.1f}x) — premium to book")
        valuation_pts = max(-15, min(15, valuation_pts))

        # ── PROFITABILITY bucket (cap ±15) ────────────────────────────────────
        roe = info.get("returnOnEquity")
        if roe is not None:
            if roe > 0.20:
                profitability += 7
                reasons.append(f"Strong ROE ({roe*100:.1f}%)")
            elif roe > 0.10:
                profitability += 3
                reasons.append(f"Decent ROE ({roe*100:.1f}%)")
            elif roe < 0:
                profitability -= 7
                reasons.append("Negative ROE — unprofitable")

        profit_margin = info.get("profitMargins")
        if profit_margin is not None:
            if profit_margin > 0.20:
                profitability += 5
                reasons.append(f"High profit margins ({profit_margin*100:.1f}%)")
            elif profit_margin < 0:
                profitability -= 5
                reasons.append("Negative profit margins")

        roce = info.get("returnOnCapitalEmployed")
        if roce is not None:
            if roce > 0.20:
                profitability += 6
                reasons.append(f"High ROCE ({roce*100:.1f}%) — excellent capital efficiency")
            elif roce > 0.12:
                profitability += 2
                reasons.append(f"Decent ROCE ({roce*100:.1f}%)")
            elif roce < 0.06:
                profitability -= 4
                reasons.append(f"Low ROCE ({roce*100:.1f}%) — poor capital allocation")
        profitability = max(-15, min(15, profitability))

        # ── GROWTH bucket (cap ±15) — revenue and earnings scored once each ──
        # Revenue: use 3Y CAGR if available (more reliable), else TTM YoY
        rev_3y = screener_d.get("sales_growth_3y_pct")
        rev_growth = info.get("revenueGrowth")
        if rev_3y is not None:
            if rev_3y > 15:
                growth_pts += 7
                reasons.append(f"3Y revenue CAGR {rev_3y:.1f}% — consistent top-line growth")
            elif rev_3y > 8:
                growth_pts += 3
                reasons.append(f"3Y revenue CAGR {rev_3y:.1f}% — steady growth")
            elif rev_3y < 0:
                growth_pts -= 5
                reasons.append(f"3Y revenue CAGR {rev_3y:.1f}% — sustained revenue decline")
        elif rev_growth is not None:
            if rev_growth > 0.20:
                growth_pts += 7
                reasons.append(f"Strong revenue growth ({rev_growth*100:.1f}% YoY)")
            elif rev_growth > 0.05:
                growth_pts += 3
                reasons.append(f"Moderate revenue growth ({rev_growth*100:.1f}% YoY)")
            elif rev_growth < -0.05:
                growth_pts -= 5
                reasons.append(f"Revenue declining ({rev_growth*100:.1f}% YoY)")

        # Earnings: use longest available CAGR, supplemented by trend signal
        pat_5y = screener_d.get("profit_growth_5y_pct")
        pat_3y = screener_d.get("profit_growth_3y_pct")
        eps_growth = info.get("earningsGrowth")
        if pat_5y is not None and horizon == "long":
            if pat_5y > 18:
                growth_pts += 6
                reasons.append(f"5Y profit CAGR {pat_5y:.1f}% — proven long-term compounder")
            elif pat_5y > 10:
                growth_pts += 3
                reasons.append(f"5Y profit CAGR {pat_5y:.1f}% — consistent long-term growth")
            elif pat_5y < 0:
                growth_pts -= 5
                reasons.append(f"5Y profit CAGR {pat_5y:.1f}% — declining earnings over 5 years")
        elif pat_3y is not None:
            if pat_3y > 20:
                growth_pts += 6
                reasons.append(f"3Y profit CAGR {pat_3y:.1f}% — strong compounding earnings")
            elif pat_3y > 10:
                growth_pts += 3
                reasons.append(f"3Y profit CAGR {pat_3y:.1f}% — sustained earnings growth")
            elif pat_3y < -10:
                growth_pts -= 5
                reasons.append(f"3Y profit CAGR {pat_3y:.1f}% — eroding profitability")
        elif eps_growth is not None:
            if eps_growth > 0.20:
                growth_pts += 5
                reasons.append(f"Strong EPS growth ({eps_growth*100:.1f}%)")
            elif eps_growth < -0.10:
                growth_pts -= 5
                reasons.append(f"EPS declining ({eps_growth*100:.1f}%)")

        # Quarterly trend supplements CAGR — capped separately so it can't double
        eps_trend = screener_d.get("eps_trend")
        trend_bonus = 0
        if eps_trend == "accelerating":
            trend_bonus = 3
            reasons.append("Quarterly PAT accelerating — 3 of 3 QoQ improvements")
        elif eps_trend == "mixed_positive":
            trend_bonus = 1
            reasons.append("Quarterly PAT trend broadly improving")
        elif eps_trend == "mixed_negative":
            trend_bonus = -1
            reasons.append("Quarterly PAT trend mostly declining")
        elif eps_trend == "decelerating":
            trend_bonus = -3
            reasons.append("Quarterly PAT declining 3 consecutive quarters")
        growth_pts = max(-15, min(15, growth_pts + trend_bonus))

        # ── BALANCE SHEET bucket (cap ±10) ────────────────────────────────────
        de = info.get("debtToEquity")
        if de is not None:
            if de > 300:
                balance_sheet -= 7
                reasons.append(f"Very high debt-to-equity ({de:.0f}%)")
            elif de > 150:
                balance_sheet -= 3
                reasons.append(f"Elevated debt-to-equity ({de:.0f}%)")
            elif de < 50:
                balance_sheet += 3
                reasons.append("Low debt — strong balance sheet")

        op_cf_series = screener_d.get("operating_cf_annual_cr") or []
        op_cf_latest = screener_d.get("operating_cf_latest_cr")
        if op_cf_latest is not None:
            if op_cf_latest < 0:
                balance_sheet -= 5
                reasons.append(f"Negative operating cash flow (₹{op_cf_latest:.0f} Cr) — earnings not converting to cash")
            elif len(op_cf_series) >= 3:
                recent_cf = [v for v in op_cf_series[-3:] if v is not None]
                if len(recent_cf) == 3 and recent_cf[0] > 0:
                    cf_growth = (recent_cf[-1] - recent_cf[0]) / abs(recent_cf[0]) * 100
                    if cf_growth > 30:
                        balance_sheet += 4
                        reasons.append(f"Operating cash flow grew {cf_growth:.0f}% over 3Y — strong cash generation")
                    elif cf_growth > 0:
                        balance_sheet += 2
                        reasons.append("Operating cash flow consistently positive and growing")
                    elif recent_cf[-1] < recent_cf[0] * 0.5:
                        balance_sheet -= 3
                        reasons.append("Operating cash flow declining significantly — watch earnings quality")

        try:
            from services.quality_factors import altman_zscore_signal, sloan_accruals_signal
            info_with_market = dict(info)
            info_with_market.setdefault("market", market)

            altman = altman_zscore_signal(info_with_market)
            z_zone = altman.get("z_zone", "unavailable")
            z      = altman.get("z_score")
            if z_zone == "distress":
                balance_sheet -= 8
                reasons.append(f"Altman Z-Score {z} — Distress Zone: balance sheet at risk")
            elif z_zone == "grey":
                balance_sheet -= 4
                reasons.append(f"Altman Z-Score {z} — Grey Zone: financial stress; monitor leverage")
            elif z_zone == "safe" and horizon in ("medium", "long"):
                balance_sheet += 3
                reasons.append(f"Altman Z-Score {z} — Safe Zone: strong balance sheet")

            accruals = sloan_accruals_signal(info_with_market)
            ar = accruals.get("accruals_ratio")
            if ar is not None:
                if ar < -5:
                    balance_sheet += 3
                    reasons.append(f"Low accruals ({ar}%) — earnings are cash-backed (Sloan 1996)")
                elif ar > 10:
                    balance_sheet -= 5
                    reasons.append(f"High accruals ({ar}%) — earnings outpacing cash flow; manipulation risk")
                elif ar > 5:
                    balance_sheet -= 3
                    reasons.append(f"Elevated accruals ({ar}%) — verify earnings quality")
        except Exception:
            pass
        balance_sheet = max(-10, min(10, balance_sheet))

        # ── GOVERNANCE bucket (cap ±10) ───────────────────────────────────────
        fii = screener_d.get("fii_holding_pct") or 0
        dii = screener_d.get("dii_holding_pct") or 0
        inst_total = fii + dii
        if inst_total > 50:
            governance += 4
            reasons.append(f"High institutional ownership (FII {fii:.1f}% + DII {dii:.1f}%) — strong smart-money conviction")
        elif inst_total > 25:
            governance += 2
            reasons.append(f"Solid institutional ownership (FII {fii:.1f}% + DII {dii:.1f}%)")

        dii_trend = screener_d.get("dii_quarterly_pct") or []
        if len(dii_trend) >= 4:
            dii_change = dii_trend[-1] - dii_trend[-4]
            if dii_change > 3:
                governance += 3
                reasons.append(f"DII (MF) stake up {dii_change:.1f}% in last 4 quarters — active accumulation")
            elif dii_change > 1:
                governance += 1
                reasons.append(f"DII (MF) stake rising (+{dii_change:.1f}%) — gradual accumulation")
            elif dii_change < -3:
                governance -= 3
                reasons.append(f"DII (MF) stake fell {dii_change:.1f}% in last 4 quarters — institutional selling")

        fii_trend = screener_d.get("fii_quarterly_pct") or []
        if len(fii_trend) >= 4:
            fii_change = fii_trend[-1] - fii_trend[-4]
            if fii_change > 3:
                governance += 2
                reasons.append(f"FII stake up {fii_change:.1f}% — foreign investor conviction")
            elif fii_change < -3:
                governance -= 2
                reasons.append(f"FII stake fell {fii_change:.1f}% — foreign capital exiting")

        promoter = screener_d.get("promoter_holding_pct")
        if promoter is not None:
            if promoter > 55:
                governance += 2
                reasons.append(f"High promoter holding ({promoter:.1f}%) — founder skin-in-the-game")
            elif promoter < 25:
                governance -= 2
                reasons.append(f"Low promoter holding ({promoter:.1f}%) — limited insider conviction")

        promoter_trend = screener_d.get("promoter_quarterly_pct") or []
        if len(promoter_trend) >= 4:
            promoter_change = promoter_trend[-1] - promoter_trend[-4]
            if promoter_change < -3:
                governance -= 4
                reasons.append(f"Promoter stake fell {abs(promoter_change):.1f}% over last 4 quarters — insider offloading")
            elif promoter_change < -1:
                governance -= 2
                reasons.append(f"Promoter stake slightly declining ({promoter_change:.1f}%) — monitor")
            elif promoter_change > 2:
                governance += 3
                reasons.append(f"Promoter stake increased {promoter_change:.1f}% — insider buying conviction")

        pledge = screener_d.get("promoter_pledge_pct")
        if market == "IN":
            try:
                from services.nse_pledge import get_promoter_pledge_pct
                nse_pledge = get_promoter_pledge_pct(symbol)
                if nse_pledge is not None:
                    pledge = nse_pledge
            except Exception:
                pass
        if pledge is not None:
            if pledge > 50:
                governance -= 8
                reasons.append(f"High promoter pledge ({pledge:.1f}%) — severe margin call risk; avoid")
            elif pledge > 25:
                governance -= 5
                reasons.append(f"Elevated promoter pledge ({pledge:.1f}%) — forced selling risk if stock falls")
            elif pledge > 10:
                governance -= 2
                reasons.append(f"Moderate promoter pledge ({pledge:.1f}%) — watch for increase")
            elif pledge == 0:
                governance += 2
                reasons.append("Zero promoter pledge — no forced selling risk")
        governance = max(-10, min(10, governance))

        # ── BANKING bucket (cap ±10, only fires for banks/NBFCs) ─────────────
        net_npa = screener_d.get("net_npa_pct")
        nim = screener_d.get("nim_pct")
        if net_npa is not None:
            if net_npa > 3:
                banking_pts -= 7
                reasons.append(f"High Net NPA ({net_npa:.1f}%) — asset quality concern")
            elif net_npa > 1.5:
                banking_pts -= 3
                reasons.append(f"Elevated Net NPA ({net_npa:.1f}%) — watch credit quality")
            elif net_npa < 0.5:
                banking_pts += 4
                reasons.append(f"Clean book — Net NPA {net_npa:.1f}%")
        if nim is not None:
            if nim > 4:
                banking_pts += 4
                reasons.append(f"Strong NIM ({nim:.1f}%) — healthy lending spread")
            elif nim < 2:
                banking_pts -= 3
                reasons.append(f"Thin NIM ({nim:.1f}%) — margin pressure")
        banking_pts = max(-10, min(10, banking_pts))

        # ── Assemble: base 50 + capped buckets ───────────────────────────────
        score = 50 + valuation_pts + profitability + growth_pts + balance_sheet + governance + banking_pts
        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _analyst_score(self, info: dict, market: str = "US") -> dict:
        """Score based on analyst consensus and price target."""
        score = 50
        reasons = []
        try:
            recommendation = info.get("recommendationKey", "")
            num_analysts = info.get("numberOfAnalystOpinions", 0)
            target_mean = info.get("targetMeanPrice")
            target_high = info.get("targetHighPrice")
            target_low  = info.get("targetLowPrice")
            current     = info.get("currentPrice") or info.get("regularMarketPrice")

            rec_map = {
                "strong_buy": (20, "BULLISH", "Strong Buy"),
                "buy":        (12, "BULLISH", "Buy"),
                "hold":       (0,  "NEUTRAL", "Hold"),
                "underperform": (-10, "BEARISH", "Underperform"),
                "sell":       (-18, "BEARISH", "Sell"),
                "strong_sell": (-20, "BEARISH", "Strong Sell"),
            }
            if recommendation in rec_map and num_analysts >= 3:
                adj, sig, label = rec_map[recommendation]
                score += adj
                reasons.append(f"{label} consensus from {num_analysts} analysts")

            if target_mean and current and current > 0:
                upside = (target_mean - current) / current * 100
                cur_sym = "₹" if market == "IN" else "$"
                if upside > 20:
                    score += 8
                    reasons.append(f"Analyst mean target {cur_sym}{target_mean:,.0f} implies {upside:.1f}% upside")
                elif upside > 8:
                    score += 4
                    reasons.append(f"Analyst mean target implies {upside:.1f}% upside")
                elif upside < -10:
                    score -= 8
                    reasons.append(f"Analyst mean target implies {upside:.1f}% downside")

        except Exception:
            pass
        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _week52_score(self, df: pd.DataFrame, info: dict) -> dict:
        """Score based on 52-week price position — breakouts and deep discounts."""
        score = 50
        reasons = []
        try:
            high52 = info.get("fiftyTwoWeekHigh") or float(df["High"].rolling(252).max().iloc[-1])
            low52  = info.get("fiftyTwoWeekLow")  or float(df["Low"].rolling(252).min().iloc[-1])
            current = float(df["Close"].iloc[-1])

            if high52 and low52 and high52 > low52:
                pct_range = (current - low52) / (high52 - low52) * 100

                if pct_range >= 90:
                    score += 12
                    reasons.append(f"Trading near 52-week high ({pct_range:.0f}% of range) — strong momentum, breakout territory")
                elif pct_range >= 70:
                    score += 6
                    reasons.append(f"In upper half of 52-week range ({pct_range:.0f}%) — momentum intact")
                elif pct_range <= 10:
                    # Deep in 52W range — could be value or falling knife
                    score -= 6
                    reasons.append(f"Near 52-week low ({pct_range:.0f}% of range) — watch for support; potential value or continued weakness")
                elif pct_range <= 30:
                    score -= 2
                    reasons.append(f"In lower third of 52-week range ({pct_range:.0f}%) — below trend")

                pct_from_high = (current - high52) / high52 * 100
                if pct_from_high > -5:
                    reasons.append(f"Within {abs(pct_from_high):.1f}% of 52-week high — potential breakout")

        except Exception:
            pass
        return {"score": max(0, min(100, score)), "reasons": reasons}

    def _aggregate_sentiment(self, articles: list) -> dict:
        if not articles:
            # No news fetched — return neutral but flag as data-unavailable so UI
            # can display "No news data" rather than "Neutral sentiment"
            return {"score": 50, "label": "NEUTRAL", "bullish": 0, "bearish": 0, "data_available": False}
        bullish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BULLISH")
        bearish = sum(1 for a in articles if a.get("sentiment", {}).get("label") == "BEARISH")
        labeled = bullish + bearish
        # Use only labeled articles in the denominator — neutral articles should not
        # dilute bullish signal (5 bullish + 5 neutral ≠ 5 bullish + 5 bearish)
        if labeled > 0:
            score = int(50 + (bullish - bearish) / labeled * 50)
        else:
            score = 50  # all neutral — genuine neutral signal
        label = "BULLISH" if score > 60 else "BEARISH" if score < 40 else "NEUTRAL"
        return {"score": score, "label": label, "bullish": bullish, "bearish": bearish, "data_available": True}

    def _quality_gate(self, info: dict, df: pd.DataFrame, horizon: str = "medium") -> tuple[bool, list[str]]:
        """
        Hard quality gate — run BEFORE scoring.
        Stocks failing this gate are rejected immediately and never scored.
        Checks for fundamentally broken financials only.
        """
        rejections: list[str] = []

        try:
            # Loss-making with negative operating cash flows
            roe = info.get("returnOnEquity")
            profit_margin = info.get("profitMargins")
            # Use explicit None check so zero cash flow is correctly treated as non-positive
            op_cf = info.get("operatingCashflow")
            if op_cf is None:
                op_cf = info.get("operatingCashflows")

            # Reject if EITHER metric is severely negative (was: AND — too strict)
            if roe is not None and roe < -0.10:
                rejections.append(f"Severely negative ROE ({roe*100:.1f}%) — destroying shareholder value")
            elif profit_margin is not None and profit_margin < -0.15:
                rejections.append(f"Deeply loss-making: profit margins {profit_margin*100:.1f}%")

            # OCF gate only applies to medium/long — growth stocks may have negative OCF short-term
            if horizon != "short" and op_cf is not None and op_cf <= 0:
                rejections.append("Non-positive operating cash flows — core business not generating cash")

            # Extreme leverage — exclude NBFC/banks by checking sector
            sector = (info.get("sector") or "").lower()
            is_financial = any(k in sector for k in ("financial", "bank", "insurance"))
            de = info.get("debtToEquity")
            if not is_financial and de and de > 500:
                rejections.append(
                    f"Extreme leverage (D/E {de:.0f}%) — balance sheet risk too high to score"
                )

        except Exception:
            pass

        return (len(rejections) == 0), rejections

    def _composite_signal(self, tech, fund, sentiment, horizon, weights, regime,
                          global_ctx: dict | None = None,
                          analyst_score: dict | None = None,
                          week52_score: dict | None = None,
                          quality: dict | None = None,
                          df: pd.DataFrame | None = None,
                          info: dict | None = None,
                          symbol: str = "",
                          market: str = "US"):
        tech_score = tech.get("score", 50)

        # ── Step 1: Raw composite from core signals ───────────────────────────
        # Built as named contributions so the exact point breakdown can be
        # surfaced via the factor-attribution endpoint — every term added to
        # raw_score below has a matching entry in `contributions`.
        contributions: dict[str, float] = {}

        # If no news was found, redistribute sentiment weight to tech+fund
        # proportionally — a score-50 default is not a genuine neutral signal
        effective_weights = dict(weights)
        if not sentiment.get("data_available", True):
            sent_w = effective_weights.pop("sentiment", 0)
            total_tf = effective_weights["tech"] + effective_weights["fund"]
            if total_tf > 0:
                effective_weights["tech"]  += sent_w * (effective_weights["tech"] / total_tf)
                effective_weights["fund"]  += sent_w * (effective_weights["fund"] / total_tf)
            effective_weights["sentiment"] = 0
        else:
            effective_weights["sentiment"] = weights["sentiment"]

        contributions["technical"]   = tech_score * effective_weights["tech"]
        contributions["fundamental"] = fund["score"] * effective_weights["fund"]
        contributions["sentiment"]   = sentiment["score"] * effective_weights["sentiment"]

        # Local market regime adjustment (±8)
        contributions["regime"] = (regime or {}).get("score_adj", 0)

        # Global macro adjustment — weighted by horizon (short term most sensitive)
        global_adj_weight = {"short": 0.15, "medium": 0.10, "long": 0.05}.get(horizon, 0.10)
        contributions["global_macro"] = 0.0
        if global_ctx:
            global_base_score = global_ctx.get("score", 50)
            stock_adj = global_ctx.get("stock_score_adj", 0)
            contributions["global_macro"] = (global_base_score - 50) * global_adj_weight + stock_adj

        # Analyst consensus — weight scales with horizon (long-term should trust analysts more)
        analyst_weight = {"short": 0.08, "medium": 0.12, "long": 0.18}.get(horizon, 0.08)
        contributions["analyst"] = 0.0
        if analyst_score:
            contributions["analyst"] = (analyst_score.get("score", 50) - 50) * analyst_weight

        # 52-week position nudge (±4 max)
        contributions["week52"] = 0.0
        if week52_score:
            contributions["week52"] = (week52_score.get("score", 50) - 50) * 0.06

        # Quality factors — professional-grade signal (±10 max)
        contributions["quality"] = 0.0
        if quality and quality.get("score") is not None:
            contributions["quality"] = (quality["score"] - 50) * 0.12

        raw_score_unclamped = sum(contributions.values())
        raw_score = max(0, min(100, raw_score_unclamped))
        # Clamping can change the sum — capture the delta so contributions
        # always sum exactly to raw_score (and therefore to composite below).
        contributions["clamp_adjustment"] = raw_score - raw_score_unclamped

        # ── Step 2: Risk penalty — separate deduction step ────────────────────
        # This ensures high-risk stocks can't "score their way" to a BUY signal;
        # risk is always subtracted last so it can override a strong raw signal.
        risk_penalty, penalty_reasons = _compute_risk_penalty(
            info=info or {},   # use the real info dict fetched in predict()
            df=df if df is not None else pd.DataFrame(),
            quality=quality,
        )
        # Also probe quality breakdown for fast D/E and FCF checks using fund data
        # (info dict may not be fully accessible here — done best-effort)

        contributions["risk_penalty"] = -risk_penalty

        composite = max(0, raw_score - risk_penalty)
        composite_r = round(composite)  # use rounded value for both display and signal — no split-brain
        if composite < 0:
            # max(0, ...) clamp on composite itself — extend clamp_adjustment
            # so sum(contributions) == composite holds even in this edge case.
            contributions["clamp_adjustment"] += composite - (raw_score - risk_penalty)
        # Rounding composite -> composite_r (the displayed/persisted score) can
        # introduce up to ±0.5 drift versus the unrounded contribution sum —
        # fold that drift in so contributions sum EXACTLY to composite_r.
        contributions["rounding_adjustment"] = composite_r - sum(contributions.values())

        if composite_r >= 60:
            signal = "BUY"
        elif composite_r >= 45:
            signal = "HOLD"
        else:
            signal = "SELL"

        if signal == "BUY":
            # Linear over the full BUY range [60,100]
            confidence = round(max(0, min(100, (composite_r - 60) / 40 * 100)))
        elif signal == "SELL":
            # Linear over the full SELL range [0,45)
            confidence = round(max(0, min(100, (45 - composite_r) / 45 * 100)))
        else:
            # HOLD confidence: highest near the midpoint (52), decays toward the thresholds
            confidence = max(0, min(100, 50 - int(abs(composite_r - 52) * 2)))
        score_band = _score_label(composite_r)

        confidence_score, confidence_band, confidence_components = self._confidence_engine(
            signal=signal, tech_score=tech_score, fund_score=fund["score"],
            sentiment_score=sentiment["score"], quality=quality, regime=regime,
            info=info or {}, horizon=horizon, sentiment_obj=sentiment, market=market,
        )

        # Build reasoning — most impactful signals first
        reasoning = []

        # Score band (top-line context for the investor)
        reasoning.append({
            "indicator": "AI Score",
            "signal": "INFO",
            "reason": f"Composite score: {composite_r}/100 — {score_band}"
                      + (f" (raw {round(raw_score)} − risk penalty {risk_penalty})" if risk_penalty > 0 else ""),
        })

        # Risk penalty reasons (if any)
        for r in penalty_reasons:
            reasoning.append({"indicator": "Risk Flag", "signal": "BEARISH", "reason": r})

        # Technical breakdown
        reasoning.extend(tech["breakdown"][:3])
        if tech.get("candlestick", {}).get("patterns"):
            reasoning.append({
                "indicator": "Candlestick",
                "signal": tech["candlestick"]["signal"],
                "reason": ", ".join(tech["candlestick"]["patterns"]),
            })

        # 52-week context
        if week52_score:
            for r in week52_score.get("reasons", [])[:1]:
                reasoning.append({"indicator": "Price Level", "signal": "INFO", "reason": r})

        # Fundamentals
        for r in fund["reasons"][:3]:
            reasoning.append({"indicator": "Fundamental", "signal": "INFO", "reason": r})

        # Analyst consensus
        if analyst_score:
            for r in analyst_score.get("reasons", [])[:2]:
                reasoning.append({"indicator": "Analyst", "signal": "INFO", "reason": r})

        # Local market regime
        reasoning.append({"indicator": "Market Regime", "signal": regime["trend"], "reason": regime["reason"]})

        # Global macro — general signals
        if global_ctx:
            for r in global_ctx.get("reasons", [])[:4]:
                reasoning.append(r)
            # Stock-specific macro impact
            for r in global_ctx.get("stock_reasons", [])[:3]:
                reasoning.append(r)

        # Quality factors — top reasons from each dimension
        if quality:
            for r in quality.get("reasons", [])[:6]:
                reasoning.append(r)

        # News sentiment
        if sentiment["label"] != "NEUTRAL":
            reasoning.append({
                "indicator": "Sentiment",
                "signal": sentiment["label"],
                "reason": f"{sentiment['bullish']} bullish vs {sentiment['bearish']} bearish in recent news"
            })

        # ── Log prediction for track record / IC engine ──────────────────────
        try:
            from services.alpha_engine.store import log_prediction
            current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or (df["Close"].iloc[-1] if not df.empty else 0))
            log_prediction(
                symbol=symbol,
                horizon=horizon,
                factor_zscores={
                    "tech":      round(tech_score / 100, 4),
                    "fund":      round(fund["score"] / 100, 4),
                    "sentiment": round(sentiment["score"] / 100, 4),
                    "quality":   round(quality["score"] / 100, 4) if quality else None,
                },
                combined_alpha=round(composite_r / 100, 4),
                meta_alpha=None,
                signal=signal,
                price=current_price,
                regime_label=regime.get("trend", ""),
            )
        except Exception as e:
            log.warning("Failed to log prediction for %s: %s", symbol, e)

        return (signal, confidence, reasoning, score_band, contributions, composite_r,
                confidence_score, confidence_band, confidence_components)

    def _confidence_engine(self, signal, tech_score, fund_score, sentiment_score,
                           quality, regime, info, horizon: str = "short", **kwargs) -> tuple[int, str, dict]:
        """
        Multi-factor confidence score (0-100). Answers "how much should you
        trust this signal?" rather than "how strong is this signal?".

        Components:
          data_completeness          — % of 8 key fundamental fields present
          factor_agreement           — % of factors agreeing with signal direction
          earnings_stability         — quality breakdown earnings_revision (India; 50 elsewhere)
          regime_certainty           — BULL/BEAR more certain than SIDEWAYS
          historical_factor_reliability — live IC-based reliability from ic_engine;
                                          falls back to 50 until 60+ outcome pairs exist
        """
        # ── data_completeness ────────────────────────────────────────────────
        # Sector-aware: financial stocks (banks, insurance, NBFCs) are evaluated
        # on different KPIs — D/E and FCF are structurally N/A for them.
        screener_d = info.get("_screener_data") or {}
        sector = (info.get("sector") or "").lower()
        industry = (info.get("industry") or "").lower()
        is_financial = (
            "financial" in sector
            or "bank" in industry
            or "insurance" in industry
            or "nbfc" in industry
        )

        if is_financial:
            # For banks/insurance/NBFCs: D/E, FCF, ROCE are structurally N/A.
            # Use only fields that are reliably populated for financial stocks.
            # 6 from yfinance (all confirmed available) + 4 from screener = 10 total.
            key_fields_info = [
                "trailingPE",       # confirmed available from yfinance for banks
                "returnOnEquity",   # confirmed available
                "revenueGrowth",    # confirmed available (NII growth proxy)
                "profitMargins",    # confirmed available
                "earningsGrowth",   # confirmed available
                "beta",             # confirmed available
            ]
            key_fields_screener = [
                "sales_growth_3y_pct",  # calculated from P&L annual data (fallback)
                "profit_growth_3y_pct", # calculated from P&L annual data (fallback)
                "fii_holding_pct",      # confirmed from shareholding section
                "promoter_holding_pct", # confirmed from shareholding section
            ]
        else:
            is_indian = kwargs.get("market", "US") == "IN"
            if is_indian:
                # Indian non-financial: freeCashflow & earningsGrowth structurally
                # absent from yfinance for NSE — use screener fields instead
                key_fields_info = [
                    "trailingPE",
                    "returnOnEquity",
                    "revenueGrowth",
                    "debtToEquity",
                    "profitMargins",
                    "beta",
                ]
                key_fields_screener = [
                    "sales_growth_3y_pct",
                    "profit_growth_3y_pct",
                    "roce_pct",
                    "fii_holding_pct",
                    "promoter_holding_pct",
                ]
            else:
                # US stocks — yfinance returns full fundamental set
                key_fields_info = [
                    "trailingPE",
                    "returnOnEquity",
                    "revenueGrowth",
                    "debtToEquity",
                    "profitMargins",
                    "earningsGrowth",
                    "freeCashflow",
                    "beta",
                    "returnOnCapitalEmployed",
                ]
                key_fields_screener = [
                    "sales_growth_3y_pct",
                    "profit_growth_3y_pct",
                    "fii_holding_pct",
                    "promoter_holding_pct",
                ]

        present = sum(1 for k in key_fields_info if info.get(k) is not None)
        present += sum(1 for k in key_fields_screener if screener_d.get(k) is not None)
        total_fields = len(key_fields_info) + len(key_fields_screener)
        data_completeness = round(present / total_fields * 100)

        # ── factor_agreement ─────────────────────────────────────────────────
        # Only include sentiment if real data was available — score-50 default
        # is not a signal and would artificially inflate agreement on neutral
        sentiment_obj = kwargs.get("sentiment_obj") or {}
        factor_scores = [tech_score, fund_score]
        if sentiment_obj.get("data_available", True):
            factor_scores.append(sentiment_score)
        if quality and quality.get("score") is not None:
            factor_scores.append(quality["score"])

        def _agrees(score: float) -> bool:
            if signal == "BUY":  return score >= 55
            if signal == "SELL": return score <= 45
            return 40 <= score <= 60

        factor_agreement = round(sum(1 for s in factor_scores if _agrees(s)) / len(factor_scores) * 100)

        # ── earnings_stability ───────────────────────────────────────────────
        # Primary: earnings_revision from quality factors (yfinance EPS history)
        # Fallback: quarterly PAT trend from screener.in (works for all Indian stocks)
        earnings_stability = 50
        earnings_stability_available = False
        if quality:
            er = quality.get("breakdown", {}).get("earnings_revision")
            if isinstance(er, dict) and er.get("score") is not None:
                earnings_stability = round(er["score"])
                earnings_stability_available = True

        if not earnings_stability_available:
            pat_history = screener_d.get("quarterly_pat_cr") or []
            # Need at least 4 quarters to assess a trend
            if len(pat_history) >= 4:
                recent = pat_history[-4:]
                # Score: proportion of profitable quarters + growth trend
                profitable = sum(1 for p in recent if p > 0)
                profit_ratio = profitable / len(recent)  # 0→1
                # Growth: compare latest half vs prior half
                mid = len(recent) // 2
                prior_avg = sum(recent[:mid]) / mid if mid else 0
                recent_avg = sum(recent[mid:]) / (len(recent) - mid)
                if prior_avg > 0:
                    growth = (recent_avg - prior_avg) / abs(prior_avg)
                    growth_score = max(-1.0, min(1.0, growth))
                else:
                    growth_score = 0.0
                # Combine: 60% profitability consistency + 40% growth direction
                raw = profit_ratio * 0.6 + (0.5 + growth_score * 0.5) * 0.4
                earnings_stability = round(raw * 100)
                earnings_stability_available = True

        # ── regime_certainty ─────────────────────────────────────────────────
        # Continuous score from actual market momentum rather than a 3-bucket flag.
        # Uses the 3M return of the benchmark index, normalised to 0-100:
        #   +10%+ return  → ~90-100 (very clear bull)
        #   -10%+ decline → ~0-10  (very clear bear)
        #   0% / sideways → ~50
        score_adj = regime.get("score_adj", 0)
        trend = regime.get("trend", "SIDEWAYS")
        if trend == "SIDEWAYS":
            regime_certainty = 50
        else:
            # score_adj is ±8; scale to [50, 100] for BULL, [0, 50) for BEAR
            # with abs capped so the range is [25, 100] (never fully 0 or 100
            # unless we have a true extreme move)
            regime_certainty = round(50 + score_adj * 5)
            regime_certainty = max(10, min(90, regime_certainty))

        # ── historical_factor_reliability — from live IC engine ───────────────
        # IC values reflect how well each factor has predicted returns historically.
        # We normalise avg(positive IC) against the "meaningful" threshold of 0.05
        # to get a 0-100 reliability score. Falls back to 50 (neutral) when fewer
        # than MIN_REAL_DATA_ROWS outcome pairs exist.
        historical_factor_reliability = 50
        historical_reliability_available = False
        try:
            from services.alpha_engine.ic_engine import get_ic_values
            from services.alpha_engine.store import count_training_rows
            from services.alpha_engine.ic_engine import MIN_REAL_DATA_ROWS
            if count_training_rows(horizon) >= MIN_REAL_DATA_ROWS:
                ic_vals = get_ic_values(horizon)
                avg_positive_ic = sum(max(0.0, v) for v in ic_vals.values()) / max(1, len(ic_vals))
                # Normalise: IC of 0.07+ → 100; IC of 0 → 0. Clamp to [0,100].
                historical_factor_reliability = round(min(100, avg_positive_ic / 0.07 * 100))
                historical_reliability_available = True
        except Exception as e:
            log.debug("IC engine not available for confidence: %s", e)

        components = {
            "data_completeness":              data_completeness,
            "factor_agreement":               factor_agreement,
            "earnings_stability":             earnings_stability,
            "regime_certainty":               regime_certainty,
            "historical_factor_reliability":  historical_factor_reliability,
            "_historical_reliability_live":   historical_reliability_available,
            "_earnings_stability_live":       earnings_stability_available,
        }

        if historical_reliability_available:
            weights = {
                "data_completeness":           0.25,
                "factor_agreement":            0.25,
                "earnings_stability":          0.15,
                "regime_certainty":            0.15,
                "historical_factor_reliability": 0.20,
            }
        else:
            weights = {
                "data_completeness":           0.3125,
                "factor_agreement":            0.3125,
                "earnings_stability":          0.1875,
                "regime_certainty":            0.1875,
                "historical_factor_reliability": 0.0,
            }

        confidence_score = round(sum(components[k] * weights[k] for k in weights))
        confidence_score = max(0, min(100, confidence_score))

        if confidence_score >= 80:
            confidence_band = "High"
        elif confidence_score >= 60:
            confidence_band = "Medium"
        else:
            confidence_band = "Low"

        return confidence_score, confidence_band, components

    def _estimate_target(self, price, signal, confidence, horizon, df, info):
        conf_factor = max(0.5, confidence / 100)

        if horizon == "short":
            atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
            move = atr * 2.5 * conf_factor
            if signal == "BUY":
                return round(price + move, 2)
            elif signal == "SELL":
                return round(price - move, 2)
            return round(price * (1 + 0.02 * conf_factor), 2)

        elif horizon == "medium":
            analyst_target = info.get("targetMeanPrice")
            if analyst_target and analyst_target > 0:
                blend = (analyst_target * 0.7 + price * 0.3)
                if signal == "BUY":
                    return round(max(blend, price * 1.05), 2)
                elif signal == "SELL":
                    return round(min(blend, price * 0.95), 2)
                # HOLD: cap within ±8% — a big target contradicts a HOLD signal
                return round(min(max(blend, price * 0.96), price * 1.08), 2)

            monthly_ret = df["Close"].pct_change(21).dropna()
            avg_monthly = monthly_ret.mean()
            projected = price * (1 + avg_monthly * 3 * conf_factor)
            if signal == "BUY":
                return round(max(projected, price * 1.05), 2)
            elif signal == "SELL":
                return round(min(projected, price * 0.92), 2)
            # HOLD: cap within ±8%
            return round(min(max(projected, price * 0.96), price * 1.08), 2)

        else:  # long
            pe = info.get("trailingPE") or info.get("forwardPE")
            # Use explicit None check — earningsGrowth=0.0 is valid and must not fall
            # through to revenueGrowth (Python's `or` treats 0.0 as falsy)
            eps_growth_raw = info.get("earningsGrowth")
            if eps_growth_raw is None:
                eps_growth_raw = info.get("revenueGrowth")
            if eps_growth_raw is None:
                eps_growth_raw = 0.08
            # Cap to a realistic sustainable annual CAGR for a 2-3 year horizon.
            # yfinance returns trailing TTM growth which spikes wildly on one-time events.
            eps_growth = max(-0.30, min(float(eps_growth_raw), 0.35))

            analyst_target = info.get("targetMeanPrice")

            if analyst_target and analyst_target > 0:
                # Analyst targets are 12-18 month forward prices — don't compound them
                # again. Just extrapolate one additional year at the capped growth rate.
                long_target = analyst_target * (1 + max(eps_growth, 0.05))
            elif pe and pe > 0:
                eps_est = price / pe
                eps_future = eps_est * ((1 + max(eps_growth, 0.05)) ** 3)
                long_target = eps_future * pe
            else:
                long_target = price * ((1 + max(eps_growth, 0.05)) ** 3)

            # Hard ceiling: long-term target must stay within ±150% of current price.
            # A prediction of >2.5x is speculative fantasy, not a useful trade level.
            long_target = min(long_target, price * 2.5)
            long_target = max(long_target, price * 0.40)

            if signal == "BUY":
                return round(max(long_target, price * 1.15), 2)
            elif signal == "SELL":
                return round(min(long_target, price * 0.80), 2)
            # HOLD: cap within ±10% — large upside should trigger BUY not HOLD
            return round(min(max(long_target, price * 0.95), price * 1.10), 2)
