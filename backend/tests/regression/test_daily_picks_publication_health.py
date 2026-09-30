"""Missing, interrupted and stale publications must fail the read-only monitor."""
import pytest

from scripts.check_daily_picks_health import publication_healthy


@pytest.mark.parametrize("status", [
    {},
    {"has_today": False, "last_attempt_status": "interrupted"},
    {"has_today": False, "generating": True},
    {"has_today": True, "stale": True, "serving_stale_payload": True},
    {"has_today": True},
])
def test_monitor_fails_closed(status):
    assert not publication_healthy(status)


def test_monitor_accepts_successful_publication_including_zero_buy_day():
    assert publication_healthy({
        "has_today": True, "stale": False, "serving_stale_payload": False,
    })
