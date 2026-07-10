"""
Product Integrity Workstream #002D-B — US Daily Picks eligible-universe guard.

Root causes addressed:
  1. The raw ~12k US static universe (containing 368 preferred shares with '$'
     in the ticker, ~4,908 ETFs, ~264 SPACs, ~58 ETNs, ~26 units, ~33
     closed-end funds, and 4 leveraged products not caught as ETFs) was being
     passed to yf.download() in Phase-0 whenever the yf.screen() call failed.
     This produced Yahoo errors for '$'-format preferred tickers, consumed
     many hours of Railway compute, and delivered no user value (ETFs/SPACs
     have no company fundamentals to analyse).

  2. `screened_from` in the Daily Picks payload was hardcoded to
     `len(_UNIVERSE["US"])` = always 12,011 regardless of what the screener
     actually returned — causing the UI to always show "12,079 US stocks"
     (reflecting the static universe at the time of the stuck run, not the
     actual filtered universe).

What these tests lock in (#002D-B + #002D-C2 hardening):
  - The _US_DAILY_PICKS_HEURISTIC_FILTERED list contains no '$' symbols, no ETFs,
    no leveraged products, no SPACs, no units, no closed-end funds.
  - ETF and ETN exclusion is whole-word and case-insensitive (avoids Netflix FP).
  - The US screener-failure path returns all 100 _US_MEGACAP_100 symbols directly,
    never the raw 12k universe; anchor is independent of the heuristic-filtered set.
  - The US screener-success path intersects with heuristic-filtered before returning.
  - `screened_from` equals the actual phase-0 universe size, not a hardcoded constant.
  - `universe_used` and `universe_degraded` are present in the payload.
  - India behavior is entirely unchanged.

All tests are deterministic and network-free.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.daily_picks import (
    _US_DAILY_PICKS_HEURISTIC_FILTERED,
    _US_DAILY_PICKS_HEURISTIC_FILTERED_SET,
    _US_MEGACAP_100,
    _build_us_daily_picks_heuristic_filtered,
    _get_universe_by_mcap,
    _bulk_screen,
)
from services.stock_universe import US_STOCKS, IN_STOCKS


# ── 1. No '$' symbols in eligible universe ───────────────────────────────────

def test_us_daily_picks_eligible_universe_has_no_dollar_symbols():
    """Every '$'-format preferred-share ticker must be excluded."""
    dollar_syms = [s for s in _US_DAILY_PICKS_HEURISTIC_FILTERED if "$" in s]
    assert dollar_syms == [], (
        f"Found {len(dollar_syms)} '$' symbols in eligible universe: {dollar_syms[:10]}"
    )


# ── 2. No ETFs in eligible universe ─────────────────────────────────────────

def test_us_daily_picks_eligible_universe_excludes_known_etfs():
    """
    ETF tickers whose static-universe name explicitly contains 'ETF' or
    'Index Fund' must not appear in _US_DAILY_PICKS_HEURISTIC_FILTERED.

    Note: ETFs whose static name does not contain these keywords (e.g.
    QQQ='Invesco QQQ Trust', GLD='SPDR Gold Shares') are a known limitation —
    they remain in the eligible set but are documented in _build_us_daily_picks_eligible.
    The live screener's exchange/mcap filter normally excludes these instruments
    before they reach Phase-0; the anchor fallback (when live screener fails)
    uses _US_MEGACAP_100 which contains no ETFs.
    """
    # Tickers whose US_STOCKS name contains 'ETF' → caught by rule 2
    etf_labeled = ["SPY", "TLT", "ARKK", "SOXL", "SOXS"]
    found_etf = [s for s in etf_labeled if s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET]
    assert found_etf == [], f"ETF-labeled tickers in eligible universe: {found_etf}"

    # Tickers whose US_STOCKS name contains 'Index Fund' → caught by rule 7
    index_fund_labeled = ["IWM", "SOXX"]  # iShares ... Index Fund
    found_if = [s for s in index_fund_labeled if s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET]
    assert found_if == [], f"Index-fund-labeled tickers in eligible universe: {found_if}"


# ── 3. No leveraged/inverse products in eligible universe ───────────────────

def test_us_daily_picks_eligible_universe_excludes_known_leveraged_inverse_products():
    """
    Leveraged/inverse daily products (non-ETF-labelled) must not appear.
    TMF/TMV/TYD/TYO are Direxion daily Treasury products without 'ETF' in
    the name; caught by ' 3x' rule.
    TQQQ/SQQQ are ProShares UltraPro products without 'ETF' in the name;
    caught by 'ultrapro' rule.
    """
    leveraged = ["TMF", "TMV", "TYD", "TYO", "TQQQ", "SQQQ"]
    found = [s for s in leveraged if s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET]
    assert found == [], f"Leveraged products found in eligible universe: {found}"


# ── 4. No SPACs or units in eligible universe ───────────────────────────────

def test_us_daily_picks_eligible_universe_excludes_known_spacs_and_units():
    """
    SPAC blank-check companies and unit instruments must be excluded.
    AACB='Artius II Acquisition Inc.', AACI='Armada Acquisition Corp. III',
    AACO='Abony Acquisition Corp.', AACP='Apogee Acquisition Corp'.
    """
    spacs = ["AACB", "AACI", "AACO", "AACP", "ACAA"]
    found = [s for s in spacs if s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET]
    assert found == [], f"SPAC tickers found in eligible universe: {found}"


# ── 5. US screener FAILURE returns anchor, not raw universe ─────────────────

def test_us_screener_failure_uses_anchor_not_raw_universe():
    """
    When yf.screen() raises any exception for US, the returned universe must
    be the complete _US_MEGACAP_100 list (all 100 symbols) — never the raw
    12k list.  The anchor is now independent of the heuristic-filtered set
    (#002D-C2): symbols present in _US_MEGACAP_100 but absent from US_STOCKS
    (e.g. MMC, FI) are still included.
    """
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("provider down")):
        syms, universe_used, universe_degraded, _, _meta = _get_universe_by_mcap("US")

    raw_us_count = len([sym for sym, _ in US_STOCKS])  # 12,011
    assert len(syms) < raw_us_count, (
        "Screener failure must not return the full raw US universe"
    )
    assert universe_used == "anchor"
    assert universe_degraded is True
    # Anchor must be the complete megacap list, independent of heuristic-filtered set
    assert len(syms) == len(_US_MEGACAP_100), (
        f"Anchor must have {len(_US_MEGACAP_100)} symbols, got {len(syms)}. "
        "Was it accidentally intersected with _US_DAILY_PICKS_HEURISTIC_FILTERED_SET?"
    )
    assert set(syms) == set(_US_MEGACAP_100), "Anchor symbols must exactly match _US_MEGACAP_100"
    # No '$' symbols in anchor (sanity — _US_MEGACAP_100 contains none)
    assert all("$" not in s for s in syms), "Anchor must not contain '$' symbols"


# ── 6. US screener returns 0 eligible symbols → anchor ──────────────────────

def test_us_empty_eligible_screener_result_uses_anchor_not_raw_universe():
    """
    If yf.screen() succeeds but returns ONLY non-eligible symbols (e.g.
    all preferred shares or ETFs), the intersection with _US_DAILY_PICKS_HEURISTIC_FILTERED
    yields 0 results and the anchor must be used instead.
    """
    # Return only '$'-format preferred shares — none will pass the intersection
    fake_result = {"quotes": [
        {"symbol": "GNL$A"},   # Global Net Lease preferred — in static universe
        {"symbol": "GS$D"},    # Goldman Sachs preferred
        {"symbol": "PEB$F"},   # Pebblebrook preferred
    ]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result):
        syms, universe_used, universe_degraded, _, _meta = _get_universe_by_mcap("US")

    assert universe_used == "anchor"
    assert universe_degraded is True
    assert len(syms) > 0
    assert all("$" not in s for s in syms)


# ── 7. Cache SUCCESS intersects with eligible, and stratifies by cap tier ────

def test_us_cache_success_intersects_with_daily_picks_eligible_universe():
    """
    When stock_fundamentals_cache returns a healthy universe (post the
    large/mid/small-cap stratification replacing yf.screen()), only the
    heuristic-eligible portion must be returned — not the raw cache output
    and not the anchor. Uses 150 synthetic large-cap-range entries so the
    result clears _MIN_HEALTHY_UNIVERSE (100) and isn't itself treated as a
    degraded/thin cache.
    """
    padding = [(f"PAD{i}", 50_000.0) for i in range(150)]  # all "large" tier, all eligible
    fake_ranked = [
        ("AAPL", 3_000_000.0),   # eligible — common equity
        ("MSFT", 3_000_000.0),   # eligible — common equity
        ("ABR$D", 1_000.0),      # NOT eligible — preferred share
        ("SPY", 500_000.0),      # NOT eligible — ETF
        ("AACB", 100.0),         # NOT eligible — SPAC
    ] + padding
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=fake_ranked):
        syms, universe_used, universe_degraded, _, _meta = _get_universe_by_mcap("US")

    assert universe_used == "fundamentals_cache"
    assert universe_degraded is False
    assert "AAPL" in syms
    assert "MSFT" in syms
    # Non-eligible instruments must be stripped
    assert "ABR$D" not in syms, "Preferred share must not survive eligibility intersection"
    assert "SPY" not in syms, "ETF must not survive eligibility intersection"
    assert "AACB" not in syms, "SPAC must not survive eligibility intersection"


# ── 8. screened_from equals actual phase-0 universe size ────────────────────

def test_screened_from_equals_actual_phase_zero_universe_size():
    """
    `screened_from` in the payload must equal the actual count of tickers
    passed to yf.download() in Phase 0 — not `len(_UNIVERSE["US"])`.

    We verify this by intercepting _bulk_screen at the _get_universe_by_mcap
    boundary: mock the cache to return exactly 5 known-eligible symbols
    (padded above _MIN_HEALTHY_UNIVERSE so it isn't treated as a thin/
    degraded cache), then check that phase0_universe_size == 5 + padding's
    quota-eligible slice (not 12,011 or any other hardcoded constant) — the
    actual point is that it is NOT the raw static universe count.
    """
    eligible_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
    assert all(s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET for s in eligible_symbols), (
        "Test symbols must all be in the heuristic-filtered set"
    )
    # Padding must be REAL eligible tickers (not synthetic symbols) — the US
    # branch intersects with _US_DAILY_PICKS_HEURISTIC_FILTERED_SET before
    # stratifying, so a fabricated symbol not in that set is correctly
    # stripped, same as a real-world ETF/preferred/SPAC would be.
    padding_pool = [s for s in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET if s not in eligible_symbols][:150]
    padding = [(s, 50_000.0) for s in padding_pool]
    fake_ranked = [(s, 3_000_000.0) for s in eligible_symbols] + padding

    with patch("services.fundamentals_cache.get_ranked_universe", return_value=fake_ranked):
        with patch("services.daily_picks.yf.download") as mock_dl:
            # Make download return an empty DataFrame so _bulk_screen falls back
            # to anchor — we only care about phase0_universe_size, not candidates
            mock_dl.return_value = MagicMock(empty=True)
            candidates, phase0_size, universe_used, universe_degraded, _, _meta = _bulk_screen("US", 50)

    assert phase0_size != 12011, (
        "screened_from must not be the raw static universe count (12,011)"
    )
    assert phase0_size == len(eligible_symbols) + len(padding), (
        f"screened_from should reflect the stratified-cache universe size, got {phase0_size}"
    )


# ── 9. universe_used and universe_degraded present in payload ───────────────

def test_universe_used_and_universe_degraded_are_present_in_payload():
    """
    The Daily Picks payload built by _generate_picks_inner must include both
    `universe_used` and `universe_degraded` with their expected types and values.

    We verify this by inspecting the _bulk_screen return value that feeds the
    payload, then asserting that the payload dict constructed in
    _generate_picks_inner contains those fields correctly.  We do this
    indirectly: the _bulk_screen function already returns (candidates, size,
    universe_used, universe_degraded), and _generate_picks_inner unpacks them
    directly into the payload.  A static structural inspection of daily_picks.py
    confirms both fields are wired into the payload dict — this test confirms
    the data flows through _bulk_screen correctly when the screener succeeds.
    """
    # Test the anchor path: screener fails → _get_universe_by_mcap returns anchor
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("down")):
        with patch("services.daily_picks.yf.download") as mock_dl:
            mock_dl.return_value = MagicMock(empty=True)
            candidates, phase0_size, universe_used, universe_degraded, _, _meta = _bulk_screen("US", 50)

    # Confirm the metadata is present and correctly typed
    assert isinstance(universe_used, str), "universe_used must be a string"
    assert isinstance(universe_degraded, bool), "universe_degraded must be a bool"
    assert universe_used == "anchor", f"Expected 'anchor', got '{universe_used}'"
    assert universe_degraded is True, "Degraded must be True when anchor is used"
    assert isinstance(phase0_size, int), "phase0_size must be an int"
    assert phase0_size > 0, "phase0_size must be positive"
    # Phase-0 size in anchor mode is the anchor list size, not the raw universe
    assert phase0_size < 200, (
        f"Anchor phase0_size ({phase0_size}) should be ≤ 100 megacap symbols; "
        "a large value suggests the raw universe was inadvertently used"
    )

    # Also test the healthy-cache path
    padding = [(f"PAD{i}", 50_000.0) for i in range(150)]
    fake_ranked = [("AAPL", 3_000_000.0), ("MSFT", 3_000_000.0)] + padding
    with patch("services.fundamentals_cache.get_ranked_universe", return_value=fake_ranked):
        with patch("services.daily_picks.yf.download") as mock_dl2:
            mock_dl2.return_value = MagicMock(empty=True)
            _, _, universe_used_ok, universe_degraded_ok, _, _meta = _bulk_screen("US", 50)

    assert universe_used_ok == "fundamentals_cache"
    assert universe_degraded_ok is False


# ── 10. India universe fallback behavior is unchanged ───────────────────────

def test_india_universe_fallback_is_bounded_and_truthful():
    """
    Release 12B: the IN screener-failure path must NOT return the US anchor,
    and must fall back to the bounded curated NIFTY-100 static list —
    truthfully labelled `static_fallback` and flagged degraded. The old
    unbounded full-NSE (~2.4k symbols) fallback is retired.
    """
    from services.daily_picks import _NIFTY_100, _US_MEGACAP_100

    with patch("services.daily_picks.yf.screen", side_effect=Exception("screener down")):
        syms, universe_used, universe_degraded, _, _meta = _get_universe_by_mcap("IN")

    assert syms == list(_NIFTY_100), (
        "IN screener failure must return the curated NIFTY-100 static fallback"
    )
    assert syms != list(_US_MEGACAP_100)  # never the US anchor
    assert universe_used == "static_fallback"
    assert universe_degraded is True
    # Bounded: never silently process the thousands-strong full NSE list.
    assert len(syms) < 300
    # Must not contain any US '$' preferred-share symbols
    assert all("$" not in s for s in syms), (
        "IN fallback universe must not contain '$'-format preferred shares"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ── #002D-C2 hardening tests ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# ── 11. Anchor has exactly 100 unique symbols ────────────────────────────────

def test_anchor_has_exactly_100_unique_symbols():
    """
    _US_MEGACAP_100 is a curated 100-symbol list. The anchor fallback must
    return all 100 symbols — not a subset produced by intersecting with the
    heuristic-filtered set (#002D-C2 fix: intersection removed).
    """
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("down")):
        syms, universe_used, _, _, _meta = _get_universe_by_mcap("US")
    assert universe_used == "anchor"
    assert len(syms) == 100, (
        f"Anchor must have 100 symbols, got {len(syms)}. "
        "Was the anchor intersected with _US_DAILY_PICKS_HEURISTIC_FILTERED_SET?"
    )
    assert len(set(syms)) == 100, "Anchor must have no duplicate symbols"


# ── 12. Anchor is independent of heuristic-filtered set ─────────────────────

def test_anchor_includes_symbols_absent_from_heuristic_filtered_set():
    """
    MMC (Marsh & McLennan) and FI (Fiserv) are in _US_MEGACAP_100 but absent
    from US_STOCKS and therefore absent from _US_DAILY_PICKS_HEURISTIC_FILTERED_SET.
    After #002D-C2, the anchor must include them (anchor is no longer
    intersected with the filtered set).
    """
    symbols_not_in_filtered = [s for s in _US_MEGACAP_100 if s not in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET]
    assert len(symbols_not_in_filtered) > 0, (
        "Precondition: at least one _US_MEGACAP_100 symbol must be absent from "
        "_US_DAILY_PICKS_HEURISTIC_FILTERED_SET (e.g. MMC or FI absent from US_STOCKS)"
    )
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("down")):
        syms, universe_used, _, _, _meta = _get_universe_by_mcap("US")
    assert universe_used == "anchor"
    for sym in symbols_not_in_filtered:
        assert sym in syms, (
            f"Anchor must include {sym} even though it is absent from "
            "_US_DAILY_PICKS_HEURISTIC_FILTERED_SET"
        )


# ── 13. ETF exclusion is whole-word and case-insensitive ────────────────────

def test_etf_exclusion_is_case_insensitive():
    """
    The heuristic filter must exclude a security whose name contains 'etf'
    in any case (lowercase, mixed-case, uppercase) as a whole word.
    """
    synthetic = [
        ("AAA", "Hypothetical etf tracker"),          # lowercase whole-word
        ("BBB", "Mixed-Case Etf Product"),             # mixed-case whole-word
        ("CCC", "UPPERCASE ETF SECURITY"),             # uppercase whole-word (already covered)
        ("DDD", "Legitimate Company Inc"),             # not an ETF — must be retained
    ]
    result = _build_us_daily_picks_heuristic_filtered(synthetic)
    assert "AAA" not in result, "Lowercase 'etf' (whole-word) must be excluded"
    assert "BBB" not in result, "Mixed-case 'Etf' (whole-word) must be excluded"
    assert "CCC" not in result, "Uppercase 'ETF' (whole-word) must be excluded"
    assert "DDD" in result, "Normal company name must not be excluded"


# ── 14. ETN exclusion is whole-word and case-insensitive ────────────────────

def test_etn_exclusion_is_case_insensitive():
    """
    The heuristic filter must exclude a security whose name contains 'etn'
    in any case as a whole word.
    """
    synthetic = [
        ("AAA", "iPath Series B S&P 500 VIX etn"),    # lowercase whole-word
        ("BBB", "ProShares Ultra Silver Etn"),          # mixed-case
        ("CCC", "Normal Company Corp"),                 # not ETN — must be retained
    ]
    result = _build_us_daily_picks_heuristic_filtered(synthetic)
    assert "AAA" not in result, "Lowercase 'etn' (whole-word) must be excluded"
    assert "BBB" not in result, "Mixed-case 'Etn' (whole-word) must be excluded"
    assert "CCC" in result, "Normal company name must not be excluded"


# ── 15. Netflix is NOT excluded (whole-word boundary avoids false positive) ──

def test_netflix_not_excluded_by_etf_rule():
    """
    'Netflix' contains 'etf' as a substring but NOT as a whole word.
    The whole-word regex r'\bETF\b' must not match it.
    """
    synthetic = [("NFLX", "Netflix, Inc.")]
    result = _build_us_daily_picks_heuristic_filtered(synthetic)
    assert "NFLX" in result, (
        "'Netflix, Inc.' must NOT be excluded by the ETF rule — "
        "'etf' is a substring of 'netflix' but not a whole word"
    )


# ── 16. NFLX present in live heuristic-filtered set ─────────────────────────

def test_nflx_is_in_heuristic_filtered_set():
    """Netflix (NFLX) must be in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET."""
    assert "NFLX" in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET, (
        "NFLX must be in the heuristic-filtered set — was Netflix accidentally "
        "excluded by a case-insensitive 'etf' substring check?"
    )


# ── 17. MMC and FI absent from heuristic-filtered set (precondition) ────────

def test_mmc_and_fi_absent_from_heuristic_filtered_set_but_present_in_megacap():
    """
    MMC and FI are in _US_MEGACAP_100 but absent from US_STOCKS, so they
    cannot be in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET.  This test locks in
    the precondition that the anchor independence fix in #002D-C2 was necessary.
    """
    for sym in ("MMC", "FI"):
        assert sym in _US_MEGACAP_100, f"{sym} must be in _US_MEGACAP_100"
        assert sym not in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET, (
            f"{sym} must NOT be in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET "
            "(it is absent from US_STOCKS and the filter is built from US_STOCKS)"
        )


# ── 18. Anchor phase-0 size is 100, not the raw static universe count ────────

def test_anchor_phase0_size_is_100_not_raw_count():
    """
    When the screener fails, _bulk_screen must report phase0_universe_size == 100
    (the anchor list size), not 12,011 (the raw static universe count).
    """
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("down")):
        with patch("services.daily_picks.yf.download") as mock_dl:
            mock_dl.return_value = MagicMock(empty=True)
            _, phase0_size, universe_used, _, _, _meta = _bulk_screen("US", 50)

    assert universe_used == "anchor"
    assert phase0_size == 100, (
        f"Anchor phase0_size must be 100, got {phase0_size}"
    )
    assert phase0_size != 12011, "phase0_size must not be the raw static universe count"


# ── 19. ETF-labeled tickers are excluded regardless of name capitalization ───

def test_etf_labeled_ticker_in_live_universe_excluded():
    """
    SPY and TLT are in US_STOCKS with names containing 'ETF' (uppercase).
    They must not appear in the heuristic-filtered set regardless of how
    the ETF check is implemented (the whole-word regex handles all cases).
    """
    etf_tickers = ["SPY", "TLT", "ARKK"]
    for sym in etf_tickers:
        assert sym not in _US_DAILY_PICKS_HEURISTIC_FILTERED_SET, (
            f"{sym} must be excluded from heuristic-filtered set (ETF)"
        )
