"""
Trade Postmortem Sprint 3A, Stage J3B.1 -- India/US session-boundary and
DST assurance, proven through the REAL /generate endpoint against a real
PostgreSQL instance.

TEST-ONLY. Characterizes existing behavior established by the Stage J3B
read-only investigation; does not change production semantics. Uses
explicit timestamp overrides (_set_trade_window), not uncontrolled
wall-clock trade times, and deterministic provider fixtures -- never
live network calls.
"""
import datetime as dt

import pytest

from tests.postgres_integration.conftest import make_auth_header

pytestmark = pytest.mark.postgres_integration


def _fake_none(*a, **k):
    return []


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 110.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _generate(client, user_id, trade_id):
    return client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(user_id))


def _open_and_close_market(client, pg_conn, user_id, *, market, symbol, exit_price):
    from tests.postgres_integration.conftest import ensure_portfolio
    ensure_portfolio(pg_conn, user_id, cash=100_000_000.0, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id, symbol=symbol, market=market, price=100.0).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


def _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts):
    pg_conn.execute(
        "UPDATE paper_trades SET opened_at = %s, closed_at = %s WHERE id = %s",
        (entry_ts, exit_ts, trade_id),
    )


def _evidence_row(pg_conn, trade_id):
    row = pg_conn.execute(
        "SELECT market, provider_symbol, market_timezone, entry_bar_policy, exit_bar_policy, "
        "limitations FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s",
        (trade_id,),
    ).fetchone()
    return row


def _find_dst_transitions(year: int):
    """Same pure-stdlib computation as
    tests/unit/test_session_boundary.py's module-level helper --
    independently derived here (not imported) so a future refactor of
    one file cannot silently desync the other's transition dates
    without both test suites failing loudly."""
    from services.market_hours import ET

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


@pytest.fixture(autouse=True)
def _enable_price_path_flag(monkeypatch):
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")


