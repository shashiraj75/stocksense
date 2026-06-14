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


def _predict_stock(symbol: str, horizon: str) -> dict | None:
    """Run prediction engine for one stock + horizon. Returns None on error."""
    try:
        engine = PredictionEngine()
        result = engine.predict(symbol, "IN", horizon)
        if result and result.get("signal") == "BUY":
            return {
                "symbol": symbol,
                "name": result.get("company_name", symbol),
                "price": result.get("current_price"),
                "target": result.get("target_price"),
                "confidence": result.get("confidence"),
                "reasoning": result.get("reasoning", [])[:2],
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
