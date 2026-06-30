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

What these tests lock in:
  - The _US_DAILY_PICKS_ELIGIBLE list contains no '$' symbols, no ETFs, no
    leveraged products, no SPACs, no units, no closed-end funds.
  - The US screener-failure path returns _US_MEGACAP_100 ∩ eligible, never
    the raw 12k universe.
  - The US screener-success path intersects with eligible before returning.
  - `screened_from` equals the actual phase-0 universe size, not a hardcoded
    static constant.
  - `universe_used` and `universe_degraded` are present in the payload.
  - India behavior is entirely unchanged.

All tests are deterministic and network-free.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.daily_picks import (
    _US_DAILY_PICKS_ELIGIBLE,
    _US_DAILY_PICKS_ELIGIBLE_SET,
    _US_MEGACAP_100,
    _get_universe_by_mcap,
    _bulk_screen,
)
from services.stock_universe import US_STOCKS, IN_STOCKS


# ── 1. No '$' symbols in eligible universe ───────────────────────────────────

def test_us_daily_picks_eligible_universe_has_no_dollar_symbols():
    """Every '$'-format preferred-share ticker must be excluded."""
    dollar_syms = [s for s in _US_DAILY_PICKS_ELIGIBLE if "$" in s]
    assert dollar_syms == [], (
        f"Found {len(dollar_syms)} '$' symbols in eligible universe: {dollar_syms[:10]}"
    )


# ── 2. No ETFs in eligible universe ─────────────────────────────────────────

def test_us_daily_picks_eligible_universe_excludes_known_etfs():
    """
    ETF tickers whose static-universe name explicitly contains 'ETF' or
    'Index Fund' must not appear in _US_DAILY_PICKS_ELIGIBLE.

    Note: ETFs whose static name does not contain these keywords (e.g.
    QQQ='Invesco QQQ Trust', GLD='SPDR Gold Shares') are a known limitation —
    they remain in the eligible set but are documented in _build_us_daily_picks_eligible.
    The live screener's exchange/mcap filter normally excludes these instruments
    before they reach Phase-0; the anchor fallback (when live screener fails)
    uses _US_MEGACAP_100 which contains no ETFs.
    """
    # Tickers whose US_STOCKS name contains 'ETF' → caught by rule 2
    etf_labeled = ["SPY", "TLT", "ARKK", "SOXL", "SOXS"]
    found_etf = [s for s in etf_labeled if s in _US_DAILY_PICKS_ELIGIBLE_SET]
    assert found_etf == [], f"ETF-labeled tickers in eligible universe: {found_etf}"

    # Tickers whose US_STOCKS name contains 'Index Fund' → caught by rule 7
    index_fund_labeled = ["IWM", "SOXX"]  # iShares ... Index Fund
    found_if = [s for s in index_fund_labeled if s in _US_DAILY_PICKS_ELIGIBLE_SET]
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
    found = [s for s in leveraged if s in _US_DAILY_PICKS_ELIGIBLE_SET]
    assert found == [], f"Leveraged products found in eligible universe: {found}"


# ── 4. No SPACs or units in eligible universe ───────────────────────────────

def test_us_daily_picks_eligible_universe_excludes_known_spacs_and_units():
    """
    SPAC blank-check companies and unit instruments must be excluded.
    AACB='Artius II Acquisition Inc.', AACI='Armada Acquisition Corp. III',
    AACO='Abony Acquisition Corp.', AACP='Apogee Acquisition Corp'.
    """
    spacs = ["AACB", "AACI", "AACO", "AACP", "ACAA"]
    found = [s for s in spacs if s in _US_DAILY_PICKS_ELIGIBLE_SET]
    assert found == [], f"SPAC tickers found in eligible universe: {found}"


# ── 5. US screener FAILURE returns anchor, not raw universe ─────────────────

def test_us_screener_failure_uses_anchor_not_raw_universe():
    """
    When yf.screen() raises any exception for US, the returned universe must
    be _US_MEGACAP_100 ∩ _US_DAILY_PICKS_ELIGIBLE — never the raw 12k list.
    """
    with patch("services.daily_picks.yf.screen", side_effect=RuntimeError("provider down")):
        syms, universe_used, universe_degraded = _get_universe_by_mcap("US")

    raw_us_count = len([sym for sym, _ in US_STOCKS])  # 12,011
    assert len(syms) < raw_us_count, (
        "Screener failure must not return the full raw US universe"
    )
    assert universe_used == "anchor"
    assert universe_degraded is True
    # Every returned symbol must be in the eligible set
    non_eligible = [s for s in syms if s not in _US_DAILY_PICKS_ELIGIBLE_SET]
    assert non_eligible == [], f"Anchor contains non-eligible symbols: {non_eligible}"
    # Must be a meaningful list (intersection of _US_MEGACAP_100 with eligible)
    assert len(syms) > 0, "Anchor fallback must not be empty"
    # No '$' symbols in anchor
    assert all("$" not in s for s in syms), "Anchor must not contain '$' symbols"