@pytest.mark.timeout(30)
class TestIndiaRealPGBoundaryProof:
    """Stage J3B.1, Stage 7."""

    def test_india_exact_full_session_boundary(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.market_hours import IST
        from services.postmortem import price_path_acquisition

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)

        entry_ts = dt.datetime(2026, 6, 2, 9, 15, 0, tzinfo=IST)
        exit_ts = dt.datetime(2026, 6, 2, 15, 30, 0, tzinfo=IST)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        def _bars(*a, **k):
            return [{"date": dt.date(2026, 6, 2), "open": 2800.0, "high": 2850.0, "low": 2780.0, "close": 2820.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        market, provider_symbol, market_timezone, entry_bar_policy, exit_bar_policy, _limitations = _evidence_row(pg_conn, trade_id)
        assert market == "IN"
        assert provider_symbol == "RELIANCE.NS"
        assert market_timezone == "Asia/Kolkata"
        assert entry_bar_policy == "ENTRY_BAR_INCLUDED_FULL"
        assert exit_bar_policy == "EXIT_BAR_INCLUDED_FULL"

        from services.postmortem.price_path_acquisition import verify_source_manifest_integrity
        manifest = pg_conn.execute(
            "SELECT source_manifest FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert verify_source_manifest_integrity(manifest) is True

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 1

    def test_india_partial_session_one_second_after_open(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.market_hours import IST
        from services.postmortem import price_path_acquisition

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)

        entry_ts = dt.datetime(2026, 6, 2, 9, 15, 1, tzinfo=IST)
        exit_ts = dt.datetime(2026, 6, 2, 15, 29, 59, tzinfo=IST)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        def _bars(*a, **k):
            return [{"date": dt.date(2026, 6, 2), "open": 2800.0, "high": 2850.0, "low": 2780.0, "close": 2820.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        _market, _provider_symbol, _tz, entry_bar_policy, exit_bar_policy, limitations = _evidence_row(pg_conn, trade_id)
        assert entry_bar_policy == "ENTRY_BAR_PARTIAL_UNKNOWN"
        assert exit_bar_policy == "EXIT_BAR_PARTIAL_UNKNOWN"
        # No fabricated full-boundary claim -- the standing early-close
        # disclosure limitation must still be present (not a boundary
        # violation itself, but confirms limitations are not silently
        # dropped for a partial-session trade).
        from services.postmortem.session_boundary import EARLY_CLOSE_UNSUPPORTED
        assert EARLY_CLOSE_UNSUPPORTED in limitations

    def test_india_holiday_characterization(self, client, pg_conn, unique_user_id, monkeypatch):
        """Stage 7C -- 2026-01-15 is a confirmed NSE_EXTRA_HOLIDAYS
        repository fixture (no live lookup)."""
        from services.market_hours import IST, NSE_EXTRA_HOLIDAYS
        from services.postmortem import price_path_acquisition

        holiday_date = dt.date(2026, 1, 15)
        assert holiday_date.strftime("%Y-%m-%d") in NSE_EXTRA_HOLIDAYS

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)

        entry_ts = dt.datetime(2026, 1, 15, 12, 0, 0, tzinfo=IST)
        exit_ts = dt.datetime(2026, 1, 15, 12, 30, 0, tzinfo=IST)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        def _bars(*a, **k):
            return []  # no real session on a holiday -- no bars available

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200  # no crash

        _market, _provider_symbol, _tz, entry_bar_policy, _exit_bar_policy, limitations = _evidence_row(pg_conn, trade_id)
        assert entry_bar_policy == "ENTRY_BAR_PARTIAL_UNKNOWN"
        assert any("non-trading day" in lim for lim in limitations)


@pytest.mark.timeout(30)
class TestUSRealPGBoundaryProof:
    """Stage J3B.1, Stage 8."""

    def test_us_exact_full_session_boundary(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        entry_ts = dt.datetime(2026, 6, 2, 9, 30, 0, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 16, 0, 0, tzinfo=ET)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        received_symbols = []

        def _bars(provider_symbol, *a, **k):
            received_symbols.append(provider_symbol)
            return [{"date": dt.date(2026, 6, 2), "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"
        assert received_symbols == ["AAPL"]  # no suffix for US

        market, provider_symbol, market_timezone, entry_bar_policy, exit_bar_policy, _limitations = _evidence_row(pg_conn, trade_id)
        assert market == "US"
        assert provider_symbol == "AAPL"
        assert market_timezone == "America/New_York"
        assert entry_bar_policy == "ENTRY_BAR_INCLUDED_FULL"
        assert exit_bar_policy == "EXIT_BAR_INCLUDED_FULL"

    def test_us_partial_session_one_second_after_open_and_before_close(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        entry_ts = dt.datetime(2026, 6, 2, 9, 30, 1, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 2, 15, 59, 59, tzinfo=ET)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        def _bars(*a, **k):
            return [{"date": dt.date(2026, 6, 2), "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        _market, _provider_symbol, _tz, entry_bar_policy, exit_bar_policy, _limitations = _evidence_row(pg_conn, trade_id)
        assert entry_bar_policy == "ENTRY_BAR_PARTIAL_UNKNOWN"
        assert exit_bar_policy == "EXIT_BAR_PARTIAL_UNKNOWN"

    @pytest.mark.parametrize("transition_name", ["spring", "autumn"])
    def test_us_dst_adjacent_trading_sessions(self, client, pg_conn, unique_user_id, monkeypatch, transition_name):
        """Stage 8C -- uses the last trading session before and first
        trading session after the given 2026 DST transition, determined
        programmatically via zoneinfo (never the transition Sunday
        itself, since the US market is closed that day)."""
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition
        from services.postmortem.session_boundary import next_trading_session, previous_trading_session

        spring, autumn = _find_dst_transitions(2026)
        transition_date = spring if transition_name == "spring" else autumn
        pre = previous_trading_session("US", transition_date)
        post = next_trading_session("US", transition_date)

        for label, session_date in (("pre", pre), ("post", post)):
            # Re-assert the flag OFF before every _open_and_close_market
            # call, not just once before the loop -- otherwise the prior
            # iteration's setenv("...", "1") (still in effect on this
            # shared, function-scoped monkeypatch) leaks into this
            # iteration's buy/sell and lets the close-service's own
            # auto-generation-on-close fire using the STALE fetch mock,
            # so this trade's report already exists before our explicit
            # _generate() call below -- observed as a real CI failure
            # (PRICE_PATH_ALREADY_COMPLETE instead of PRICE_PATH_GENERATED)
            # the first time this loop omitted the per-iteration delenv.
            monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
            trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)
            entry_ts = dt.datetime(session_date.year, session_date.month, session_date.day, 9, 30, 0, tzinfo=ET)
            exit_ts = dt.datetime(session_date.year, session_date.month, session_date.day, 16, 0, 0, tzinfo=ET)
            _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

            def _make_bars(_d):
                def _bars(*a, **k):
                    return [{"date": _d, "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                              "volume": 500, "adj_close": None, "dividend": 0.0}]
                return _bars

            _bars = _make_bars(session_date)

            monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
            monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
            monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)
            monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

            resp = _generate(client, unique_user_id, trade_id)
            assert resp.status_code == 200, f"{label} session {session_date} failed: {resp.text}"
            assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED", label

            market, _provider_symbol, market_timezone, entry_bar_policy, exit_bar_policy, _limitations = _evidence_row(pg_conn, trade_id)
            assert market == "US"
            assert market_timezone == "America/New_York"
            assert entry_bar_policy == "ENTRY_BAR_INCLUDED_FULL", label
            assert exit_bar_policy == "EXIT_BAR_INCLUDED_FULL", label

            # UTC offset genuinely differs across the transition -- proves
            # ZoneInfo, not a fixed offset, resolved this session.
            expected_offset = dt.timedelta(hours=-5) if entry_ts.utcoffset() == dt.timedelta(hours=-5) else dt.timedelta(hours=-4)
            assert entry_ts.utcoffset() == expected_offset


@pytest.mark.timeout(30)
class TestPostgresTimestampAwarenessInvariant:
    """Stage J3B.1, Stage 6D -- distinguishes 'confirmed defect in the
    reusable acquisition API' from 'proven production endpoint
    reachability': does the real paper-trading endpoint + PostgreSQL
    driver ever hand back a NAIVE opened_at/closed_at? If this invariant
    holds, the naive-timestamp gap characterized in Stage 6A-6C is
    confirmed to live only in the reusable acquisition API, never
    reachable through the real endpoint today."""

    def test_persisted_paper_trade_timestamps_are_always_timezone_aware(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        opened_at, closed_at = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        assert opened_at.tzinfo is not None
        assert closed_at.tzinfo is not None


@pytest.mark.timeout(30)
class TestForcedNaiveTimestampRealPGAssurance:
    """Stage J3B.2, Stage 6 -- real-PostgreSQL assurance for the pre-I/O
    naive-timestamp guard, using the same test-only forced-naive
    GenerationContext approach as the fake-conn regression test in
    tests/regression/test_paper_trading_price_path_lease_lifecycle.py.
    The real, persisted paper_trades.opened_at/closed_at are never
    altered -- only load_generation_context's own RETURNED
    GenerationContext is monkeypatched, simulating a future internal
    caller violating the timezone-aware contract."""

    def test_forced_naive_entry_timestamp_rejected_before_provider_io(self, client, pg_conn, unique_user_id, monkeypatch):
        import dataclasses
        from unittest.mock import patch

        from services.postmortem import price_path_generation

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        opened_at, closed_at = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        assert opened_at.tzinfo is not None and closed_at.tzinfo is not None  # genuinely aware, unaltered

        def _raises(*a, **k):
            raise AssertionError("provider must not be called when the pre-I/O guard rejects a naive timestamp")

        real_loader = price_path_generation.load_generation_context

        def _forced_naive_loader(*args, **kwargs):
            ctx = real_loader(*args, **kwargs)
            return dataclasses.replace(ctx, entry_timestamp=ctx.entry_timestamp.replace(tzinfo=None))

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        with patch.object(price_path_generation, "load_generation_context", side_effect=_forced_naive_loader):
            with patch(
                "services.postmortem.price_path_acquisition.fetch_raw_daily_bars", side_effect=_raises,
            ), patch(
                "services.postmortem.price_path_acquisition.fetch_split_events", side_effect=_raises,
            ), patch(
                "services.postmortem.price_path_acquisition.fetch_dividend_events", side_effect=_raises,
            ):
                resp = _generate(client, unique_user_id, trade_id)

        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 0

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "PRICE_PATH_GENERATION_ERROR"  # sanitized, never the raw naive timestamp

        # The real, persisted trade row remains genuinely unchanged.
        opened_at_after, closed_at_after = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        assert opened_at_after == opened_at
        assert closed_at_after == closed_at
        assert opened_at_after.tzinfo is not None and closed_at_after.tzinfo is not None

        # No transaction left open, no duplicate outbox row on a repeat
        # request -- follows the existing retry/backoff contract (a
        # retry before backoff elapses reports IN_PROGRESS-equivalent
        # settlement, never a second outbox row for the same
        # trade/schema/calc/rules identity).
        outbox_row_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_row_count == 1

        with patch.object(price_path_generation, "load_generation_context", side_effect=_forced_naive_loader):
            with patch(
                "services.postmortem.price_path_acquisition.fetch_raw_daily_bars", side_effect=_raises,
            ), patch(
                "services.postmortem.price_path_acquisition.fetch_split_events", side_effect=_raises,
            ), patch(
                "services.postmortem.price_path_acquisition.fetch_dividend_events", side_effect=_raises,
            ):
                resp2 = _generate(client, unique_user_id, trade_id)
        assert resp2.status_code == 200

        outbox_row_count_after_retry = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_row_count_after_retry == 1  # still exactly one outbox row -- no duplicate
