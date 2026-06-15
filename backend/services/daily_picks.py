"""
Daily Picks Service
Screens Nifty 100 stocks, runs prediction engine on each,
returns top 5 BUY signals per horizon (short/medium/long).
Results cached to picks_cache.json so the endpoint is instant after generation.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from services.prediction_engine import PredictionEngine

CACHE_FILE = os.path.join(os.path.dirname(__file__), "../picks_cache.json")

# Nifty 100 — liquid, well-known Indian stocks
NIFTY100 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN",
    "SUNPHARMA", "HCLTECH", "WIPRO", "ULTRACEMCO", "BAJFINANCE",
    "NESTLEIND", "TECHM", "POWERGRID", "NTPC", "ONGC",
    "COALINDIA", "TATAMOTORS", "ADANIENT", "ADANIPORTS", "BAJAJFINSV",
    "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "GRASIM",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "INDUSINDBK", "JSWSTEEL",
    "M&M", "SBILIFE", "SHREECEM", "TATACONSUM", "TATASTEEL",
    "APOLLOHOSP", "BAJAJ-AUTO", "BPCL", "BRITANNIA", "CHOLAFIN",
    "DABUR", "DLF", "DMART", "GODREJCP", "HAVELLS",
    "ICICIPRULI", "INDHOTEL", "IOC", "IRCTC", "LUPIN",
    "MCDOWELL-N", "MUTHOOTFIN", "NAUKRI", "PIDILITIND", "PNB",
    "SAIL", "SIEMENS", "SRF", "TORNTPHARM", "TRENT",
    "TVSMOTOR", "UBL", "VEDL", "VOLTAS", "ZOMATO",
    "PAYTM", "NYKAA", "POLICYBZR", "DELHIVERY", "MARICO",
    "BANDHANBNK", "BANKBARODA", "FEDERALBNK", "HAL", "BHEL",
    "CANBK", "CONCOR", "GAIL", "HINDPETRO", "IDFCFIRSTB",
    "LICHSGFIN", "MOTHERSON", "MPHASIS", "NMDC", "OBEROIRLTY",
    "OFSS", "PERSISTENT", "PIIND", "RECLTD", "SUPREMEIND",
]


HORIZON_LABELS = {
    "short":  ("1–5 days",   "short-term"),
    "medium": ("2–4 weeks",  "medium-term"),
    "long":   ("3–6 months", "long-term"),
}


def _build_summary(result: dict, horizon: str) -> str:
    """Compose a human-readable analyst-style summary from prediction engine output."""
    name       = result.get("company_name", result.get("symbol", ""))
    confidence = result.get("confidence", 0)
    price      = result.get("current_price", 0)
    target     = result.get("target_price", 0)
    upside     = round((target - price) / price * 100, 1) if price and target else 0
    period, term = HORIZON_LABELS.get(horizon, ("", ""))

    tech  = result.get("technical", {})
    fund  = result.get("fundamental_score", {})
    sent  = result.get("sentiment_score", {})
    reg   = result.get("market_regime", {})
    glob  = result.get("global_context") or {}

    # Tech strength label
    tech_score = tech.get("score", 50)
    if tech_score >= 70:
        tech_label = "strong bullish technical setup"
    elif tech_score >= 60:
        tech_label = "moderately bullish technical momentum"
    else:
        tech_label = "emerging bullish technical signals"

    # Fundamental label
    fund_score = fund.get("score", 50)
    if fund_score >= 70:
        fund_label = "solid fundamental backing"
    elif fund_score >= 55:
        fund_label = "decent fundamental support"
    else:
        fund_label = "neutral fundamental profile"

    # Sentiment label
    sent_label = ""
    if sent.get("label") == "BULLISH" or sent.get("score", 50) >= 60:
        sent_label = " News sentiment is bullish."
    elif sent.get("label") == "BEARISH" or sent.get("score", 50) <= 40:
        sent_label = " Recent news sentiment leans cautious, but technicals override."

    # Market regime
    regime_note = ""
    reg_trend = reg.get("trend", "")
    if reg_trend == "BULL":
        regime_note = " Domestic market is in an uptrend."
    elif reg_trend == "BEAR":
        regime_note = " Domestic market is under pressure — tight stop-loss recommended."

    # Global macro note
    global_note = ""
    global_score = glob.get("score")
    if global_score is not None:
        levels = glob.get("levels", {})
        changes = glob.get("changes", {})
        vix = levels.get("vix")
        sp500_chg = changes.get("sp500")
        crude_chg = changes.get("crude_brent")
        usdinr = levels.get("usdinr")

        parts = []
        if global_score >= 60:
            parts.append("Global macro environment is supportive")
        elif global_score <= 40:
            parts.append("Global macro headwinds are present")

        if vix and vix > 20:
            parts.append(f"VIX elevated at {vix:.0f} (risk-off)")
        elif vix and vix < 14:
            parts.append(f"VIX calm at {vix:.0f} (risk-on)")

        if sp500_chg is not None and abs(sp500_chg) > 0.5:
            parts.append(f"S&P 500 {sp500_chg:+.1f}%")

        if crude_chg is not None and abs(crude_chg) > 1.0:
            parts.append(f"Brent crude {crude_chg:+.1f}%")

        if usdinr:
            parts.append(f"USD/INR ₹{usdinr:.1f}")

        if parts:
            global_note = " " + "; ".join(parts) + "."

    # Confidence tone
    if confidence >= 70:
        conf_tone = f"with high conviction ({confidence}% AI confidence)"
    elif confidence >= 50:
        conf_tone = f"with moderate confidence ({confidence}% AI confidence)"
    else:
        conf_tone = f"as a speculative opportunity ({confidence}% AI confidence)"

    # Quality factor highlights
    quality_note = ""
    qf = result.get("quality_factors") or {}
    qf_breakdown = qf.get("breakdown") or {}
    val_score  = qf_breakdown.get("valuation", {})
    risk_score = qf_breakdown.get("risk_management", {})
    flow_score = qf_breakdown.get("inst_flow", {})
    piotroski  = qf.get("piotroski")

    quality_parts = []
    if isinstance(val_score, dict) and val_score.get("score", 50) >= 65:
        quality_parts.append("attractively valued")
    elif isinstance(val_score, dict) and val_score.get("score", 50) <= 35:
        quality_parts.append("stretched valuation — risk to monitor")
    if isinstance(risk_score, dict) and risk_score.get("score", 50) >= 65:
        quality_parts.append("strong risk-adjusted return profile")
    if isinstance(flow_score, dict) and flow_score.get("score", 50) >= 65:
        quality_parts.append("institutional accumulation signals present")
    if piotroski is not None and piotroski >= 7:
        quality_parts.append(f"Piotroski F-Score {piotroski}/9 (high-quality financials)")
    if quality_parts:
        quality_note = " " + "; ".join(quality_parts[:2]).capitalize() + "."

    summary = (
        f"{name} is flagged as a {term} BUY {conf_tone}. "
        f"The AI engine detects a {tech_label} combined with {fund_label}.{sent_label}"
        f"{regime_note}{global_note}{quality_note} "
        f"Target ₹{target:,.2f} implies {upside}% upside within {period}."
    )
    return summary


def _predict_stock(symbol: str, horizon: str) -> dict | None:
    """Run prediction engine for one stock + horizon. Returns None on error."""
    try:
        engine = PredictionEngine()
        result = engine.predict(symbol, "IN", horizon)
        if result and result.get("signal") == "BUY":
            reasoning = result.get("reasoning", [])
            trade = result.get("trade_levels", {})
            return {
                "symbol": symbol,
                "name": result.get("company_name", symbol),
                "price": result.get("current_price"),
                "target": result.get("target_price"),
                "stop_loss": trade.get("stop_loss"),
                "entry_low": trade.get("entry_low"),
                "entry_high": trade.get("entry_high"),
                "risk_reward": trade.get("risk_reward"),
                "confidence": result.get("confidence"),
                "tech_score": result.get("technical", {}).get("score"),
                "fund_score": result.get("fundamental_score", {}).get("score"),
                "sentiment": result.get("sentiment_score", {}).get("label", "NEUTRAL"),
                "reasoning": reasoning,
                "summary": _build_summary(result, horizon),
                "global_context": result.get("global_context"),
                "quality_factors": result.get("quality_factors"),
                "horizon": horizon,
            }
    except Exception:
        pass
    return None


def generate_picks() -> dict:
    """
    Run predictions on NIFTY100 for all 3 horizons using a thread pool.
    Returns dict with short/medium/long each containing top 5 BUY picks.
    Takes ~5-10 minutes on Render free tier.
    """
    print(f"[picks] Starting generation for {len(NIFTY100)} stocks × 3 horizons …")
    start = time.time()

    tasks = [(sym, h) for sym in NIFTY100 for h in ("short", "medium", "long")]
    results: dict[str, list] = {"short": [], "medium": [], "long": []}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_predict_stock, sym, h): (sym, h) for sym, h in tasks}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 30 == 0:
                print(f"[picks] {done}/{len(tasks)} done …")
            r = future.result()
            if r:
                results[r["horizon"]].append(r)

    # Sort by confidence, keep top 5
    picks = {}
    for horizon, items in results.items():
        picks[horizon] = sorted(items, key=lambda x: x["confidence"], reverse=True)[:5]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "picks": picks,
    }

    with open(CACHE_FILE, "w") as f:
        json.dump(payload, f)

    elapsed = round(time.time() - start, 1)
    total = sum(len(v) for v in picks.values())
    print(f"[picks] Done in {elapsed}s — {total} BUY picks found.")

    # Send to Telegram if configured
    try:
        from services.telegram_bot import send_picks_to_telegram
        send_picks_to_telegram(picks)
    except Exception as e:
        print(f"[telegram] Error: {e}")

    return payload


def get_cached_picks() -> dict | None:
    """Return cached picks from disk, or None if not yet generated."""
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None
