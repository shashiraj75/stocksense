import time
import feedparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_vader = SentimentIntensityAnalyzer()

# News cache: { "SYMBOL:MARKET" -> (timestamp, result) }
_news_cache: dict[str, tuple[float, dict]] = {}
_NEWS_TTL = 10 * 60  # 10 minutes

# Free public RSS feeds — no API key needed
RSS_FEEDS = {
    "US": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
        "https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en",
    ],
    "IN": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}.NS&region=IN&lang=en-IN",
        "https://news.google.com/rss/search?q={symbol}+NSE+India+stock&hl=en-IN&gl=IN&ceid=IN:en",
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/{symbol}.cms",
        # Moneycontrol — India's largest financial news portal
        "https://www.moneycontrol.com/rss/buzzingstocks.xml",
    ],
}

MACRO_FEEDS = {
    "US": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
        "https://news.google.com/rss/search?q=US+Federal+Reserve+interest+rates+economy&hl=en-US&gl=US&ceid=US:en",
    ],
    "IN": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ENSEI&region=IN&lang=en-IN",
        "https://news.google.com/rss/search?q=RBI+India+Nifty+Sensex+economy&hl=en-IN&gl=IN&ceid=IN:en",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        # Moneycontrol macro feeds
        "https://www.moneycontrol.com/rss/marketreports.xml",
        "https://www.moneycontrol.com/rss/economy.xml",
    ],
}


def score_sentiment(text: str) -> dict:
    scores = _vader.polarity_scores(text)
    compound = scores["compound"]
    if compound >= 0.05:
        label = "BULLISH"
    elif compound <= -0.05:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    return {"label": label, "score": round(abs(compound), 3)}


class NewsSentimentService:
    async def get_news_with_sentiment(self, symbol: str, market: str, limit: int = 20) -> dict:
        cache_key = f"{symbol}:{market}"
        cached = _news_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _NEWS_TTL:
            return cached[1]

        articles = self._fetch_rss(symbol, market, limit)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"])
        result = {"symbol": symbol, "market": market, "articles": articles}
        _news_cache[cache_key] = (time.time(), result)
        return result

    async def get_macro_news(self, market: str) -> dict:
        articles = self._fetch_macro_rss(market, 15)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"])
        return {"market": market, "articles": articles}

    def _fetch_rss(self, symbol: str, market: str, limit: int) -> list:
        urls = [t.format(symbol=symbol) for t in RSS_FEEDS.get(market, RSS_FEEDS["US"])]
        sym_lower = symbol.lower()

        def _parse(url: str) -> list:
            try:
                feed = feedparser.parse(url)
                articles = []
                for entry in feed.entries[:limit * 2]:
                    title = entry.get("title", "")
                    # For broad feeds (moneycontrol buzzingstocks), only include articles
                    # that mention the stock symbol or company name in the title/summary
                    is_broad_feed = "moneycontrol.com/rss/buzzing" in url
                    if is_broad_feed:
                        combined = (title + " " + entry.get("summary", "")).lower()
                        if sym_lower not in combined:
                            continue
                    articles.append({
                        "title": title,
                        "source": feed.feed.get("title", "RSS"),
                        "url": entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description": entry.get("summary", "")[:200],
                    })
                return articles[:limit]
            except Exception:
                return []

        articles: list = []
        with ThreadPoolExecutor(max_workers=len(urls)) as pool:
            for batch in pool.map(_parse, urls):
                articles.extend(batch)
                if len(articles) >= limit:
                    break
        return articles[:limit]

    def _fetch_macro_rss(self, market: str, limit: int) -> list:
        articles = []
        for url in MACRO_FEEDS.get(market, []):
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:limit]:
                    articles.append({
                        "title": entry.get("title", ""),
                        "source": feed.feed.get("title", "RSS"),
                        "url": entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description": entry.get("summary", "")[:200],
                    })
            except Exception:
                pass
        return articles[:limit]
