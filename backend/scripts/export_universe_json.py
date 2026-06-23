"""
Exports services/stock_universe.py's US_STOCKS/IN_STOCKS/CRYPTO_COINS into
frontend/public/stock_universe.json — the file the frontend's autocomplete
(useStockSearch, SearchBar, Watchlist) actually reads at runtime.

These two files were previously kept in sync by hand (e.g. GLD/SLV/GOLDBEES/
SILVERBEES were each added separately to both, in a past session) with no
automated link between them — regenerating stock_universe.py alone does
nothing for the user-facing autocomplete until this export also runs.

Run from the backend directory, after generate_stock_universe.py:
  python scripts/export_universe_json.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from services.stock_universe import US_STOCKS, IN_STOCKS, CRYPTO_COINS

OUT_FILE = os.path.join(os.path.dirname(__file__), "../../frontend/public/stock_universe.json")


def run():
    data = {
        "US": [{"symbol": sym, "name": name, "market": "US"} for sym, name in US_STOCKS],
        "IN": [{"symbol": sym, "name": name, "market": "IN"} for sym, name in IN_STOCKS],
        "CRYPTO": [{"symbol": sym, "name": name, "market": "CRYPTO"} for sym, name in CRYPTO_COINS],
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"✅  Written to {OUT_FILE}")
    print(f"    US: {len(data['US'])}  |  IN: {len(data['IN'])}  |  CRYPTO: {len(data['CRYPTO'])}")


if __name__ == "__main__":
    run()
