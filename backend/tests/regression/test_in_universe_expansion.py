"""
Release 12B — India Daily Picks universe expansion and coverage truthfulness.

Root cause (Daily Picks integrity audit): yfinance's screen() routes custom
EquityQuery page sizing through `size` (default 100, Yahoo max 250); `count`
only applies to predefined queries (default 25). The old call passed
count=250 with a custom query, so Yahoo served exactly 25 rows — sorted by
market cap descending, the India universe silently became the 25 largest NSE
companies, excluding mid/small caps before ranking started.

These tests mock yf.screen entirely — no Yahoo, no network, no providers.
"""

from unittest.mock import patch

import services.daily_picks as dp


def _page(symbols):
    return {"quotes": [{"symbol": f"{s}.NS"} for s in symbols]}


def _syms(prefix, n, start=0):
    return [f"{prefix}{i:04d}" for i in range(start, start + n)]


def _get_in_universe(pages):
    """Run _get_universe_by_mcap('IN') with yf.screen serving the given pages
    (then empty pages forever)."""
    calls = {"n": 0}

    def fake_screen(query, offset=None, size=None, count=None,
                    sortField=None, sortAsc=None):
        idx = calls["n"]
        calls["n"] += 1
        return pages[idx] if idx < len(pages) else {"quotes": []}

    with patch.object(dp.yf, "screen", side_effect=fake_screen):
        result = dp._get_universe_by_mcap("IN")
    return result, calls["n"]


# ─────────────────────────────────────────────────────────────────────────────
# Pagination collection
# ─────────────────────────────────────────────────────────────────────────────

def test_single_25_row_page_is_not_a_healthy_screener_universe():
    # The exact production failure: ask for 250, provider serves one 25-row
    # page. Must NOT be labelled a healthy broad screener universe.
    (symbols, used, degraded, raw), _ = _get_in_universe([_page(_syms("BIG", 25))])
    assert used == "static_fallback"
    assert degraded is True
    assert raw == 25  # truthful telemetry: what the provider actually served
    assert symbols == list(dp._NIFTY_100)  # bounded curated fallback


def test_repeated_provider_pages_do_not_loop_forever():
    same = _page(_syms("REP", 250))
    (symbols, used, degraded, raw), n_calls = _get_in_universe([same] * 50)
    # Identical page repeats → loop stops immediately after detecting it,
    # never exceeding the hard page bound.
    assert n_calls <= dp._SCREEN_MAX_PAGES
    assert used == "screener" and degraded is False
    assert len(symbols) == 250


def test_multiple_pages_build_a_broad_universe():
    pages = [_page(_syms("NSE", 250, 0)), _page(_syms("NSE", 250, 250))]
    (symbols, used, degraded, raw), _ = _get_in_universe(pages)
    assert used == "screener"
    assert degraded is False
    assert len(symbols) == dp._IN_TARGET_UNIVERSE == 250
    assert len(symbols) > 100


def test_symbols_are_normalized_deduplicated_and_deterministic():
    pages = [
        {"quotes": [
            {"symbol": "RELIANCE.NS"}, {"symbol": "TCS.NS"},
            {"symbol": "RELIANCE.NS"},           # duplicate within page
            {"symbol": None}, {"symbol": ""},    # malformed
            {"no_symbol": True}, "not-a-dict",   # malformed entries
        ] + [{"symbol": f"MID{i:04d}.NS"} for i in range(120)]
         + [{"symbol": "TCS.NS"}]},              # duplicate again
    ]
    (a, used, degraded, raw), _ = _get_in_universe(pages)
    (b, *_), _ = _get_in_universe(pages)
    assert a == b                                # deterministic
    assert a[0] == "RELIANCE" and a[1] == "TCS"  # suffix stripped, order kept
    assert len(a) == len(set(a))                 # deduplicated
    assert len(a) == 122
    assert used == "screener" and degraded is False


def test_healthy_result_reports_actual_counts():
    pages = [_page(_syms("OK", 180))]
    (symbols, used, degraded, raw), _ = _get_in_universe(pages)
    assert used == "screener"
    assert degraded is False
    assert len(symbols) == 180  # actual, not the 250 target
    assert raw == 180


def test_screener_exception_uses_truthful_static_fallback():
    with patch.object(dp.yf, "screen", side_effect=RuntimeError("boom")):
        symbols, used, degraded, raw = dp._get_universe_by_mcap("IN")
    assert used == "static_fallback"
    assert degraded is True
    assert symbols == list(dp._NIFTY_100)
    # The bounded curated fallback never silently processes thousands.
    assert len(symbols) < 200


def test_fallback_never_claims_screener_coverage():
    (symbols, used, degraded, raw), _ = _get_in_universe([_page(_syms("TINY", 10))])
    assert used != "screener"
    assert used != "full_universe"
    assert degraded is True


# ─────────────────────────────────────────────────────────────────────────────
# Invariants: filters, caps, US behavior
# ─────────────────────────────────────────────────────────────────────────────

def test_market_cap_floor_unchanged():
    assert dp._MIN_MCAP_CR == 100      # crores INR, IN
    assert dp._MIN_MCAP_USD_M == 2000  # $M, US


def test_phase0_and_deep_analysis_caps_unchanged():
    assert dp._SCREEN_BATCH_SIZE == 300
    assert dp._N_CANDIDATES == 50


def test_us_universe_selection_unchanged():
    captured = {}

    def fake_screen(query, offset=None, size=None, count=None,
                    sortField=None, sortAsc=None):
        captured.update(offset=offset, size=size, count=count,
                        sortField=sortField, sortAsc=sortAsc)
        eligible = sorted(dp._US_DAILY_PICKS_HEURISTIC_FILTERED_SET)[:30]
        return {"quotes": [{"symbol": s} for s in eligible]}

    with patch.object(dp.yf, "screen", side_effect=fake_screen) as mock:
        symbols, used, degraded, raw = dp._get_universe_by_mcap("US")

    assert mock.call_count == 1                 # US: single call, no paging
    assert captured["count"] == 250             # original US request preserved
    assert captured["offset"] is None and captured["size"] is None
    assert used == "screener" and degraded is False
    assert raw == 30
    assert all(s in dp._US_DAILY_PICKS_HEURISTIC_FILTERED_SET for s in symbols)


def test_universe_target_metadata_constant():
    assert dp._IN_TARGET_UNIVERSE == 250
    assert dp._IN_MIN_HEALTHY_UNIVERSE == 100
