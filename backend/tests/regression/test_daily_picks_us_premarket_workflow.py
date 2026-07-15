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
    def test_cron_is_the_dst_corrected_early_run(self):
        # 2026-07-15: moved from 04:00 UTC to 06:00 UTC — 04:00 UTC during
        # EST (UTC-5) lands at 23:00 ET the PREVIOUS calendar date, which
        # would make every EST-season base permanently fail the premarket
        # finalizer's same-ET-day provenance check (see
        # daily_picks_us.yml's own comment and premarket_finalizer.py's
        # validate_base_for_finalization() check E). 06:00 UTC lands on the
        # correct ET calendar day in both EDT and EST, several hours before
        # either 6:00 AM ET finalizer candidate.
        src = (_WORKFLOWS / "daily_picks_us.yml").read_text()
        assert 'cron: "0 6 * * 1-5"' in src
        assert 'cron: "0 4 * * 1-5"' not in src  # superseded 2026-07-15
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
