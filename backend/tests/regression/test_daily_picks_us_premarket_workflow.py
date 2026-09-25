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
    def test_legacy_github_base_workflow_is_manual_only(self):
        # 2026-09 dedicated-worker remediation: the unchanged 06:00 UTC
        # production slot is now owned by the short-lived Railway worker.
        # This legacy GitHub workflow remains only as a manual diagnostic /
        # rollback surface and must never race the Railway cron.
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert "workflow_dispatch:" in src
        assert "schedule:" not in src
        assert 'cron: "0 6 * * 1-5"' not in src

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
    def test_legacy_github_base_workflow_is_manual_only(self):
        # The approved 20:37 UTC / 02:07 IST production slot is unchanged,
        # but is now owned by the dedicated Railway worker. GitHub must not
        # retain a competing automatic base-generation schedule.
        src = (_WORKFLOWS / "daily_picks_in.yml").read_text()
        assert "workflow_dispatch:" in src
        assert "schedule:" not in src
        assert 'cron: "37 20 * * 0-4"' not in src

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
        # 2026-07-15: moved from ~7:35 AM ET ("35 11"/"35 12") to 6:00 AM ET
        # so 6:00 AM ET becomes the authoritative finalization attempt (see
        # services/premarket_finalizer.py's in_premarket_window() docstring
        # for the full history, including the 2026-07-13 missed-run
        # rationale this retargeting deliberately preserves).
        src = (_WORKFLOWS / "daily_picks_us_premarket.yml").read_text()
        assert 'cron: "0 10 * * 1-5"' in src   # EDT candidate
        assert 'cron: "0 11 * * 1-5"' in src   # EST candidate
        assert 'cron: "35 11 * * 1-5"' not in src  # superseded 2026-07-15
        assert 'cron: "35 12 * * 1-5"' not in src  # superseded 2026-07-15

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
