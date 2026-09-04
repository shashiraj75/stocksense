"""
2026-09 root-cause fix — Auto Close trigger checking used to be entirely
client-side (a trade only closed while a browser tab had it mounted and
polling). services.paper_trade_exit_monitor supplies the missing
server-side trigger, delegating the actual close to the SAME
close_paper_trade path the manual sell endpoint already uses.

check_exit_trigger is tested as a pure function (mirrors frontend's
checkExitTrigger exactly). run_exit_monitor_cycle is tested with the DB
connection, market-hours check, quote service, and close_paper_trade all
mocked — these tests verify the ORCHESTRATION (who gets checked, who gets
closed, error isolation), not close_paper_trade's own correctness (that
has its own dedicated test coverage elsewhere).
"""
from unittest.mock import MagicMock, patch

import pytest

from services.paper_trade_exit_monitor import check_exit_trigger, run_exit_monitor_cycle
from services.postmortem.close_service import TradeAlreadyClosedError, TradeNotOwnedError


# ---------------------------------------------------------------------------
# check_exit_trigger — pure function
# ---------------------------------------------------------------------------

def test_stop_loss_triggers_at_or_below_stop():
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=100.0) == "STOP_LOSS"
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=95.0) == "STOP_LOSS"


def test_target_triggers_at_or_above_target():
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=150.0) == "TARGET_HIT"
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=160.0) == "TARGET_HIT"


def test_no_trigger_between_stop_and_target():
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=125.0) is None


def test_stop_loss_checked_before_target_on_ambiguous_input():
    """A gap move that somehow satisfies both conditions at once (e.g. a
    malformed stop above target) must resolve to STOP_LOSS — matching the
    frontend's own checkExitTrigger ordering exactly."""
    assert check_exit_trigger(stop_loss=200.0, target_price=100.0, live_price=150.0) == "STOP_LOSS"


@pytest.mark.parametrize("bad_price", [None, 0, -5.0, float("nan")])
def test_invalid_live_price_never_triggers(bad_price):
    assert check_exit_trigger(stop_loss=100.0, target_price=150.0, live_price=bad_price) is None


def test_none_or_non_positive_levels_never_trigger():
    assert check_exit_trigger(stop_loss=None, target_price=None, live_price=50.0) is None
    assert check_exit_trigger(stop_loss=0, target_price=0, live_price=50.0) is None


def test_only_stop_loss_set_target_absent():
    assert check_exit_trigger(stop_loss=100.0, target_price=None, live_price=90.0) == "STOP_LOSS"
    assert check_exit_trigger(stop_loss=100.0, target_price=None, live_price=110.0) is None


def test_only_target_set_stop_absent():
    assert check_exit_trigger(stop_loss=None, target_price=150.0, live_price=160.0) == "TARGET_HIT"
    assert check_exit_trigger(stop_loss=None, target_price=150.0, live_price=140.0) is None


# ---------------------------------------------------------------------------
# run_exit_monitor_cycle — orchestration, everything else mocked
# ---------------------------------------------------------------------------

