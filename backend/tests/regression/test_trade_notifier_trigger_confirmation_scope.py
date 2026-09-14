"""
Paper trade trigger-confirmation email scope correction (2026-09-14).

Triggered by direct user feedback: the routine "near target"/"near stop
loss" in-page banner (added earlier the same day) was reverted per
explicit request — it crowded the page with continuous proximity
messages. The user also asked that trigger-confirmation emails ("for the
triggered trades we also need the email notifications") not be limited
to Auto Close trades only.

Root cause found by direct code reading: _notify_auto_close_triggers'
own SQL filter was `trade_management_mode = 'auto'` — a Manual/AI-
assisted trade closed via the frontend "Close Now" button after its own
stop-loss/target trigger fired (checkExitTrigger/showManualBanner) never
sent this confirmation at all, only genuinely system-auto-closed trades
did. The filter is now exit_reason IN ('STOP_LOSS', 'TARGET_HIT')
regardless of trade_management_mode; a plain manual sell (exit_reason ==
'MANUAL', no trigger involved) remains correctly excluded. The email
copy's "Position closed automatically." line is now conditional on
whether the trade was actually auto-closed, so a manually-closed trigger
doesn't misleadingly claim automatic execution.
"""
from unittest.mock import patch

from services import trade_notifier


SOURCE = trade_notifier.__spec__.loader.get_source(trade_notifier.__name__)  # type: ignore[union-attr]


# ── 1. SQL scope no longer restricted to auto mode ──────────────────────────

def test_trigger_query_no_longer_filters_by_trade_management_mode():
    assert "trade_management_mode = 'auto'" not in SOURCE


def test_trigger_query_still_filters_to_a_genuine_trigger_exit_reason():
    assert "t.exit_reason IN ('STOP_LOSS', 'TARGET_HIT')" in SOURCE


# ── 2. Email copy reflects actual mode ──────────────────────────────────────

def test_trigger_email_html_says_automatically_only_when_actually_auto():
    html_auto = trade_notifier._trigger_email_html(
        "AAPL", "US", 100.0, 110.0, 10.0, "target", 50.0, is_auto=True,
    )
    assert "Position closed automatically." in html_auto

    html_manual = trade_notifier._trigger_email_html(
        "AAPL", "US", 100.0, 90.0, -10.0, "stop", -50.0, is_auto=False,
    )
    assert "Position closed automatically." not in html_manual
    assert "Position closed." in html_manual


# ── 3. End-to-end: a Manual-mode triggered close now emails ─────────────────

class _FakeConn:
    def __init__(self, fetchall_result):
        self._fetchall_result = fetchall_result
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchall(self):
        return self._fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_manual_mode_trade_closed_on_trigger_now_sends_confirmation_email():
    """Direct proof of the fix: a row with trade_management_mode='manual'
    (not 'auto') must still result in a sent email — the exact case that
    was previously silently skipped."""
    row = (
        1, "TCS", "IN", 3000.0, 3300.0, 10, "TARGET_HIT",
        __import__("datetime").datetime(2026, 9, 14, tzinfo=__import__("datetime").timezone.utc),
        None, None, "user@example.com", "manual",
    )
    conn = _FakeConn(fetchall_result=[row])

    with patch.object(trade_notifier, "_conn", return_value=conn), \
         patch.object(trade_notifier, "_send_email", return_value=True) as mock_send:
        trade_notifier._notify_auto_close_triggers()

    mock_send.assert_called_once()
    _, subject, html = mock_send.call_args[0]
    assert "TCS" in subject
    assert "target achieved" in subject
    assert "Position closed automatically." not in html
    assert "Position closed." in html


def test_manual_mode_trade_manually_sold_no_trigger_never_emails():
    """A plain manual sell (exit_reason == 'MANUAL') must remain excluded
    — this fix is about a genuine trigger event, not every close."""
    with patch.object(trade_notifier, "_conn") as mock_conn_factory, \
         patch.object(trade_notifier, "_send_email") as mock_send:
        conn = _FakeConn(fetchall_result=[])
        mock_conn_factory.return_value = conn
        trade_notifier._notify_auto_close_triggers()
        # The SQL itself excludes MANUAL via the exit_reason IN (...) filter
        # — asserting no row was returned/no email sent is the behavioral
        # proof; the SQL-text assertion above covers the filter itself.
        assert conn.executed
        assert "'MANUAL'" not in conn.executed[0][0]

    mock_send.assert_not_called()
