"""
Unit tests for the TRADE_POSTMORTEM_DAILY_ENABLED feature flag — PR #32
pre-merge correction. Mirrors the exact fail-safe pattern already proven
by services/recommendation_consolidation_api_composer.py's
rci_live_stock_analysis_enabled and services/finnhub_client.py's
ENABLE_FINNHUB_FOR_IN: missing/empty/invalid resolves to disabled, only
an explicit accepted true value enables it.
"""
import pytest

from api.routers.paper_trading import _TRADE_POSTMORTEM_DAILY_ENV_VAR, _trade_postmortem_daily_enabled


@pytest.mark.parametrize("raw_value,expected", [
    ("1", True),
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("False", False),
    ("no", False),
    ("off", False),
    ("", False),
    ("  ", False),
    ("not-a-boolean", False),
    ("2", False),
    ("enabled", False),
    ("null", False),
])
def test_flag_value_parsing(monkeypatch, raw_value, expected):
    monkeypatch.setenv(_TRADE_POSTMORTEM_DAILY_ENV_VAR, raw_value)
    assert _trade_postmortem_daily_enabled() is expected


def test_missing_variable_resolves_to_disabled(monkeypatch):
    monkeypatch.delenv(_TRADE_POSTMORTEM_DAILY_ENV_VAR, raising=False)
    assert _trade_postmortem_daily_enabled() is False


def test_whitespace_padded_true_value_still_enables(monkeypatch):
    monkeypatch.setenv(_TRADE_POSTMORTEM_DAILY_ENV_VAR, "  true  ")
    assert _trade_postmortem_daily_enabled() is True
