# Product Integrity Workstream #019 — News & Sentiment Pipeline Freshness Fixes

**Status:** Deployed to production (2026-07-16, commit `fa0afe3`) — live-verified against the real production news endpoint.

## 1. Trigger

User feedback: the Stock Detail page's "News & Sentiment" section for DIXON showed "Insufficient fresh company-specific news evidence" plus only 4 "historical context" articles dated 4-8 months old — despite the user confirming genuinely recent DIXON news exists online right now. User asked two things: why isn't fresh news showing, and does this affect the AI Prediction logic.

## 2. Investigation findings

**Question 2 first, since it scopes the risk of everything below: no, the AI signal is unaffected.** Traced `sentiment_score` through `_composite_signal` and `_confidence_engine` (`prediction_engine.py`) — when news evidence is unavailable, its weight (10-15% depending on horizon) is redistributed proportionally to technicals/fundamentals rather than defaulted to neutral, and it's excluded from the confidence-agreement calculation rather than counted as "disagreement." `_estimate_target` never reads sentiment directly. The code is deliberately engineered so missing news evidence degrades gracefully rather than silently corrupting BUY/SELL/HOLD, confidence, or target price. The impact of the bugs below is real but scoped to the News & Sentiment UI section and the "Sentiment X%" pill being less informative than it should be — not the recommendation itself.

**Question 1: four independent, confirmed bugs — not thin coverage for a mid-cap stock.** Each verified against the live production endpoints, not assumed:

- **Bug A:** The Google News RSS query (`news_sentiment.py`'s `RSS_FEEDS`) had no recency operator. Google News ranks an un-filtered query by relevance, not date. Fetched the exact query live for DIXON: only months-old results. Adding `when:14d` to the identical query returned same-day articles ("Dixon Tech shares rise up to 6% as Investec sees strong earnings growth...", dated today).
- **Bug B:** `classify_article_relevance`'s company-name match required the FULL run-on core-name phrase ("dixon technologies india" for "Dixon Technologies (India) Limited") — real headlines essentially never include the trailing country word, so ordinary coverage like "Dixon Technologies shares rally 5%" was classified `unknown` and excluded. `_target_identifiers` already computed a 2-word prefix ("dixon technologies") for peer-exclusion purposes but it was never also accepted as a target match.
- **Bug C:** The Economic Times per-symbol RSS URL is dead — verified live it returns ET's generic homepage HTML, not RSS. ET's real per-stock feeds use internal numeric IDs this codebase has no mapping for.
- **Bug D:** The Yahoo Finance RSS feed is deprecated — verified live (both US and IN query shapes) it returns Yahoo's generic HTML homepage, not RSS.

Bugs C and D were silently contributing zero articles for **every symbol**, not just DIXON.

## 3. Fixes

- **Bug A:** Google News queries (both `RSS_FEEDS` and `MACRO_FEEDS`, both markets) now include `when:14d`, matching the existing `SENTIMENT_MAX_AGE_DAYS["general"]` freshness window rather than an arbitrary new number.
- **Bug B:** `_target_mentioned` now also accepts the 2-word `own_prefixes` match (previously computed and discarded) as a valid target identifier, in addition to the full run-on phrase. **Deliberately did not touch ticker case-sensitivity** — an existing test (`test_word_boundaries_prevent_false_positive_matches`, "the tsm format is a file extension" must never match ticker TSM) confirms that design is intentional, not an oversight; loosening it would reintroduce the exact false-positive risk it exists to prevent.
- **Bug C:** Removed the dead ET per-symbol feed from `RSS_FEEDS["IN"]`. The macro feed's ET URL (a numeric ID, verified live to return valid RSS) was left untouched — it's a genuinely different, working endpoint.
- **Bug D:** Removed the dead Yahoo Finance RSS feed from both `RSS_FEEDS["US"]`/`["IN"]` and `MACRO_FEEDS["US"]`/`["IN"]`.

## 4. What this does not do

- Does not catch every real-world headline pattern — abbreviated brand references like "Dixon Tech" (vs. "Dixon Technologies") still don't match, since that would require partial-word substring matching the codebase deliberately avoids for false-positive safety. Live-verified after the fix: 3 of DIXON's ~10 recent articles now correctly classify as fresh + company-specific; a couple of abbreviated-title ones remain excluded. This is a real, accepted scope limit, not silently claimed as fully solved.
- Does not add a replacement source for the two removed dead feeds — no new provider integration researched or built in this pass. For US specifically, this leaves Google News as the sole `RSS_FEEDS` source (previously also sole in practice, since Yahoo was already silently dead).
- Does not change `SENTIMENT_MAX_AGE_DAYS` itself, `_confidence_engine`, or `_composite_signal`'s weight-redistribution logic — those were investigated and confirmed already correct.

## 5. Tests

- `test_news_pipeline_freshness_fixes.py` — 10 new tests: the exact reported DIXON headline pattern now matches, all-caps ticker and full 3-word phrase paths unchanged, the protective false-positive test's exact scenario re-asserted unaffected, single-word company names still can't match via the new path (structural guarantee, not just this fix), Google News recency operator present on every feed, both dead feeds removed while the working ET macro feed is confirmed still present.
- All 22 pre-existing `test_news_relevance.py` tests still pass unmodified.
- **Live end-to-end verification** (not just unit tests): called `NewsSentimentService.get_news_with_sentiment("DIXON", "IN")` directly against the real Google News/Moneycontrol feeds — 3 articles now correctly classify as fresh + company-specific, all dated within the prior week, where before this fix the section would have shown "Insufficient fresh company-specific news evidence."
- Full backend suite: **2187/2187 passed** (2177 baseline + 10 new).
- No frontend changes this release.

## 6. Rollback

All four fixes are independent and additive/corrective within a single file (`news_sentiment.py`) — any can be reverted individually without affecting the others. No schema, no API contract change.
