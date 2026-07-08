"""
US-aware Telegram notifications for Daily Picks.

Root cause being locked in: US Daily Picks never produced a Telegram
message because daily_picks.py gated the send on
`_post_success_market == "IN"` and telegram_bot.py hardcoded 🇮🇳/₹.

These tests exercise send_picks_to_telegram with requests.post
monkeypatched — NO real Telegram request is ever made — and lock the
market-aware call in generate_picks via a source-level regression check
(running the full generation job in a unit test is neither possible nor
allowed: no network, no generation endpoints).
"""
import pytest

from services import telegram_bot


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, text="ok"):
        self.ok = ok
        self.status_code = status_code
        self.text = text


@pytest.fixture
def telegram_env(monkeypatch):
    """Configured-bot environment plus a captured, never-sent request."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)
    return sent


def _picks(price=100.0, target=120.0):
    return {
        "short": [{"symbol": "AAA", "price": price, "target": target, "confidence": 72}],
        "medium": [],
        "long": [{"symbol": "BBB", "price": price, "target": target, "confidence": 61}],
    }


@pytest.mark.unit
class TestMarketFormatting:
    def test_in_message_uses_indian_flag_and_rupee(self, telegram_env):
        assert telegram_bot.send_picks_to_telegram(_picks(), market="IN") is True
        text = telegram_env[0]["json"]["text"]
        assert "🇮🇳" in text
        assert "₹" in text
        assert "🇺🇸" not in text
        assert "$" not in text

    def test_us_message_uses_us_flag_and_dollar(self, telegram_env):
        assert telegram_bot.send_picks_to_telegram(_picks(), market="US") is True
        text = telegram_env[0]["json"]["text"]
        assert "🇺🇸" in text
        assert "$" in text
        assert "🇮🇳" not in text
        assert "₹" not in text

    def test_default_market_is_in_for_backward_compatibility(self, telegram_env):
        assert telegram_bot.send_picks_to_telegram(_picks()) is True
        assert "🇮🇳" in telegram_env[0]["json"]["text"]

    def test_market_value_is_normalized(self, telegram_env):
        assert telegram_bot.send_picks_to_telegram(_picks(), market=" us ") is True
        assert "🇺🇸" in telegram_env[0]["json"]["text"]

    def test_unknown_market_skips_rather_than_guessing_a_currency(self, telegram_env):
        assert telegram_bot.send_picks_to_telegram(_picks(), market="CRYPTO") is False
        assert telegram_env == []  # nothing sent

    def test_wording_and_disclaimer_preserved(self, telegram_env):
        telegram_bot.send_picks_to_telegram(_picks(), market="US")
        text = telegram_env[0]["json"]["text"]
        assert "*StockSense360 Daily Picks* — Top BUY Calls" in text
        assert "_AI-generated signals. Not financial advice. Do your own research._" in text
        assert "⚡ Short Term (1–10 days)" in text
        assert "↑20.0%" in text          # upside math unchanged
        assert "(72% confidence)" in text

    def test_message_content_identical_between_markets_except_flag_and_currency(self, telegram_env):
        telegram_bot.send_picks_to_telegram(_picks(), market="IN")
        telegram_bot.send_picks_to_telegram(_picks(), market="US")
        in_text, us_text = (s["json"]["text"] for s in telegram_env)
        assert in_text.replace("🇮🇳", "🇺🇸").replace("₹", "$") == us_text


@pytest.mark.unit
class TestSafeSkips:
    def test_missing_env_vars_skip_safely_without_sending(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        called = []
        monkeypatch.setattr(telegram_bot.requests, "post",
                            lambda *a, **k: called.append(1) or _FakeResponse())
        assert telegram_bot.send_picks_to_telegram(_picks(), market="US") is False
        assert called == []

    def test_failed_send_returns_false(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        monkeypatch.setattr(telegram_bot.requests, "post",
                            lambda *a, **k: _FakeResponse(ok=False, status_code=400, text="bad"))
        assert telegram_bot.send_picks_to_telegram(_picks(), market="IN") is False


@pytest.mark.regression
class TestDailyPicksCallsTelegramForBothMarkets:
    """Source-level lock on generate_picks' post-success block: the IN-only
    gate must stay gone and the call must pass the run's own market. The
    full job cannot run in a unit test (network/generation are off-limits),
    so this guards the exact regression shape instead."""

    def test_no_in_only_telegram_gate_and_market_is_forwarded(self):
        import inspect
        from services import daily_picks
        src = inspect.getsource(daily_picks.generate_picks)
        assert 'send_picks_to_telegram(payload.get("picks", {}), market=_post_success_market)' in src
        # The old gate — telegram only when the market was IN — must not return.
        telegram_call_pos = src.index("send_picks_to_telegram")
        preceding = src[max(0, telegram_call_pos - 400):telegram_call_pos]
        assert '_post_success_market == "IN"' not in preceding

    def test_telegram_call_remains_exception_isolated(self):
        import inspect
        from services import daily_picks
        src = inspect.getsource(daily_picks.generate_picks)
        call_pos = src.index("send_picks_to_telegram(payload")
        # The call must sit inside a try whose except swallows into a log —
        # Telegram failure can never alter the job's terminal status.
        window = src[max(0, call_pos - 200):call_pos + 300]
        assert "try:" in window
        assert 'log.warning(f"[telegram] Error: {exc}")' in window