def _fake_conn_with_rows(rows):
    """A minimal fake matching the `with _conn() as conn: conn.execute(...).fetchall()` shape."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.fetchall.return_value = rows
    conn.transaction.return_value.__enter__.return_value = None
    conn.transaction.return_value.__exit__.return_value = False
    return conn


def test_no_open_markets_short_circuits_before_any_db_query():
    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=False), \
         patch("services.paper_trade_exit_monitor._conn") as mock_conn:
        summary = run_exit_monitor_cycle()
    assert summary == {"checked": 0, "closed": 0, "errors": 0}
    mock_conn.assert_not_called()


def test_no_eligible_trades_returns_zero_summary():
    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows([])):
        summary = run_exit_monitor_cycle()
    assert summary == {"checked": 0, "closed": 0, "errors": 0}


def test_triggered_trade_is_closed_via_close_paper_trade():
    row = (42, "user-1", "AAPL", "US", 100.0, 150.0)  # id, user_id, symbol, market, stop_loss, target_price
    fake_result = MagicMock(market="US", proceeds=9500.0)

    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows([row])), \
         patch("services.market_data.MarketDataService") as MockSvc, \
         patch("services.paper_trade_exit_monitor.close_paper_trade", return_value=fake_result) as mock_close:
        mock_svc_instance = MockSvc.return_value

        async def _fake_get_quote(symbol, market):
            return {"price": 95.0}  # below stop_loss=100.0

        mock_svc_instance.get_quote = _fake_get_quote
        summary = run_exit_monitor_cycle()

    assert summary == {"checked": 1, "closed": 1, "errors": 0}
    mock_close.assert_called_once()
    _, kwargs = mock_close.call_args
    assert kwargs["trade_id"] == 42
    assert kwargs["user_id"] == "user-1"
    assert kwargs["exit_price"] == 95.0
    assert kwargs["exit_mechanism"].value == "STOP_LOSS"


def test_untriggered_trade_is_checked_but_not_closed():
    row = (42, "user-1", "AAPL", "US", 100.0, 150.0)

    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows([row])), \
         patch("services.market_data.MarketDataService") as MockSvc, \
         patch("services.paper_trade_exit_monitor.close_paper_trade") as mock_close:
        mock_svc_instance = MockSvc.return_value

        async def _fake_get_quote(symbol, market):
            return {"price": 125.0}  # between stop and target

        mock_svc_instance.get_quote = _fake_get_quote
        summary = run_exit_monitor_cycle()

    assert summary == {"checked": 1, "closed": 0, "errors": 0}
    mock_close.assert_not_called()


def test_already_closed_race_is_benign_not_an_error():
    """A manual close winning the race must not be counted as a monitor
    error — TradeAlreadyClosedError is an expected, harmless outcome."""
    row = (42, "user-1", "AAPL", "US", 100.0, 150.0)

    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows([row])), \
         patch("services.market_data.MarketDataService") as MockSvc, \
         patch("services.paper_trade_exit_monitor.close_paper_trade",
               side_effect=TradeAlreadyClosedError(42)):
        mock_svc_instance = MockSvc.return_value

        async def _fake_get_quote(symbol, market):
            return {"price": 90.0}

        mock_svc_instance.get_quote = _fake_get_quote
        summary = run_exit_monitor_cycle()

    assert summary == {"checked": 1, "closed": 0, "errors": 0}


def test_close_service_error_is_counted_and_does_not_crash_the_cycle():
    rows = [
        (42, "user-1", "AAPL", "US", 100.0, 150.0),
        (43, "user-2", "MSFT", "US", 200.0, 300.0),
    ]

    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows(rows)), \
         patch("services.market_data.MarketDataService") as MockSvc, \
         patch("services.paper_trade_exit_monitor.close_paper_trade",
               side_effect=TradeNotOwnedError(42)) as mock_close:
        mock_svc_instance = MockSvc.return_value

        async def _fake_get_quote(symbol, market):
            return {"price": 90.0}  # triggers stop for both rows

        mock_svc_instance.get_quote = _fake_get_quote
        summary = run_exit_monitor_cycle()

    # Both trades triggered and both attempted a close; both rejected with
    # TradeNotOwnedError — the second trade's own attempt must still run
    # even though the first one raised.
    assert summary == {"checked": 2, "closed": 0, "errors": 2}
    assert mock_close.call_count == 2


def test_missing_or_zero_quote_price_never_triggers_a_close():
    row = (42, "user-1", "AAPL", "US", 100.0, 150.0)

    with patch("services.paper_trade_exit_monitor.is_market_open", return_value=True), \
         patch("services.paper_trade_exit_monitor._conn", return_value=_fake_conn_with_rows([row])), \
         patch("services.market_data.MarketDataService") as MockSvc, \
         patch("services.paper_trade_exit_monitor.close_paper_trade") as mock_close:
        mock_svc_instance = MockSvc.return_value

        async def _fake_get_quote(symbol, market):
            return None  # quote unavailable

        mock_svc_instance.get_quote = _fake_get_quote
        summary = run_exit_monitor_cycle()

    assert summary == {"checked": 1, "closed": 0, "errors": 0}
    mock_close.assert_not_called()
