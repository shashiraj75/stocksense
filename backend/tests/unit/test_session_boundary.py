"""
Trade Postmortem Sprint 3A, Pre-Stage-H Correction 1 — session-boundary
resolution tests. Covers the full enumerated scenario list: exact
session open/close for both markets, intraday entry, DST, holiday,
early-close (documented as unsupported), before-open/after-close entry,
before-open exit, and same-day full/partial session trades.
"""
import datetime as dt
import os
import time as time_module

import pytest

from services.market_hours import ET, IST, NSE_EXTRA_HOLIDAYS
from services.postmortem.price_path_evidence import (
    ENTRY_BAR_INCLUDED_FULL,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EXIT_BAR_INCLUDED_FULL,
    EXIT_BAR_PARTIAL_UNKNOWN,
)
from services.postmortem.session_boundary import (
    AMBIGUOUS_RESOLUTION,
    EARLY_CLOSE_UNSUPPORTED,
    SAME_DAY_FULL_SESSION,
    SessionBoundaryError,
    classify_entry_boundary,
    classify_exit_boundary,
    classify_same_day_trade,
    is_trading_day,
    next_trading_session,
    previous_trading_session,
    resolve_session,
)

# 2026-06-02 is a Tuesday, a normal NSE/US trading day for both markets.
IN_TRADING_DAY = dt.date(2026, 6, 2)
US_TRADING_DAY = dt.date(2026, 6, 2)
US_DST_DAY = dt.date(2026, 6, 15)  # well within US daylight saving time
US_HOLIDAY = dt.date(2026, 7, 4)   # Independence Day (observed)


