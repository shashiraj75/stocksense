import yfinance as yf
from typing import Optional

# yfinance is completely free — no API key needed
MARKET_SUFFIX = {"US": "", "IN": ".NS"}


class MarketDataService:
    def _sym(self, symbol: str, market: str) -> str:
        return symbol + MARKET_SUFFIX.get(market, "")

    async def get_quote(self, symbol: str, market: str) -> Optional[dict]:
        try:
            t = yf.Ticker(self._sym(symbol, market))
            fi = t.fast_info
            price = fi.last_price
            prev = fi.previous_close
            return {
                "symbol": symbol,
                "market": market,
                "price": round(price, 2),
                "prev_close": round(prev, 2),
                "change": round(price - prev, 2),
                "change_pct": round((price - prev) / prev * 100, 2),
                "volume": int(fi.three_month_average_volume or 0),
                "market_cap": fi.market_cap,
                "fifty_two_week_high": fi.year_high,
                "fifty_two_week_low": fi.year_low,
            }
        except Exception:
            return None

    async def get_ohlcv(self, symbol: str, market: str, period: str, interval: str) -> dict:
        t = yf.Ticker(self._sym(symbol, market))
        df = t.history(period=period, interval=interval)
        df.reset_index(inplace=True)
        return {
            "symbol": symbol,
            "market": market,
            "data": [
                {
                    "date": str(row["Date"])[:10],
                    "open": round(row["Open"], 2),
                    "high": round(row["High"], 2),
                    "low": round(row["Low"], 2),
                    "close": round(row["Close"], 2),
                    "volume": int(row["Volume"]),
                }
                for _, row in df.iterrows()
            ],
        }

    async def get_fundamentals(self, symbol: str, market: str) -> dict:
        info = yf.Ticker(self._sym(symbol, market)).info
        return {
            "symbol": symbol,
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "description": info.get("longBusinessSummary", "")[:500],
        }

    async def search(self, query: str, market: str) -> list:
        from services.stock_universe import search_universe
        return search_universe(query, market, limit=8)
