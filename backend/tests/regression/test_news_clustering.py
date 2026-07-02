"""
Wave 0D3 — deterministic duplicate-story clustering for News Sentiment.

Root cause: URL-only dedup cannot catch the same real-world event arriving
through Yahoo AND Google News, several publishers, or syndicated reposts
with different URLs — one bullish event could become three bullish votes,
inflating score, composite, confidence, Daily Picks rank, badges, summaries,
and reasoning.

All tests are deterministic fixtures with a fixed injected `now`. No RSS,
no providers, no network, no production calls.
"""

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.news_sentiment import (
    CLUSTER_TIME_WINDOW,
    cluster_company_news_articles,
    title_cluster_tokens,
    _strip_outlet_suffix,
)
from services.prediction_engine import PredictionEngine

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
ENGINE = PredictionEngine()


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _article(title: str, label: str = "BULLISH", age: timedelta = timedelta(hours=2),
             url: str | None = None, conf: float = 0.7) -> dict:
    return {"title": title, "source": "test",
            "url": url or f"http://x/{abs(hash(title + str(age)))}",
            "published_at": _rfc2822(NOW - age), "description": "",
            "sentiment": {"label": label, "score": conf}}


# Canonical same-event trio: identical story via three outlets, different
# URLs, slightly different suffixes/wording, all within 48h.
_EVENT_A = "TSMC beats Q2 estimates on record AI chip demand"
def _syndicated_trio(label="BULLISH"):
    return [
        _article(_EVENT_A + " - Reuters", label, timedelta(hours=1), url="http://a/1"),
        _article(_EVENT_A + " | Bloomberg", label, timedelta(hours=5), url="http://b/2"),
        _article(_EVENT_A, label, timedelta(hours=9), url="http://c/3"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Title normalization
# ─────────────────────────────────────────────────────────────────────────────

def test_normalization_is_punctuation_and_whitespace_insensitive():
    a = title_cluster_tokens("TSMC beats Q2 estimates, on record   AI chip demand!")
    b = title_cluster_tokens("tsmc beats q2 estimates on record ai chip demand")
    assert a == b


def test_outlet_suffixes_are_stripped_safely():
    assert _strip_outlet_suffix("TSMC beats Q2 estimates - Reuters") == "TSMC beats Q2 estimates"
    assert _strip_outlet_suffix("TSMC beats Q2 estimates | Bloomberg") == "TSMC beats Q2 estimates"
    assert _strip_outlet_suffix("TSMC beats Q2 estimates – CNBC") == "TSMC beats Q2 estimates"
    # A long segment after a dash is meaningful text, not an outlet — kept.
    long_tail = "TSMC beats estimates - what the record quarter means for chip investors"
    assert _strip_outlet_suffix(long_tail) == long_tail


def test_meaningful_terms_kept_and_generic_words_dropped():
    tokens = title_cluster_tokens("TSMC stock rallies as shares surge on record Q2 earnings beat")
    # Generic finance words never drive similarity…
    for generic in ("stock", "rallies", "shares", "surge", "earnings", "on", "as"):
        assert generic not in tokens
    # …while event-bearing terms survive.
    for meaningful in ("tsmc", "record", "q2", "beat"):
        assert meaningful in tokens


def test_original_title_never_mutated():
    art = _article(_EVENT_A + " - Reuters")
    before = art["title"]
    cluster_company_news_articles([art])
    assert art["title"] == before


# ─────────────────────────────────────────────────────────────────────────────
# Clustering
# ─────────────────────────────────────────────────────────────────────────────

def test_same_event_two_urls_cluster_into_one_event():
    arts = _syndicated_trio()[:2]
    result = cluster_company_news_articles(arts)
    assert result["unique_story_count"] == 1
    assert result["duplicate_article_count"] == 1


def test_three_syndicated_articles_cluster_into_one_event():
    result = cluster_company_news_articles(_syndicated_trio())
    assert result["unique_story_count"] == 1
    assert result["duplicate_article_count"] == 2
    assert result["bullish_votes"] == 1


def test_distinct_events_sharing_generic_words_do_not_cluster():
    arts = [
        _article("TSMC beats Q2 estimates on record AI chip demand"),
        _article("TSMC announces Arizona fab expansion investment plan"),
        # Shares only ticker + generic finance words with the others:
        _article("TSMC stock rallies as market gains on earnings optimism"),
    ]
    result = cluster_company_news_articles(arts)
    assert result["unique_story_count"] == 3
    assert result["duplicate_article_count"] == 0


def test_similar_titles_more_than_48h_apart_do_not_cluster():
    arts = [
        _article(_EVENT_A, age=timedelta(hours=1), url="http://a/1"),
        _article(_EVENT_A, age=timedelta(hours=1) + CLUSTER_TIME_WINDOW + timedelta(hours=1), url="http://b/2"),
    ]
    result = cluster_company_news_articles(arts)
    assert result["unique_story_count"] == 2


def test_invalid_publication_timestamp_never_clusters():
    valid = _article(_EVENT_A, url="http://a/1")
    undated = dict(_article(_EVENT_A, url="http://b/2"), published_at="")
    result = cluster_company_news_articles([valid, undated])
    assert result["unique_story_count"] == 2  # undated stays a singleton


def test_clustering_is_deterministic_under_input_order():
    arts = _syndicated_trio() + [
        _article("TSMC announces Arizona fab expansion investment plan", "BULLISH", timedelta(hours=3)),
        _article("TSMC faces antitrust probe over pricing practices", "BEARISH", timedelta(hours=4)),
    ]
    baseline = cluster_company_news_articles(list(arts))
    rng = random.Random(42)
    for _ in range(5):
        shuffled = list(arts)
        rng.shuffle(shuffled)
        result = cluster_company_news_articles(shuffled)
        assert result["unique_story_count"] == baseline["unique_story_count"] == 3
        assert result["bullish_votes"] == baseline["bullish_votes"]
        assert result["bearish_votes"] == baseline["bearish_votes"]
        assert result["duplicate_article_count"] == baseline["duplicate_article_count"]


def test_no_transitive_over_clustering():
    # A~B and B~C directly, but C does not meet the threshold against the
    # cluster seed A — seed-anchored clustering must keep C separate.
    a = _article("TSMC wins major Apple chip order for iPhone generation", age=timedelta(hours=1), url="http://a")
    b = _article("TSMC wins major Apple chip order for processors generation", age=timedelta(hours=2), url="http://b")
    c = _article("TSMC wins major Apple chip supply order deal processors", age=timedelta(hours=3), url="http://c")
    ta, tb, tc = (title_cluster_tokens(x["title"]) for x in (a, b, c))
    # Fixture sanity: B is similar to both A and C; A and C fall below 0.70.
    assert len(ta & tb) / len(ta | tb) >= 0.70
    assert len(tb & tc) / len(tb | tc) >= 0.70
    assert len(ta & tc) / len(ta | tc) < 0.70
    result = cluster_company_news_articles([a, b, c])
    assert result["unique_story_count"] == 2  # {A,B} and {C} — no chain merge


def test_representative_selection_is_deterministic():
    trio = _syndicated_trio()
    trio[1]["sentiment"]["score"] = 0.95  # highest confidence wins
    result = cluster_company_news_articles(trio)
    assert result["clusters"][0]["representative"]["url"] == "http://b/2"


# ─────────────────────────────────────────────────────────────────────────────
# Cluster sentiment outcomes
# ─────────────────────────────────────────────────────────────────────────────

def test_conflicting_duplicate_cluster_gives_no_directional_vote():
    arts = _syndicated_trio()
    arts[1]["sentiment"] = {"label": "BEARISH", "score": 0.8}
    result = cluster_company_news_articles(arts)
    assert result["unique_story_count"] == 1
    assert result["bullish_votes"] == 0
    assert result["bearish_votes"] == 0
    assert result["conflicted_cluster_count"] == 1
    assert result["clusters"][0]["outcome"] == "conflicted"


def test_directional_plus_neutral_cluster_gives_single_consensus_vote():
    arts = _syndicated_trio()
    arts[2]["sentiment"] = {"label": "NEUTRAL", "score": 0.1}
    result = cluster_company_news_articles(arts)
    assert result["bullish_votes"] == 1
    assert result["conflicted_cluster_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate decision integrity (through the real _aggregate_sentiment)
# ─────────────────────────────────────────────────────────────────────────────

def _agg(arts, horizon="short"):
    return ENGINE._aggregate_sentiment(arts, horizon=horizon, now=NOW, symbol="TSM", market="US")


def test_one_bullish_event_three_articles_counts_once():
    result = _agg(_syndicated_trio())
    assert result["bullish"] == 1          # NOT 3
    assert result["bearish"] == 0
    assert result["score"] == 100
    assert result["company_news_event_count"] == 1
    assert result["duplicate_company_news_article_count"] == 2
    assert result["company_specific_article_count"] == 3


def test_one_bearish_event_three_articles_counts_once():
    result = _agg([
        _article("TSMC misses Q2 estimates as chip demand weakens - Reuters", "BEARISH", timedelta(hours=1), "http://a"),
        _article("TSMC misses Q2 estimates as chip demand weakens | Bloomberg", "BEARISH", timedelta(hours=3), "http://b"),
        _article("TSMC misses Q2 estimates as chip demand weakens", "BEARISH", timedelta(hours=6), "http://c"),
    ])
    assert result["bearish"] == 1 and result["bullish"] == 0
    assert result["score"] == 0


def test_conflicting_only_duplicate_coverage_is_unavailable_evidence():
    arts = _syndicated_trio()
    arts[1]["sentiment"] = {"label": "BEARISH", "score": 0.8}
    result = _agg(arts)
    assert result["data_available"] is False
    assert result["label"] == "NEUTRAL"
    assert result["bullish"] == 0 and result["bearish"] == 0
    assert result["freshness_status"] == "no_directional_company_news"
    assert result["reason_unavailable"] is not None


def test_conflicting_only_coverage_produces_no_badge_summary_or_ranking_evidence():
    import services.daily_picks as dp

    arts = _syndicated_trio()
    arts[1]["sentiment"] = {"label": "BEARISH", "score": 0.8}
    conflicted = _agg(arts, horizon="long")

    class _FakeEngine:
        async def predict(self, symbol, market, horizon):
            return {
                "company_name": "TEST", "signal": "BUY", "current_price": 100.0,
                "target_price": 110.0, "confidence": 70, "trade_levels": {},
                "technical": {"score": 60}, "fundamental_score": {"score": 55},
                "sentiment_score": conflicted, "quality_factors": {"score": 58},
                "reasoning": [], "score_band": None, "global_context": {},
                "market_regime": {}, "composite_score": 62.0, "confidence_score": 70,
            }

    with patch("services.daily_picks.PredictionEngine", return_value=_FakeEngine()), \
         patch("services.daily_picks.time.sleep"):
        row = dp._predict_stock("TSM", "long", "US")

    assert row["sentiment"] == "NEUTRAL"       # no Bullish/Bearish News badge
    assert row["sentiment_score"] is None      # None → z = 0.0 in ranking
    assert "News sentiment is bullish" not in row["summary"]


def test_mixed_unique_and_duplicate_events_count_unique_events_only():
    arts = _syndicated_trio() + [  # one bullish event ×3
        _article("TSMC announces Arizona fab expansion investment plan", "BULLISH", timedelta(hours=2)),
        _article("TSMC faces antitrust probe over pricing practices", "BEARISH", timedelta(hours=4)),
    ]
    result = _agg(arts)
    assert result["bullish"] == 2  # syndicated event once + expansion once
    assert result["bearish"] == 1
    assert result["company_news_event_count"] == 3
    # score = 50 + (2-1)/3*50 = 66 (int truncation of 66.67)
    assert result["score"] == 66


def test_fully_distinct_qualified_set_preserves_released_score_behaviour():
    arts = [
        _article("TSMC beats Q2 estimates on record AI chip demand", "BULLISH", timedelta(hours=1)),
        _article("TSMC announces Arizona fab expansion investment plan", "BULLISH", timedelta(hours=2)),
        _article("TSMC wins major automotive chip supply contract", "BULLISH", timedelta(hours=3)),
        _article("TSMC faces antitrust probe over pricing practices", "BEARISH", timedelta(hours=4)),
    ]
    result = _agg(arts)
    assert result["bullish"] == 3 and result["bearish"] == 1
    assert result["score"] == 75  # identical to the pre-0D3 formula output
    assert result["duplicate_company_news_article_count"] == 0


def test_contextual_and_historical_articles_never_enter_clustering():
    arts = [
        _article("AI chip stocks rally to record highs", "BULLISH", timedelta(hours=1)),   # contextual
        _article("TSMC beats Q2 estimates on record AI chip demand", "BULLISH", timedelta(days=300)),  # historical
    ]
    result = _agg(arts, horizon="medium")
    assert result["data_available"] is False   # nothing qualified → unchanged behaviour
    assert result.get("company_news_event_count") in (None, 0)


def test_freshness_and_relevance_rules_unchanged():
    # Stale direct article: still no_fresh_news (freshness precedes everything).
    stale = [_article("TSMC beats Q2 estimates on record AI chip demand", "BULLISH", timedelta(days=300))]
    r1 = ENGINE._aggregate_sentiment(stale, horizon="long", now=NOW, symbol="TSM", market="US")
    assert r1["freshness_status"] == "no_fresh_news"
    # Contextual-only fresh set: still no_company_specific_news.
    ctx = [_article("Semiconductor stocks surge as Nasdaq hits record", "BULLISH", timedelta(hours=1))]
    r2 = ENGINE._aggregate_sentiment(ctx, horizon="short", now=NOW, symbol="TSM", market="US")
    assert r2["freshness_status"] == "no_company_specific_news"


def test_route_response_exposes_event_metadata():
    import asyncio
    from services.news_sentiment import NewsSentimentService

    trio = _syndicated_trio()
    fixture = [dict(a) for a in trio] + [
        {"title": "Semiconductor stocks surge on broad rally", "source": "t",
         "url": "http://d/4", "published_at": _rfc2822(datetime.now(timezone.utc) - timedelta(hours=1)),
         "description": ""},
    ]
    # Route uses live 'now'; re-stamp the trio to be genuinely fresh.
    for i, a in enumerate(fixture[:3]):
        a["published_at"] = _rfc2822(datetime.now(timezone.utc) - timedelta(hours=i + 1))

    svc = NewsSentimentService()
    with patch.object(NewsSentimentService, "_fetch_rss", return_value=fixture):
        result = asyncio.run(svc.get_news_with_sentiment("W0D3TEST", "US", 10))

    # The trio titles carry no known ticker for symbol W0D3TEST — relevance
    # depends on the target; use TSM instead for a real assertion:
    with patch.object(NewsSentimentService, "_fetch_rss", return_value=[dict(a) for a in fixture]):
        result = asyncio.run(svc.get_news_with_sentiment("TSM", "US", 10))
    assert result["company_specific_article_count"] == 3
    assert result["current_company_news_event_count"] == 1
    assert result["duplicate_company_news_article_count"] == 2
    assert len(result["articles"]) == 4  # display articles never removed
