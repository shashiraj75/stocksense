"""
3-phase US Daily Picks upgrade — static, source-text-based checks (SES-003
§2: "code shape" property, no execution) on the GitHub Actions workflow
files themselves. Locks in the cron schedule changes and the new premarket
finalizer workflow so a future edit can't silently change the trigger times
or accidentally point either workflow at the wrong backend endpoint.
"""
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


@pytest.mark.regression
class TestUsBaseWorkflowCronChanged:
    def test_cron_changed_to_early_run(self):
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert 'cron: "0 4 * * 1-5"' in src
        assert 'cron: "17 12 * * 1-5"' not in src

    def test_still_calls_generate_endpoint_for_us(self):
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert "/api/picks/generate?market=US" in src

    def test_workflow_dispatch_still_present(self):
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert "workflow_dispatch:" in src

    def test_never_calls_the_finalizer_endpoint(self):
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert "premarket-finalize" not in src


@pytest.mark.regression
class TestIndiaWorkflowUntouched:
    def test_cron_unchanged(self):
        src = (_WORKFLOWS / "daily_picks_in.yml").read_text()
        assert 'cron: "56 21 * * 0-4"' in src

    def test_still_calls_generate_endpoint_for_in(self):
        src = (_WORKFLOWS / "daily_picks_in.yml").read_text()
        assert "/api/picks/generate?market=IN" in src

    def test_no_premarket_finalizer_reference_at_all(self):
        src = (_WORKFLOWS / "daily_picks_in.yml").read_text()
        assert "premarket" not in src.lower()


@pytest.mark.regression
class TestUsPremarketFinalizerWorkflowExists:
    def test_file_exists(self):
        assert (_WORKFLOWS / "daily_picks_us_premarket.yml").exists()

    def test_has_both_dst_cron_candidates(self):
        src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert 'cron: "5 12 * * 1-5"' in src   # EDT candidate
        assert 'cron: "5 13 * * 1-5"' in src   # EST candidate

    def test_workflow_dispatch_present(self):
        src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert "workflow_dispatch:" in src

    def test_calls_only_the_finalizer_endpoint_never_generate(self):
        src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert "/api/picks/premarket-finalize" in src
        assert "/api/picks/generate" not in src

    def test_includes_a_health_check_before_finalizing(self):
        src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert "/health" in src

    def test_uses_the_same_secret_header_pattern_as_generate(self):
        generate_src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        finalizer_src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert "x-secret: ${{ secrets.PICKS_SECRET }}" in generate_src
        assert "x-secret: ${{ secrets.PICKS_SECRET }}" in finalizer_src