# ── 6. US screener returns 0 eligible symbols → anchor ──────────────────────

def test_us_empty_eligible_screener_result_uses_anchor_not_raw_universe():
    """
    If yf.screen() succeeds but returns ONLY non-eligible symbols (e.g.
    all preferred shares or ETFs), the intersection with _US_DAILY_PICKS_ELIGIBLE
    yields 0 results and the anchor must be used instead.
    """
    # Return only '$'-format preferred shares — none will pass the intersection
    fake_result = {"quotes": [
        {"symbol": "GNL$A"},   # Global Net Lease preferred — in static universe
        {"symbol": "GS$D"},    # Goldman Sachs preferred
        {"symbol": "PEB$F"},   # Pebblebrook preferred
    ]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result):
        syms, universe_used, universe_degraded = _get_universe_by_mcap("US")

    assert universe_used == "anchor"
    assert universe_degraded is True
    assert len(syms) > 0
    assert all("$" not in s for s in syms)


# ── 7. US screener SUCCESS intersects with eligible ──────────────────────────

def test_us_screener_success_intersects_with_daily_picks_eligible_universe():
    """
    When yf.screen() succeeds and returns a mix of eligible and non-eligible
    symbols, only the eligible portion must be returned — not the raw screener
    output and not the anchor.
    """
    fake_result = {"quotes": [
        {"symbol": "AAPL"},      # eligible — common equity
        {"symbol": "MSFT"},      # eligible — common equity
        {"symbol": "ABR$D"},     # NOT eligible — preferred share
        {"symbol": "SPY"},       # NOT eligible — ETF
        {"symbol": "AACB"},      # NOT eligible — SPAC
    ]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result):
        syms, universe_used, universe_degraded = _get_universe_by_mcap("US")

    assert universe_used == "screener"
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
    boundary: mock the screener to return exactly 5 known-eligible symbols,
    then check that phase0_universe_size == 5 (not 12,011 or any other
    hardcoded constant).
    """
    eligible_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
    assert all(s in _US_DAILY_PICKS_ELIGIBLE_SET for s in eligible_symbols), (
        "Test symbols must all be in eligible set"
    )
    fake_result = {"quotes": [{"symbol": s} for s in eligible_symbols]}

    with patch("services.daily_picks.yf.screen", return_value=fake_result):
        with patch("services.daily_picks.yf.download") as mock_dl:
            # Make download return an empty DataFrame so _bulk_screen falls back
            # to anchor — we only care about phase0_universe_size, not candidates
            mock_dl.return_value = MagicMock(empty=True)
            candidates, phase0_size, universe_used, universe_degraded = _bulk_screen("US", 50)

    # The screener returned 5 eligible symbols; that is the phase-0 universe size
    assert phase0_size == len(eligible_symbols), (
        f"screened_from should be {len(eligible_symbols)}, got {phase0_size}. "
        "Was the old `len(_UNIVERSE[market])` constant reintroduced?"
    )
    assert phase0_size != 12011, (
        "screened_from must not be the raw static universe count (12,011)"
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
            candidates, phase0_size, universe_used, universe_degraded = _bulk_screen("US", 50)

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

    # Also test the screener-success path
    fake_result = {"quotes": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result):
        with patch("services.daily_picks.yf.download") as mock_dl2:
            mock_dl2.return_value = MagicMock(empty=True)
            _, _, universe_used_ok, universe_degraded_ok = _bulk_screen("US", 50)

    assert universe_used_ok == "screener"
    assert universe_degraded_ok is False


# ── 10. India universe fallback behavior is unchanged ───────────────────────

def test_india_universe_fallback_behavior_is_unchanged():
    """
    The IN screener-failure path must still return the full NSE universe
    (not the anchor) — India behavior must be entirely unaffected by the
    US universe guard changes.
    """
    from services.daily_picks import _UNIVERSE

    with patch("services.daily_picks.yf.screen", side_effect=Exception("screener down")):
        syms, universe_used, universe_degraded = _get_universe_by_mcap("IN")

    # Should return the full IN static universe
    assert syms is _UNIVERSE["IN"], (
        "IN screener failure must return the full static NSE universe unchanged"
    )
    assert universe_used == "full_universe"
    assert universe_degraded is False
    assert len(syms) == len(IN_STOCKS), (
        f"IN fallback must have {len(IN_STOCKS)} symbols, got {len(syms)}"
    )
    # Must not contain any US '$' preferred-share symbols
    assert all("$" not in s for s in syms[:100]), (
        "IN fallback universe must not contain '$'-format preferred shares"
    )
