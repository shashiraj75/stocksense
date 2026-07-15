"""
Product Integrity #010 §6 — Daily Picks and Premarket Finalizer schedules
are explicitly frozen by this release. This test reads the actual workflow
YAML files (not a copy of the values) and fails if any of these crons ever
drift, intentionally or accidentally, as part of Multibagger-scoped work.
"""
import os
import yaml

_WORKFLOWS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".github", "workflows"
)


def _load(name):
    with open(os.path.join(_WORKFLOWS_DIR, name)) as f:
        return yaml.safe_load(f)


def _crons(workflow):
    on = workflow.get(True) or workflow.get("on")
    return [entry["cron"] for entry in on["schedule"]]


def test_india_daily_picks_cron_unchanged():
    assert _crons(_load("daily_picks_in.yml")) == ["56 21 * * 0-4"]


def test_us_daily_picks_base_cron_unchanged():
    assert _crons(_load("daily_picks_us.yml")) == ["0 6 * * 1-5"]


def test_us_premarket_finalizer_crons_unchanged():
    assert _crons(_load("daily_picks_us_premarket.yml")) == ["0 10 * * 1-5", "0 11 * * 1-5"]


def test_india_daily_picks_targets_market_in():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in.yml")).read()
    assert "market=IN" in src


def test_us_daily_picks_base_targets_market_us():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_us.yml")).read()
    assert "market=US" in src


def test_us_premarket_finalizer_targets_market_us():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_us_premarket.yml")).read()
    assert "market=US" in src


def test_no_daily_picks_workflow_calls_multibagger_endpoint():
    for name in ("daily_picks_in.yml", "daily_picks_us.yml", "daily_picks_us_premarket.yml"):
        src = open(os.path.join(_WORKFLOWS_DIR, name)).read()
        assert "multibagger" not in src.lower()


def test_no_multibagger_workflow_calls_daily_picks_endpoint():
    for name in ("multibagger_refresh.yml", "multibagger_refresh_us.yml"):
        src = open(os.path.join(_WORKFLOWS_DIR, name)).read()
        assert "/api/picks/generate" not in src
        assert "premarket-finalize" not in src


def test_us_multibagger_is_single_cron_not_dual_dst_candidates():
    """Product Integrity #010: the #009 dual-candidate design (0 7 * * 0 / 0 8 * * 0) was replaced with one fixed cron."""
    assert _crons(_load("multibagger_refresh_us.yml")) == ["0 8 * * 0"]


def test_india_multibagger_cron_unchanged_from_009():
    assert _crons(_load("multibagger_refresh.yml")) == ["30 21 * * 5"]
