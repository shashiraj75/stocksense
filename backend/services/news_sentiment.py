import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

log = logging.getLogger(__name__)

_vader = SentimentIntensityAnalyzer()

# Financial domain lexicon — terms VADER misses or scores wrong.
# Compound override: if any phrase is found in text, it anchors the score.
# Values are in VADER's compound range [-1, +1].
_FIN_LEXICON: list[tuple[str, float]] = [
    # Strongly bullish
    ("strong buy",          0.90), ("buy rating",          0.80),
    ("record profit",       0.80), ("record revenue",      0.75),
    ("beat expectations",   0.75), ("beats estimates",     0.75),
    ("dividend hike",       0.65), ("dividend increase",   0.60),
    ("upgrade",             0.65), ("upgraded",            0.65),
    ("outperform",          0.60), ("overweight",          0.50),
    ("share buyback",       0.55), ("share repurchase",    0.50),
    ("all-time high",       0.70), ("52-week high",        0.60),
    ("breakout",            0.55), ("rally",               0.50),
    ("bullish",             0.65), ("beat",                0.50),
    ("exceeds",             0.45), ("surpassed",           0.50),
    ("robust earnings",     0.70), ("strong earnings",     0.65),
    ("margin expansion",    0.55), ("order inflow",        0.50),
    ("promoter buying",     0.55), ("fii buying",          0.55),
    # Strongly bearish
    ("strong sell",        -0.90), ("sell rating",        -0.80),
    ("profit warning",     -0.80), ("earnings miss",      -0.80),
    ("below expectations", -0.70), ("missed estimates",   -0.75),
    ("downgrade",          -0.65), ("downgraded",         -0.65),
    ("underperform",       -0.60), ("underweight",        -0.50),
    ("dividend cut",       -0.80), ("dividend suspension",-0.75),
    ("default",            -0.75), ("debt restructuring", -0.60),
    ("fraud",              -0.90), ("scam",               -0.90),
    ("investigation",      -0.55), ("regulatory action",  -0.55),
    ("collapse",           -0.80), ("crash",              -0.75),
    ("layoffs",            -0.45), ("restructuring",      -0.35),
    ("loss widened",       -0.70), ("net loss",           -0.55),
    ("bearish",            -0.65), ("margin pressure",    -0.55),
    ("promoter selling",   -0.55), ("fii selling",        -0.50),
    ("52-week low",        -0.60), ("all-time low",       -0.70),
]

# News cache: { "SYMBOL:MARKET" -> (timestamp, result) }
_news_cache: dict[str, tuple[float, dict]] = {}
_news_lock = threading.Lock()
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
        "https://www.moneycontrol.com/rss/marketreports.xml",
        "https://www.moneycontrol.com/rss/economy.xml",
    ],
}


def _fin_lexicon_score(text: str) -> float | None:
    """
    Check text against the financial lexicon phrases.
    Returns the compound score of the strongest match found, or None.
    Longer (more specific) phrases take priority over shorter ones.
    """
    t = text.lower()
    best: float | None = None
    best_len = 0
    for phrase, score in _FIN_LEXICON:
        if phrase in t and len(phrase) > best_len:
            best = score
            best_len = len(phrase)
    return best


def score_sentiment(title: str, description: str = "") -> dict:
    """
    Score sentiment using a two-layer approach:
    1. Financial lexicon override — catches domain terms VADER misses
    2. VADER — blended across title (70%) + description (30%)
    """
    # Layer 1: financial lexicon on combined text (title weighted more)
    combined = f"{title} {description}".strip()
    fin_score = _fin_lexicon_score(combined)

    # Layer 2: VADER scores
    vader_title = _vader.polarity_scores(title)["compound"]
    vader_desc  = _vader.polarity_scores(description)["compound"] if description else 0.0
    vader_blend = vader_title * 0.7 + vader_desc * 0.3

    # Blend: financial lexicon anchors if present, VADER modulates
    if fin_score is not None:
        # 60% lexicon, 40% VADER — lexicon is domain-authoritative
        compound = fin_score * 0.6 + vader_blend * 0.4
    else:
        compound = vader_blend

    compound = max(-1.0, min(1.0, compound))

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
        with _news_lock:
            cached = _news_cache.get(cache_key)
            if cached and (time.time() - cached[0]) < _NEWS_TTL:
                return cached[1]

        articles = self._fetch_rss(symbol, market, limit)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"], a.get("description", ""))
        result = {"symbol": symbol, "market": market, "articles": articles}

        with _news_lock:
            _news_cache[cache_key] = (time.time(), result)
        return result

    async def get_macro_news(self, market: str) -> dict:
        articles = self._fetch_macro_rss(market, 15)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"], a.get("description", ""))
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
                    is_broad_feed = "moneycontrol.com/rss/buzzing" in url
                    if is_broad_feed:
                        combined = (title + " " + entry.get("summary", "")).lower()
                        if sym_lower not in combined:
                            continue
                    articles.append({
                        "title":        title,
                        "source":       feed.feed.get("title", "RSS"),
                        "url":          entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description":  entry.get("summary", "")[:300],
                    })
                return articles[:limit]
            except Exception as e:
                log.warning("RSS feed failed [%s]: %s", url, e)
                return []

        seen_urls: set[str] = set()
        articles: list = []
        with ThreadPoolExecutor(max_workers=len(urls)) as pool:
            for batch in pool.map(_parse, urls):
                for art in batch:
                    if art["url"] not in seen_urls:
                        seen_urls.add(art["url"])
                        articles.append(art)
                    if len(articles) >= limit:
                        break
        return articles[:limit]

    def _fetch_macro_rss(self, market: str, limit: int) -> list:
        seen_urls: set[str] = set()
        articles = []
        for url in MACRO_FEEDS.get(market, []):
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:limit]:
                    link = entry.get("link", "#")
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)
                    articles.append({
                        "title":        entry.get("title", ""),
                        "source":       feed.feed.get("title", "RSS"),
                        "url":          link,
                        "published_at": entry.get("published", ""),
                        "description":  entry.get("summary", "")[:300],
                    })
            except Exception as e:
                log.warning("Macro RSS feed failed [%s]: %s", url, e)
        return articles[:limit]