@pytest.mark.unit
class TestIndiaSessionBoundaries:
    def test_exact_session_open(self):
        ts = dt.datetime(2026, 6, 2, 9, 15, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL

    def test_intraday_entry(self):
        ts = dt.datetime(2026, 6, 2, 12, 0, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exact_session_close_exit(self):
        ts = dt.datetime(2026, 6, 2, 15, 30, tzinfo=IST)
        result = classify_exit_boundary("IN", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL


@pytest.mark.unit
class TestUSSessionBoundaries:
    def test_exact_session_open(self):
        ts = dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL

    def test_intraday_entry(self):
        ts = dt.datetime(2026, 6, 2, 13, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exact_session_close_exit(self):
        ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL

    def test_dst_date_session_open_still_930_local(self):
        """US_DST_DAY is well inside daylight saving — 9:30 ET local
        time must still resolve to session open regardless of the
        underlying UTC offset ET/DST introduces."""
        ts = dt.datetime(2026, 6, 15, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        session = resolve_session("US", US_DST_DAY)
        assert session["open"].utcoffset() == dt.timedelta(hours=-4)  # EDT, not EST

    def test_holiday_is_not_a_trading_day(self):
        assert is_trading_day("US", US_HOLIDAY) is False
        ts = dt.datetime(2026, 7, 4, 10, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert "non-trading day" in " ".join(result["limitations"])


@pytest.mark.unit
class TestEarlyCloseDocumentedLimitation:
    def test_every_session_resolution_discloses_early_close_unsupported(self):
        """The current market calendar cannot prove any early-close
        schedule — every resolution explicitly says so rather than
        silently assuming standard hours are always correct."""
        session = resolve_session("US", US_TRADING_DAY)
        assert EARLY_CLOSE_UNSUPPORTED in session["limitations"]
        session_in = resolve_session("IN", IN_TRADING_DAY)
        assert EARLY_CLOSE_UNSUPPORTED in session_in["limitations"]


@pytest.mark.unit
class TestBeforeOpenAfterClose:
    def test_before_open_entry(self):
        ts = dt.datetime(2026, 6, 2, 6, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "before official session open" in " ".join(result["limitations"])

    def test_after_close_entry_shifts_to_next_session(self):
        ts = dt.datetime(2026, 6, 2, 20, 0, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] is None
        assert result["effective_session_date"] == next_trading_session("US", US_TRADING_DAY)

    def test_before_open_exit_shifts_to_previous_session(self):
        ts = dt.datetime(2026, 6, 2, 6, 0, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] is None
        assert result["effective_session_date"] == previous_trading_session("US", US_TRADING_DAY)


@pytest.mark.unit
class TestSameDayTrades:
    def test_same_day_full_session_trade(self):
        entry = dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == SAME_DAY_FULL_SESSION

    def test_same_day_partial_session_trade_is_ambiguous(self):
        entry = dt.datetime(2026, 6, 2, 10, 0, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 14, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == AMBIGUOUS_RESOLUTION

    def test_same_day_entry_after_open_but_exit_at_close_is_ambiguous(self):
        entry = dt.datetime(2026, 6, 2, 10, 0, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET)
        result = classify_same_day_trade("US", entry, exit_ts)
        assert result["resolution"] == AMBIGUOUS_RESOLUTION


@pytest.mark.unit
class TestMiscBoundaryHygiene:
    def test_naive_timestamp_rejected(self):
        with pytest.raises(SessionBoundaryError):
            classify_entry_boundary("US", dt.datetime(2026, 6, 2, 9, 30))

    def test_unknown_market_rejected(self):
        with pytest.raises(SessionBoundaryError):
            resolve_session("XX", US_TRADING_DAY)

    def test_next_trading_session_skips_weekend(self):
        friday = dt.date(2026, 6, 5)  # Friday
        assert next_trading_session("US", friday) == dt.date(2026, 6, 8)  # Monday

    def test_previous_trading_session_skips_weekend(self):
        monday = dt.date(2026, 6, 8)
        assert previous_trading_session("US", monday) == dt.date(2026, 6, 5)


# ============================================================================
# Stage J3B.1 -- India/US session-boundary characterization and DST assurance
# (TEST-ONLY; characterizes current behavior, does not change semantics).
# ============================================================================

# 2026-01-15 is a confirmed NSE_EXTRA_HOLIDAYS entry (repository calendar,
# not fetched live) -- used for the India holiday characterization below.
IN_HOLIDAY = dt.date(2026, 1, 15)
assert IN_HOLIDAY.strftime("%Y-%m-%d") in NSE_EXTRA_HOLIDAYS

# DST transition dates for 2026, determined programmatically (not
# hardcoded from memory) -- see the paired computation in
# tests/postgres_integration/test_price_path_session_boundary_real_pg.py's
# module-level _dst_transition_dates() helper, which both this file's
# characterization tests and the real-PG tests derive from independently
# using zoneinfo, so a stdlib tzdata update cannot silently desync them.


def _find_dst_transitions(year: int) -> tuple[dt.date, dt.date]:
    """Walks every day of `year` and returns (spring_date, autumn_date) --
    the two calendar dates on which America/New_York's noon UTC offset
    changes. Pure stdlib zoneinfo, no hardcoded transition date."""
    prev_offset = None
    spring = autumn = None
    d = dt.date(year, 1, 1)
    one_day = dt.timedelta(days=1)
    while d.year == year:
        offset = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ET).utcoffset()
        if prev_offset is not None and offset != prev_offset:
            if offset > prev_offset:
                spring = d
            else:
                autumn = d
        prev_offset = offset
        d += one_day
    assert spring is not None and autumn is not None
    return spring, autumn


DST_SPRING_TRANSITION, DST_AUTUMN_TRANSITION = _find_dst_transitions(2026)


@pytest.mark.unit
class TestIndiaBoundaryMatrixStageJ3B1:
    """Stage J3B.1, Stage 2 -- strengthened India boundary matrix. Rows 1,
    4, 10 already exist as genuine boundary proofs in
    TestIndiaSessionBoundaries/TestBeforeOpenAfterClose above and are not
    duplicated; this class adds the still-missing 1-second-granularity,
    holiday, UTC-equivalence, and same-day rows."""

    def test_entry_one_second_before_open_is_partial_with_pre_open_limitation(self):
        ts = dt.datetime(2026, 6, 2, 9, 14, 59, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "before official session open" in " ".join(result["limitations"])

    def test_entry_one_second_after_open_is_partial(self):
        ts = dt.datetime(2026, 6, 2, 9, 15, 1, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exit_one_second_before_close_is_partial(self):
        ts = dt.datetime(2026, 6, 2, 15, 29, 59, tzinfo=IST)
        result = classify_exit_boundary("IN", ts)
        assert result["policy"] == EXIT_BAR_PARTIAL_UNKNOWN

    def test_exit_one_second_after_close_is_characterized_as_included_full(self):
        """CHARACTERIZATION, not approved policy: the exit side has no
        distinct 'after close' branch symmetric to the entry side's
        after-close-shifts-to-next-session handling (session_boundary.py
        classify_exit_boundary's final branch is reached by both
        exactly-at-close and any-time-after-close). This test locks in
        the CURRENT behavior for regression visibility; it does not
        assert this is correct."""
        ts = dt.datetime(2026, 6, 2, 15, 30, 1, tzinfo=IST)
        result = classify_exit_boundary("IN", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL  # CHARACTERIZATION

    def test_friday_to_monday_traversal_skips_weekend(self):
        friday = dt.date(2026, 6, 5)
        assert next_trading_session("IN", friday) == dt.date(2026, 6, 8)  # Monday
        assert previous_trading_session("IN", dt.date(2026, 6, 8)) == friday

    def test_confirmed_repository_holiday_is_partial_unknown(self):
        assert is_trading_day("IN", IN_HOLIDAY) is False
        ts = dt.datetime(2026, 1, 15, 12, 0, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "non-trading day" in " ".join(result["limitations"])

    def test_utc_instant_equivalent_to_0915_ist_matches_local_result(self):
        # 09:15 IST == 03:45 UTC (IST is UTC+5:30, fixed offset, no DST).
        utc_ts = dt.datetime(2026, 6, 2, 3, 45, tzinfo=dt.timezone.utc)
        local_ts = dt.datetime(2026, 6, 2, 9, 15, tzinfo=IST)
        assert classify_entry_boundary("IN", utc_ts) == classify_entry_boundary("IN", local_ts)

    def test_same_day_full_session_trade(self):
        entry = dt.datetime(2026, 6, 2, 9, 15, tzinfo=IST)
        exit_ts = dt.datetime(2026, 6, 2, 15, 30, tzinfo=IST)
        result = classify_same_day_trade("IN", entry, exit_ts)
        assert result["resolution"] == SAME_DAY_FULL_SESSION

    def test_same_day_partial_session_trade_is_ambiguous(self):
        entry = dt.datetime(2026, 6, 2, 10, 0, tzinfo=IST)
        exit_ts = dt.datetime(2026, 6, 2, 14, 0, tzinfo=IST)
        result = classify_same_day_trade("IN", entry, exit_ts)
        assert result["resolution"] == AMBIGUOUS_RESOLUTION


@pytest.mark.unit
class TestUSBoundaryMatrixStageJ3B1:
    """Stage J3B.1, Stage 3 -- strengthened US boundary matrix, same
    structure as the India class above."""

    def test_entry_one_second_before_open_is_partial(self):
        ts = dt.datetime(2026, 6, 2, 9, 29, 59, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "before official session open" in " ".join(result["limitations"])

    def test_entry_one_second_after_open_is_partial(self):
        ts = dt.datetime(2026, 6, 2, 9, 30, 1, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN

    def test_exit_one_second_before_close_is_partial(self):
        ts = dt.datetime(2026, 6, 2, 15, 59, 59, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] == EXIT_BAR_PARTIAL_UNKNOWN

    def test_exit_one_second_after_close_is_characterized_as_included_full(self):
        """CHARACTERIZATION, not approved policy -- see the India
        equivalent test's docstring above; identical mechanism."""
        ts = dt.datetime(2026, 6, 2, 16, 0, 1, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] == EXIT_BAR_INCLUDED_FULL  # CHARACTERIZATION

    def test_weekend_traversal_skips_saturday_sunday(self):
        friday = dt.date(2026, 6, 5)
        assert next_trading_session("US", friday) == dt.date(2026, 6, 8)
        assert previous_trading_session("US", dt.date(2026, 6, 8)) == friday

    def test_confirmed_repository_holiday(self):
        assert is_trading_day("US", US_HOLIDAY) is False

    def test_utc_instant_equivalent_exact_boundary_matches_local_result(self):
        # 2026-06-02 is EDT (UTC-4): 09:30 ET == 13:30 UTC.
        utc_ts = dt.datetime(2026, 6, 2, 13, 30, tzinfo=dt.timezone.utc)
        local_ts = dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        assert classify_entry_boundary("US", utc_ts) == classify_entry_boundary("US", local_ts)

    def test_same_day_full_and_partial_session(self):
        full = classify_same_day_trade(
            "US", dt.datetime(2026, 6, 2, 9, 30, tzinfo=ET), dt.datetime(2026, 6, 2, 16, 0, tzinfo=ET),
        )
        assert full["resolution"] == SAME_DAY_FULL_SESSION
        partial = classify_same_day_trade(
            "US", dt.datetime(2026, 6, 2, 10, 0, tzinfo=ET), dt.datetime(2026, 6, 2, 14, 0, tzinfo=ET),
        )
        assert partial["resolution"] == AMBIGUOUS_RESOLUTION


@pytest.mark.unit
class TestDSTAssuranceStageJ3B1:
    """Stage J3B.1, Stage 4 -- proves 09:30 ET session open still resolves
    correctly on the trading sessions immediately adjacent to a DST
    transition, with UTC offsets that genuinely differ as ZoneInfo
    determines (not a hardcoded assumption). The transition date itself
    is never used as a session-boundary test since the US market is
    closed on the Sundays both 2026 transitions fall on."""

    def test_final_trading_session_before_spring_transition_still_930_local(self):
        pre = previous_trading_session("US", DST_SPRING_TRANSITION)
        assert is_trading_day("US", pre)
        ts = dt.datetime(pre.year, pre.month, pre.day, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        assert ts.utcoffset() == dt.timedelta(hours=-5)  # still EST

    def test_first_trading_session_after_spring_transition_still_930_local(self):
        post = next_trading_session("US", DST_SPRING_TRANSITION)
        assert is_trading_day("US", post)
        ts = dt.datetime(post.year, post.month, post.day, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        assert ts.utcoffset() == dt.timedelta(hours=-4)  # now EDT

    def test_final_trading_session_before_autumn_transition_still_930_local(self):
        pre = previous_trading_session("US", DST_AUTUMN_TRANSITION)
        assert is_trading_day("US", pre)
        ts = dt.datetime(pre.year, pre.month, pre.day, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        assert ts.utcoffset() == dt.timedelta(hours=-4)  # still EDT

    def test_first_trading_session_after_autumn_transition_still_930_local(self):
        post = next_trading_session("US", DST_AUTUMN_TRANSITION)
        assert is_trading_day("US", post)
        ts = dt.datetime(post.year, post.month, post.day, 9, 30, tzinfo=ET)
        result = classify_entry_boundary("US", ts)
        assert result["policy"] == ENTRY_BAR_INCLUDED_FULL
        assert ts.utcoffset() == dt.timedelta(hours=-5)  # now EST

    def test_pure_fold_zero_and_fold_one_are_distinct_utc_instants(self):
        """PURE TIMEZONE CHARACTERIZATION -- NOT a market-session test.
        The US autumn fall-back hour (01:00-02:00 local) is locally
        ambiguous; fold=0 (first pass, still DST) and fold=1 (second
        pass, now standard time) must resolve to two different UTC
        instants. No production code in this repository currently
        constructs naive same-day-ambiguous times, so this does not by
        itself justify any production-code change -- it only proves the
        underlying stdlib/tzdata behavior this codebase would rely on if
        it ever did."""
        naive_ambiguous = dt.datetime(DST_AUTUMN_TRANSITION.year, DST_AUTUMN_TRANSITION.month, DST_AUTUMN_TRANSITION.day, 1, 30)
        first_pass = naive_ambiguous.replace(tzinfo=ET, fold=0)
        second_pass = naive_ambiguous.replace(tzinfo=ET, fold=1)
        assert first_pass.utcoffset() != second_pass.utcoffset()
        assert first_pass.astimezone(dt.timezone.utc) != second_pass.astimezone(dt.timezone.utc)


@pytest.mark.unit
class TestEarlyCloseKnownLimitationStageJ3B1:
    """Stage J3B.1, Stage 5 -- proves ONLY the actual current contract:
    resolve_session always uses the standard close regardless of any
    real early-close calendar, and every resolution discloses this via
    EARLY_CLOSE_UNSUPPORTED. Does NOT claim early-close handling exists
    and does NOT hardcode a real early-close date (none is present as an
    authoritative deterministic repository fixture)."""

    def test_standard_close_used_regardless_of_real_early_close_kbown_limitation(self):
        session = resolve_session("US", US_TRADING_DAY)
        assert session["close"] == dt.datetime(
            US_TRADING_DAY.year, US_TRADING_DAY.month, US_TRADING_DAY.day, 16, 0, tzinfo=ET,
        )
        assert EARLY_CLOSE_UNSUPPORTED in session["limitations"]

    def test_hypothetical_early_close_afternoon_time_evaluated_under_standard_model(self):
        """KNOWN_LIMITATION -- a time after a real (unmodeled) early
        close but before the assumed 16:00 standard close is classified
        as ordinary intraday (PARTIAL_UNKNOWN), not as after-close. This
        is the documented, conservative gap from session_boundary.py's
        own module docstring -- not a claim that early close is
        supported."""
        ts = dt.datetime(US_TRADING_DAY.year, US_TRADING_DAY.month, US_TRADING_DAY.day, 14, 0, tzinfo=ET)
        result = classify_exit_boundary("US", ts)
        assert result["policy"] == EXIT_BAR_PARTIAL_UNKNOWN  # KNOWN_LIMITATION, not EARLY_CLOSE_SUPPORTED


@pytest.mark.unit
class TestNaiveTimestampCharacterizationStageJ3B1:
    """Stage J3B.1, Stage 6A/6B/6C -- characterizes naive-timestamp
    handling across the session_boundary layer and the acquisition
    wrapper that calls it."""

    def test_session_boundary_rejects_naive_entry_timestamp(self):
        with pytest.raises(SessionBoundaryError):
            classify_entry_boundary("IN", dt.datetime(2026, 6, 2, 9, 15))

    def test_session_boundary_rejects_naive_exit_timestamp(self):
        with pytest.raises(SessionBoundaryError):
            classify_exit_boundary("US", dt.datetime(2026, 6, 2, 16, 0))

    def test_acquisition_wrapper_swallows_session_boundary_error_to_partial_unknown(self):
        """Stage 6B -- _resolve_boundary_policies catches
        SessionBoundaryError and returns PARTIAL_UNKNOWN for both sides
        rather than propagating a typed failure. This is a
        characterization of current behavior, not an endorsement: a
        caller cannot distinguish 'genuinely ambiguous mid-session
        timestamp' from 'input was rejected as naive' from the returned
        policy alone."""
        from services.postmortem.price_path_acquisition import _resolve_boundary_policies

        limitations: list[str] = []
        entry_policy, exit_policy = _resolve_boundary_policies(
            "US", dt.datetime(2026, 6, 2, 9, 30), dt.datetime(2026, 6, 2, 16, 0), limitations,
        )
        assert entry_policy == ENTRY_BAR_PARTIAL_UNKNOWN
        assert exit_policy == EXIT_BAR_PARTIAL_UNKNOWN
        # No naive-timestamp-specific limitation string is appended --
        # the SessionBoundaryError is caught before any limitations from
        # session_boundary are ever produced for this call.
        assert limitations == []

    @pytest.mark.skipif(
        not hasattr(time_module, "tzset"), reason="time.tzset() is unavailable on this platform (e.g. native Windows)",
    )
    def test_naive_timestamp_fetch_window_depends_on_host_tz_before_final_rejection(self):
        """Stage 6C -- demonstrates deterministically whether
        acquire_price_path_evidence's date-window computation
        (entry_timestamp.astimezone(market_tzinfo), price_path_acquisition.py
        lines 965-966) is sensitive to the HOST PROCESS timezone when
        given a naive timestamp. Controls TZ via time.tzset(), restores
        it in finally, and never leaves the process timezone modified
        after the test.

        CONFIRMED, PRECISE CHARACTERIZATION (refines the Stage J3B
        investigation's naive-timestamp finding): the final
        PricePathEvidenceBundle DOES reject a naive entry/exit_timestamp
        in __post_init__ (price_path_evidence.py), so acquisition
        ultimately fails closed rather than silently persisting
        wrong-window evidence. BUT the entry_date/exit_date used to call
        fetch_bars_fn (i.e. the actual provider request window, were this
        real yfinance rather than a fake) is computed via naive
        `.astimezone(market_tzinfo)` BEFORE that rejection, and genuinely
        differs by host TZ -- so a real provider call would already have
        gone out with a host-TZ-dependent, incorrect window before the
        request is ultimately discarded."""
        from services.market_hours import IST
        from services.postmortem.price_path_acquisition import acquire_price_path_evidence
        from services.postmortem.price_path_evidence import PricePathEvidenceError

        original_tz = os.environ.get("TZ")
        naive_ts = dt.datetime(2026, 6, 2, 23, 30)  # 23:30, date-sensitive across host TZs
        captured_windows = []

        def _capturing_bars(provider_symbol, start, end):
            captured_windows.append((start, end))
            return []

        def _fake_bars(*a, **k):
            return []

        try:
            os.environ["TZ"] = "UTC"
            time_module.tzset()
            with pytest.raises(PricePathEvidenceError):
                acquire_price_path_evidence(
                    paper_trade_id=1, user_id="u", symbol="RELIANCE", market="IN",
                    market_timezone_name="Asia/Kolkata", market_tzinfo=IST,
                    entry_timestamp=naive_ts, exit_timestamp=naive_ts,
                    fetch_bars_fn=_capturing_bars, fetch_splits_fn=_fake_bars, fetch_dividends_fn=_fake_bars,
                )

            os.environ["TZ"] = "Pacific/Kiritimati"  # UTC+14, maximally distant from UTC
            time_module.tzset()
            with pytest.raises(PricePathEvidenceError):
                acquire_price_path_evidence(
                    paper_trade_id=1, user_id="u", symbol="RELIANCE", market="IN",
                    market_timezone_name="Asia/Kolkata", market_tzinfo=IST,
                    entry_timestamp=naive_ts, exit_timestamp=naive_ts,
                    fetch_bars_fn=_capturing_bars, fetch_splits_fn=_fake_bars, fetch_dividends_fn=_fake_bars,
                )
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time_module.tzset()

        assert len(captured_windows) == 2
        # Same naive input, different host TZ -> different provider
        # request window -- confirmed at the fetch-window layer, even
        # though the bundle itself is ultimately rejected both times.
        assert captured_windows[0] != captured_windows[1]


@pytest.mark.unit
class TestMuhuratSpecialSessionInventoryStageJ3B1:
    """Stage J3B.1, Stage 9 -- reads the actual MUHURAT_SESSIONS data and
    its real consumer (services.market_hours.is_market_open) to prove
    session_boundary/price-path classification does NOT consult it, so
    the current Muhurat handling is limited to the live 'is market open
    right now' check only. Uses the ONE deterministic repository entry
    already present (2026-11-08) -- does not invent or search for a
    future date."""

    def test_muhurat_sessions_data_has_at_least_one_deterministic_entry(self):
        from services.market_hours import MUHURAT_SESSIONS
        assert len(MUHURAT_SESSIONS) >= 1
        assert MUHURAT_SESSIONS[0]["date"] == "2026-11-08"

    def test_session_boundary_module_never_references_muhurat_sessions(self):
        import inspect
        from services.postmortem import session_boundary
        source = inspect.getsource(session_boundary)
        assert "MUHURAT" not in source

    def test_muhurat_date_is_classified_by_ordinary_holiday_rules_not_muhurat_awareness(self):
        """The 2026-11-08 Muhurat date is itself a Sunday (not a
        weekday-holiday-calendar entry at all in this repository), so
        price-path classification treats it as an ordinary non-trading
        weekend day -- conservatively PARTIAL_UNKNOWN -- with no
        awareness that a real, brief Muhurat trading session occurs on
        it. This characterizes the current blind spot: neither the
        weekday check nor the holiday calendar nor session_boundary
        distinguishes 'Muhurat session in progress' from 'ordinary
        non-trading day'."""
        from services.market_hours import MUHURAT_SESSIONS

        muhurat_date = dt.date.fromisoformat(MUHURAT_SESSIONS[0]["date"])
        assert muhurat_date.weekday() == 6  # Sunday, per the current repository fixture
        assert is_trading_day("IN", muhurat_date) is False
        ts = dt.datetime(muhurat_date.year, muhurat_date.month, muhurat_date.day, 18, 30, tzinfo=IST)
        result = classify_entry_boundary("IN", ts)
        assert result["policy"] == ENTRY_BAR_PARTIAL_UNKNOWN
        assert "non-trading day" in " ".join(result["limitations"])
