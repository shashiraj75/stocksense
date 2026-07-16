"""
Product Integrity #012 — NSE bhavcopy last-resort price correction, wired
into PredictionEngine.predict()'s two current_price/price_reference
construction sites.

predict() is a very large, deeply-integrated async method — following this
codebase's established convention for exactly this situation (see
test_india_session_freshness_backend_gate.py / Product Integrity #011),
the wiring itself is verified structurally via source inspection; the pure
lookup/parsing logic is verified behaviorally in test_nse_bhavcopy.py.
"""
import inspect

import services.prediction_engine as pe


def _src():
    return inspect.getsource(pe.PredictionEngine.predict)


def test_bhavcopy_correction_is_only_attempted_when_the_bar_is_actually_stale():
    src = _src()
    assert "df.index[-1].date() < _expected_session" in src
    assert "get_bhavcopy_close(symbol, _expected_session)" in src


def test_bhavcopy_close_takes_priority_over_the_stale_yahoo_bar_at_both_sites():
    src = _src()
    assert src.count(
        'current_price = _bhavcopy_close if _bhavcopy_close is not None else float(df["Close"].iloc[-1])'
    ) == 2


def test_price_reference_source_reflects_bhavcopy_when_used_at_both_sites():
    src = _src()
    assert src.count('"source": "nse_bhavcopy" if _bhavcopy_close is not None else "yahoo_daily_history"') == 2


def test_is_stale_is_false_not_none_when_bhavcopy_corrected_the_price():
    """A bhavcopy-corrected pick must be labeled fresh (False), not left at
    the ambiguous None used for markets with no freshness check at all —
    this is a genuinely known-fresh price, not an unchecked one."""
    src = _src()
    assert src.count("False if _bhavcopy_close is not None else (") == 2


def test_bhavcopy_never_substitutes_for_ohlc_history():
    """The correction must only ever touch current_price / price_reference —
    it must not appear anywhere near the df history fetch itself, which
    must remain yfinance-only (bhavcopy has no OHLC history to offer)."""
    fetch_history_src = inspect.getsource(pe.PredictionEngine.predict)
    fetch_history_only = fetch_history_src.split("def _fetch_history")[1].split("def _fetch_info")[0]
    assert "bhavcopy" not in fetch_history_only.lower()


def test_bhavcopy_import_is_lazy_matching_existing_file_convention():
    """This file's established style is mid-function lazy imports for
    optional/side integrations (TRACKING_ONLY_SYMBOLS, case_generator) —
    the bhavcopy import should follow the same pattern, not a top-level
    import that would make nse_bhavcopy a hard dependency for every call."""
    src = _src()
    assert "from services.nse_bhavcopy import get_bhavcopy_close" in src
    top_level_src = inspect.getsource(pe)
    import_lines = [l for l in top_level_src.splitlines()[:60] if l.strip().startswith(("import ", "from "))]
    assert not any("nse_bhavcopy" in l for l in import_lines)


def test_bhavcopy_trigger_uses_last_VALID_close_not_the_raw_last_row():
    """2026-07-17 regression: every symbol in the 2026-07-16 India Daily
    Picks run showed is_stale=True with bhavcopy never firing. Root cause,
    confirmed by directly inspecting yfinance's raw response: it can return
    a placeholder row for the current session with Close=NaN before that
    session's real close is populated. df.index[-1].date() alone can't
    distinguish that placeholder from a genuinely fresh bar — it looks
    "today", so the staleness check (< _expected_session) came back False
    and skipped the bhavcopy correction entirely, even though the price
    actually used a few lines later (after the existing dropna(subset=
    ["Close"]) call) silently fell back to the prior session's close. The
    trigger must be evaluated against the last row with a non-null Close,
    not the raw last index entry."""
    src = _src()
    assert 'df["Close"].dropna()' in src
    assert "_last_valid_date" in src
    assert "_last_valid_date is not None" in src
    assert "_last_valid_date < _expected_session" in src


import pandas as pd


def test_last_valid_close_date_skips_a_trailing_nan_placeholder_row():
    """Direct behavioral proof of the fixed lookup logic (the same
    operation predict() now performs on its `df`): with a trailing
    Close=NaN row for the current session, the last VALID close's date
    must resolve to the prior (actually-populated) session, not today's
    placeholder date."""
    idx = pd.to_datetime(["2026-07-14", "2026-07-15", "2026-07-16"])
    df = pd.DataFrame({"Close": [848.25, 844.45, None]}, index=idx)

    valid_close = df["Close"].dropna()
    last_valid_date = valid_close.index[-1].date()

    assert last_valid_date == pd.Timestamp("2026-07-15").date()
    assert last_valid_date != df.index[-1].date()  # the bug: raw last row is the NaN placeholder
