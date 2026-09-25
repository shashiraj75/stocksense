import os
from unittest.mock import patch

import pytest

from api.routers import picks
from services import daily_picks
from scripts import run_daily_picks_worker


def test_external_worker_only_flag_is_fail_closed_boolean():
    for value in ("1", "true", "TRUE", "yes", "on"):
        with patch.dict(os.environ, {"DAILY_PICKS_EXTERNAL_WORKER_ONLY": value}, clear=False):
            assert picks._external_worker_only() is True

    for value in ("0", "", "false", "no", "2"):
        with patch.dict(os.environ, {"DAILY_PICKS_EXTERNAL_WORKER_ONLY": value}, clear=False):
            assert picks._external_worker_only() is False


def test_governed_recovery_noops_when_external_worker_only():
    with patch.dict(
        os.environ,
        {
            "DAILY_PICKS_EXTERNAL_WORKER_ONLY": "1",
            "USE_POSTGRES": "1",
        },
        clear=False,
    ):
        assert daily_picks.attempt_governed_recovery("US", "test") == {
            "triggered": False,
            "reason": "external_worker_only",
        }


def test_worker_rejects_bad_market():
    with pytest.raises(ValueError):
        run_daily_picks_worker._market_from_argv(["worker.py", "EU"])


def test_worker_requires_durable_postgres_before_importing_pipeline():
    with patch.dict(os.environ, {"USE_POSTGRES": "0"}, clear=False):
        assert run_daily_picks_worker.run("IN") == 2
