"""
UI/UX Truthfulness Correction Program — Wave 0C: fresh-news eligibility,
stale-news exclusion, and truthful sentiment reasoning.

Root cause: articles' published_at was captured as a raw string and never
parsed — months-old articles carried full weight in the sentiment score,
composite score, confidence factor agreement, Daily Picks ranking factor,
the "Bullish News" badge, the generated summary, and reasoning text that
falsely said "in recent news."

All tests are deterministic: fixture articles with controlled ages against
a fixed injected `now`. No RSS/provider/network calls anywhere.
"""

from datetime import datetime, timedelta, timezone

from services.news_sentiment import (
    SENTIMENT_MAX_AGE_DAYS,
    is_article_eligible,
    parse_published_at,
    split_articles_by_freshness,
)
from services.prediction_engine import PredictionEngine

# Fixed deterministic "now" for every test in this file.
NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


def _rfc2822(dt: datetime) -> str:
    """Format like real RSS feeds do (RFC-2822)."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _article(age: timedelta | None, label: str = "BULLISH", title: str = "t") -> dict:
    published = "" if age is None else _rfc2822(NOW - age)
    return {"title": title, "source": "test", "url": f"http://x/{title}",
            "published_at": published, "sentiment": {"label": label, "score": 0.7}}


ENGINE = PredictionEngine()


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_published_at_handles_rfc2822_and_iso():
    assert parse_published_at("Wed, 01 Jul 2026 09:30:00 +0000") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    assert parse_published_at("2026-07-01T09:30:00+00:00") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    # Naive ISO assumed UTC
    assert parse_published_at("2026-07-01T09:30:00") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)


def test_parse_published_at_never_raises_and_never_invents_dates():
    for bad in ["", None, "not a date", "0", "yesterday", "32 Foo 2026", 12345]:
        assert parse_published_at(bad) is None, f"input={bad!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 1-4. Horizon-specific eligibility policy
# ─────────────────────────────────────────────────────────────────────────────

def test_same_day_article_eligible_for_all_horizons():
    raw = _rfc2822(NOW - timedelta(hours=3))
    for horizon in ("short", "medium", "long", None):
        assert is_article_eligible(raw, horizon, NOW) is True, f"horizon={horizon}"


def test_four_day_article_ineligible_short_eligible_medium_long_general():
    raw = _rfc2822(NOW - timedelta(days=4))
    assert is_article_eligible(raw, "short", NOW) is False
    assert is_article_eligible(raw, "medium", NOW) is True
    assert is_article_eligible(raw, "long", NOW) is True
    assert is_article_eligible(raw, None, NOW) is True  # general = 14d


def test_fifteen_day_article_eligible_only_for_long():
    raw = _rfc2822(NOW - timedelta(days=15))
    assert is_article_eligible(raw, "short", NOW) is False
    assert is_article_eligible(raw, "medium", NOW) is False
    assert is_article_eligible(raw, None, NOW) is False
    assert is_article_eligible(raw, "long", NOW) is True  # long = 30d


def test_thirty_one_day_article_ineligible_everywhere():
    raw = _rfc2822(NOW - timedelta(days=31))
    for horizon in ("short", "medium", "long", None):
        assert is_article_eligible(raw, horizon, NOW) is False, f"horizon={horizon}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Eleven-month-old bullish article contributes nothing anywhere
# ─────────────────────────────────────────────────────────────────────────────

def test_eleven_month_old_bullish_article_has_zero_decision_weight():
    stale = [_article(timedelta(days=335), "BULLISH")]
    result = ENGINE._aggregate_sentiment(stale, horizon="medium", now=NOW)
    assert result["data_available"] is False       # → excluded from composite,
    assert result["label"] == "NEUTRAL"            #   confidence factor agreement,
    assert result["score"] == 50                   #   and never a badge source
    assert result["bullish"] == 0                  # not counted as bullish evidence
    assert result["freshness_status"] == "no_fresh_news"
    assert result["historical_article_count"] == 1
    assert result["eligible_article_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Mixed set — only eligible articles contribute
# ─────────────────────────────────────────────────────────────────────────────

def test_mixed_set_counts_only_eligible_articles():
    articles = [
        _article(timedelta(hours=5), "BULLISH", "fresh1"),
        _article(timedelta(days=1), "BEARISH", "fresh2"),
        _article(timedelta(days=200), "BULLISH", "stale1"),   # must not count
        _article(timedelta(days=300), "BULLISH", "stale2"),   # must not count
        _article(None, "BULLISH", "undated"),                 # must not count
    ]
    result = ENGINE._aggregate_sentiment(articles, horizon="medium", now=NOW)
    assert result["data_available"] is True
    assert result["bullish"] == 1
    assert result["bearish"] == 1
    assert result["score"] == 50  # 1v1 among eligible — NOT 4v1 among all
    assert result["total_article_count"] == 5
    assert result["eligible_article_count"] == 2
    assert result["historical_article_count"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# 7. All articles stale → unavailable, no fabricated conclusion
# ─────────────────────────────────────────────────────────────────────────────

def test_all_stale_articles_report_unavailable_not_bullish():
    stale = [_article(timedelta(days=150 + i * 30), "BULLISH", f"s{i}") for i in range(9)]
    result = ENGINE._aggregate_sentiment(stale, horizon="short", now=NOW)
    # The exact SBI scenario: 9 old bullish articles previously scored 100.
    assert result["data_available"] is False
    assert result["label"] == "NEUTRAL"
    assert result["bullish"] == 0 and result["bearish"] == 0
    assert result["reason_unavailable"] is not None


def test_unavailable_sentiment_triggers_existing_redistribution_contract():
    """data_available=False is the exact flag the composite's no-news
    redistribution path and the confidence engine's factor-agreement
    exclusion already key on — locking in that stale-only input produces it."""
    stale = [_article(timedelta(days=100), "BULLISH")]
    result = ENGINE._aggregate_sentiment(stale, horizon="long", now=NOW)
    assert result["data_available"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 8-9. Malformed and future dates
# ─────────────────────────────────────────────────────────────────────────────

def test_malformed_and_missing_dates_are_ineligible_without_exceptions():
    articles = [
        {"title": "a", "url": "u1", "published_at": "", "sentiment": {"label": "BULLISH"}},
        {"title": "b", "url": "u2", "published_at": "garbage text", "sentiment": {"label": "BULLISH"}},
        {"title": "c", "url": "u3", "sentiment": {"label": "BULLISH"}},  # key absent
    ]
    result = ENGINE._aggregate_sentiment(articles, horizon="medium", now=NOW)
    assert result["data_available"] is False
    assert result["eligible_article_count"] == 0


def test_future_dated_articles_beyond_tolerance_are_ineligible():
    slightly_future = _rfc2822(NOW + timedelta(hours=2))   # clock skew — tolerated
    far_future = _rfc2822(NOW + timedelta(days=3))         # nonsense — ineligible
    assert is_article_eligible(slightly_future, "medium", NOW) is True
    assert is_article_eligible(far_future, "medium", NOW) is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. Identical eligible sets score identically to the old formula
# ─────────────────────────────────────────────────────────────────────────────

def test_scoring_unchanged_for_fully_fresh_article_set():
    fresh = [
        _article(timedelta(hours=1), "BULLISH"),
        _article(timedelta(hours=2), "BULLISH"),
        _article(timedelta(hours=3), "BULLISH"),
        _article(timedelta(hours=4), "BEARISH"),
    ]
    result = ENGINE._aggregate_sentiment(fresh, horizon="short", now=NOW)
    # Old formula: 50 + (3-1)/4*50 = 75 — must be byte-identical.
    assert result["score"] == 75
    assert result["label"] == "BULLISH"
    assert result["data_available"] is True
    assert result["freshness_status"] == "fresh"


def test_empty_article_list_still_reports_unavailable():
    result = ENGINE._aggregate_sentiment([], horizon="medium", now=NOW)
    assert result["data_available"] is False
    assert result["freshness_status"] == "no_articles"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Daily Picks badge / generated summary cannot come from stale news
# ─────────────────────────────────────────────────────────────────────────────

def test_daily_picks_badge_and_summary_gate_on_unavailable_sentiment():
    """The Daily Picks card badge uses sentiment_score.label and the
    generated summary adds 'News sentiment is bullish.' when label==BULLISH
    or score>=60. Stale-only input now yields NEUTRAL/50/unavailable, so
    neither can fire — while fresh input still behaves normally."""
    from services.daily_picks import _build_summary

    stale_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=200), "BULLISH")], horizon="long", now=NOW)
    fresh_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=1), "BULLISH")], horizon="long", now=NOW)

    def summary_for(sent):
        return _build_summary({
            "company_name": "TEST", "symbol": "TEST", "confidence": 70,
            "current_price": 100.0, "target_price": 110.0,
            "technical": {"score": 65}, "fundamental_score": {"score": 60},
            "sentiment_score": sent, "market_regime": {}, "global_context": {},
            "quality_factors": {},
        }, "long")

    assert "News sentiment is bullish" not in summary_for(stale_sent)
    assert stale_sent["label"] == "NEUTRAL"   # → no 📰 badge on the card
    assert "News sentiment is bullish" in summary_for(fresh_sent)
    assert fresh_sent["label"] == "BULLISH"


# ─────────────────────────────────────────────────────────────────────────────
# 12. India and US parity
# ─────────────────────────────────────────────────────────────────────────────

def test_freshness_logic_is_market_agnostic():
    """Eligibility is a pure function of published_at + horizon + now — the
    helper takes no market/symbol/currency argument at all, so IN and US
    articles with identical timestamps behave identically by construction."""
    raw = _rfc2822(NOW - timedelta(days=10))
    import inspect
    params = set(inspect.signature(is_article_eligible).parameters)
    assert params == {"published_at_raw", "horizon", "now"}
    assert is_article_eligible(raw, "medium", NOW) is True


# ─────────────────────────────────────────────────────────────────────────────
# Reasoning text truthfulness (source-level lock)
# ─────────────────────────────────────────────────────────────────────────────

def test_reasoning_no_longer_claims_unverified_recent_news():
    import inspect
    import services.prediction_engine as pe
    src = inspect.getsource(pe)
    assert "in eligible recent news" in src
    assert "bearish in recent news" not in src, (
        "The reasoning text must not claim bare 'recent news' — recency is "
        "now verified, and the wording must say so."
    )


def test_policy_constants_are_centralized():
    assert SENTIMENT_MAX_AGE_DAYS == {"short": 3, "medium": 14, "long": 30, "general": 14}


# ═════════════════════════════════════════════════════════════════════════════
# Release 7 follow-up — unavailable sentiment is MISSING ranking evidence
# (None → z = 0.0), never an active numeric 50 in the Daily Picks cross-section.
# ═════════════════════════════════════════════════════════════════════════════

from unittest.mock import patch

_IC_EQ = {"tech": 0.25, "fund": 0.25, "sentiment": 0.25, "quality": 0.25}
_REGIME = {"label": "BULL_CALM"}


def _rank_row(symbol: str, sentiment_score, tech=50.0, fund=50.0, quality=50.0) -> dict:
    return {"symbol": symbol, "horizon": "medium", "tech_score": tech,
            "fund_score": fund, "quality_score": quality,
            "sentiment_score": sentiment_score}


def _rank(items):
    import services.daily_picks as dp
    with patch("services.alpha_engine.meta_model.predict", return_value=None):
        return dp._zscore_and_rank([dict(r) for r in items], _IC_EQ, _REGIME, 0, market="IN")


def _fixture_prediction(sent: dict) -> dict:
    return {
        "company_name": "TEST", "signal": "BUY", "current_price": 100.0,
        "target_price": 110.0, "confidence": 70, "trade_levels": {},
        "technical": {"score": 60}, "fundamental_score": {"score": 55},
        "sentiment_score": sent, "quality_factors": {"score": 58},
        "reasoning": [], "score_band": None, "global_context": {},
        "market_regime": {}, "composite_score": 62.0, "confidence_score": 70,
    }


def _run_predict_stock(sent: dict) -> dict:
    """Drive the real _predict_stock with a mocked engine and no sleeping."""
    import services.daily_picks as dp

    class _FakeEngine:
        async def predict(self, symbol, market, horizon):
            return _fixture_prediction(sent)

    with patch("services.daily_picks.PredictionEngine", return_value=_FakeEngine()), \
         patch("services.daily_picks.time.sleep"):
        row = dp._predict_stock("TEST", "medium", "IN")
    assert row is not None
    return row


# Test 1 — unavailable sentiment becomes None before ranking
def test_unavailable_sentiment_becomes_none_in_ranking_row():
    row = _run_predict_stock({"score": 50, "label": "NEUTRAL", "bullish": 0,
                              "bearish": 0, "data_available": False})
    assert row["sentiment_score"] is None      # NOT 50
    assert row["sentiment"] == "NEUTRAL"       # badge label preserved (no badge)

    fresh = _run_predict_stock({"score": 75, "label": "BULLISH", "bullish": 3,
                                "bearish": 1, "data_available": True})
    assert fresh["sentiment_score"] == 75.0    # fresh stays numeric


def test_non_finite_sentiment_scores_also_become_none():
    # Realistic malformed numerics: NaN/±Infinity. (None or non-numeric types
    # are unreachable from the sentiment service — it always emits a numeric
    # score — and would trip a pre-existing comparison in _build_summary
    # before reaching ranking; deliberately not exercised to keep this
    # release's scope narrow. The extraction guard covers them regardless.)
    for bad in [float("nan"), float("inf"), float("-inf")]:
        row = _run_predict_stock({"score": bad, "label": "NEUTRAL", "data_available": True})
        assert row["sentiment_score"] is None, f"score={bad!r}"


# Test 2 — unavailable sentiment z-score is exactly 0.0
def test_unavailable_sentiment_z_is_zero_and_contributes_nothing():
    rows = _rank([
        _rank_row("FRESH_HI", 80.0),
        _rank_row("FRESH_LO", 40.0),
        _rank_row("STALE", None),
    ])
    stale = next(r for r in rows if r["symbol"] == "STALE")
    assert stale["factor_zscores"]["sentiment"] == 0.0
    # All other factors identical (all 50) → its alpha must be exactly the
    # non-sentiment part, i.e. sentiment contributed 0.25 * 0.0 = 0.0.
    hi = next(r for r in rows if r["symbol"] == "FRESH_HI")
    sent_part_hi = 0.25 * hi["factor_zscores"]["sentiment"]
    assert abs((hi["combined_alpha"] - sent_part_hi) - stale["combined_alpha"]) < 1e-6


# Test 3 — unavailable row does not distort fresh rows' statistics
def test_unavailable_row_excluded_from_sentiment_statistics():
    with_stale = _rank([
        _rank_row("A", 80.0), _rank_row("B", 40.0), _rank_row("C", None),
    ])
    without_stale = _rank([
        _rank_row("A", 80.0), _rank_row("B", 40.0),
    ])
    for sym in ("A", "B"):
        z_with = next(r for r in with_stale if r["symbol"] == sym)["factor_zscores"]["sentiment"]
        z_without = next(r for r in without_stale if r["symbol"] == sym)["factor_zscores"]["sentiment"]
        assert z_with == z_without, f"{sym}: {z_with} vs {z_without} — stale row distorted the cross-section"
    # mean=60, std=20 → A: +1.0, B: -1.0 (locks the formula itself)
    assert next(r for r in with_stale if r["symbol"] == "A")["factor_zscores"]["sentiment"] == 1.0
    assert next(r for r in with_stale if r["symbol"] == "B")["factor_zscores"]["sentiment"] == -1.0


# Test 4 — legacy compatibility score cannot affect rank
def test_legacy_fallback_scores_produce_identical_zero_contribution():
    row_50 = _run_predict_stock({"score": 50, "label": "NEUTRAL", "data_available": False})
    row_95 = _run_predict_stock({"score": 95, "label": "NEUTRAL", "data_available": False})
    assert row_50["sentiment_score"] is None and row_95["sentiment_score"] is None

    ranked = _rank([
        {**_rank_row("X50", None)},
        {**_rank_row("X95", None)},
        _rank_row("FRESH", 70.0),
    ])
    z50 = next(r for r in ranked if r["symbol"] == "X50")
    z95 = next(r for r in ranked if r["symbol"] == "X95")
    assert z50["factor_zscores"]["sentiment"] == 0.0
    assert z95["factor_zscores"]["sentiment"] == 0.0
    assert z50["combined_alpha"] == z95["combined_alpha"]


# Test 5 — fresh eligible sentiment remains fully active
def test_fresh_sentiment_remains_active_ranking_evidence():
    rows = _rank([
        _rank_row("HI", 80.0), _rank_row("LO", 40.0),
    ])
    hi = next(r for r in rows if r["symbol"] == "HI")
    lo = next(r for r in rows if r["symbol"] == "LO")
    assert hi["factor_zscores"]["sentiment"] == 1.0    # (80-60)/20 — existing formula
    assert lo["factor_zscores"]["sentiment"] == -1.0
    assert hi["combined_alpha"] > lo["combined_alpha"]  # sentiment still differentiates rank


# Test 6 — all sentiment unavailable is safe
def test_all_unavailable_sentiment_population_is_safe():
    import math
    rows = _rank([
        _rank_row("A", None), _rank_row("B", None), _rank_row("C", None),
    ])
    for r in rows:
        z = r["factor_zscores"]["sentiment"]
        assert z == 0.0
        assert math.isfinite(r["combined_alpha"])
    alphas = {r["combined_alpha"] for r in rows}
    assert len(alphas) == 1  # sentiment creates no ranking differentiation


# Test 7 — India and US parity
def test_unavailable_sentiment_treatment_is_market_agnostic():
    import services.daily_picks as dp
    items = [_rank_row("A", 80.0), _rank_row("B", None)]
    with patch("services.alpha_engine.meta_model.predict", return_value=None):
        rows_in = dp._zscore_and_rank([dict(r) for r in items], _IC_EQ, _REGIME, 0, market="IN")
        rows_us = dp._zscore_and_rank([dict(r) for r in items], _IC_EQ, _REGIME, 0, market="US")
    for sym in ("A", "B"):
        z_in = next(r for r in rows_in if r["symbol"] == sym)["factor_zscores"]["sentiment"]
        z_us = next(r for r in rows_us if r["symbol"] == sym)["factor_zscores"]["sentiment"]
        assert z_in == z_us


# ═════════════════════════════════════════════════════════════════════════════
# Wave 0C display truthfulness — presentation-only freshness annotation
# ═════════════════════════════════════════════════════════════════════════════

def test_classify_article_freshness_reasons():
    from services.news_sentiment import classify_article_freshness
    assert classify_article_freshness(_rfc2822(NOW - timedelta(hours=2)), "general", NOW) == (True, "fresh")
    assert classify_article_freshness(_rfc2822(NOW - timedelta(days=335)), "general", NOW) == (False, "stale")
    assert classify_article_freshness("", "general", NOW) == (False, "invalid_date")
    assert classify_article_freshness("garbage", "general", NOW) == (False, "invalid_date")
    assert classify_article_freshness(_rfc2822(NOW + timedelta(days=3)), "general", NOW) == (False, "future_date")
    # Consistency: the boolean must match is_article_eligible exactly.
    for raw in [_rfc2822(NOW - timedelta(days=1)), _rfc2822(NOW - timedelta(days=400)), "", None]:
        assert classify_article_freshness(raw, "medium", NOW)[0] == is_article_eligible(raw, "medium", NOW)


def test_news_response_carries_presentation_eligibility_metadata():
    import asyncio
    from services.news_sentiment import NewsSentimentService

    fixture = [
        {"title": "fresh", "source": "t", "url": "u1",
         "published_at": _rfc2822(datetime.now(timezone.utc) - timedelta(hours=1)), "description": ""},
        {"title": "old", "source": "t", "url": "u2",
         "published_at": _rfc2822(datetime.now(timezone.utc) - timedelta(days=300)), "description": ""},
        {"title": "undated", "source": "t", "url": "u3", "published_at": "", "description": ""},
    ]
    svc = NewsSentimentService()
    with patch.object(NewsSentimentService, "_fetch_rss", return_value=[dict(a) for a in fixture]):
        # Unique symbol so the module-level 10-minute cache cannot interfere.
        result = asyncio.run(svc.get_news_with_sentiment("W0CTEST", "IN", 10))

    arts = {a["title"]: a for a in result["articles"]}
    assert arts["fresh"]["sentiment_eligible"] is True
    assert arts["fresh"]["eligibility_reason"] == "fresh"
    assert arts["old"]["sentiment_eligible"] is False
    assert arts["old"]["eligibility_reason"] == "stale"
    assert arts["undated"]["sentiment_eligible"] is False
    assert arts["undated"]["eligibility_reason"] == "invalid_date"
    assert result["total_article_count"] == 3
    assert result["eligible_article_count"] == 1
    assert result["historical_article_count"] == 2


def test_presentation_metadata_does_not_change_decision_outputs():
    """The annotation is display-only: _aggregate_sentiment must return the
    exact same decision output whether or not articles carry the new fields."""
    plain = [_article(timedelta(hours=1), "BULLISH"), _article(timedelta(days=300), "BULLISH")]
    annotated = [dict(a, sentiment_eligible=True, eligibility_reason="fresh") for a in plain]
    r_plain = ENGINE._aggregate_sentiment(plain, horizon="medium", now=NOW)
    r_annot = ENGINE._aggregate_sentiment(annotated, horizon="medium", now=NOW)
    for key in ("score", "label", "bullish", "bearish", "data_available", "freshness_status"):
        assert r_plain[key] == r_annot[key], key


# Test 8 — Wave 0C protections regression (badge/summary path re-locked with
# the ranking-row conversion now in place)
def test_wave0c_badge_and_summary_protections_still_hold():
    stale_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=335), "BULLISH")], horizon="long", now=NOW)
    assert stale_sent["data_available"] is False
    row = _run_predict_stock(stale_sent)
    assert row["sentiment"] == "NEUTRAL"          # no 📰 Bullish News badge
    assert row["sentiment_score"] is None         # no ranking evidence
    assert "News sentiment is bullish" not in row["summary"]

    fresh_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=1), "BULLISH")], horizon="long", now=NOW)
    fresh_row = _run_predict_stock(fresh_sent)
    assert fresh_row["sentiment"] == "BULLISH"    # fresh news still behaves normally
    assert fresh_row["sentiment_score"] == 100.0
