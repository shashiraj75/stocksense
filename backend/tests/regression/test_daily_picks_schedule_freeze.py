"""
Daily Picks scheduling contract regression tests.

2026-09 dedicated-worker remediation: heavy IN/US base generation moved
from GitHub Actions cron into short-lived Railway cron workers so it no
longer shares the long-running API container's memory cgroup. The legacy
GitHub base-generation workflows intentionally remain present only as
manual diagnostic/rollback surfaces and therefore MUST NOT contain a
schedule block.

The approved production schedule instants themselves are unchanged:
IN 20:37 UTC Sun-Thu (02:07 IST Mon-Fri), US 06:00 UTC Mon-Fri. Those
instants are now enforced at Railway configuration/rollout verification
rather than by these GitHub workflow files. The separate US premarket
finalizer remains GitHub-scheduled and stays frozen here.
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


def test_india_base_workflow_is_manual_only_after_worker_migration():
    workflow = _load("daily_picks_in.yml")
    on = workflow.get(True) or workflow.get("on")
    assert "workflow_dispatch" in on
    assert "schedule" not in on


def test_us_base_workflow_is_manual_only_after_worker_migration():
    workflow = _load("daily_picks_us.yml")
    on = workflow.get(True) or workflow.get("on")
    assert "workflow_dispatch" in on
    assert "schedule" not in on


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


# ── Daily Picks Scheduler & Completion Reliability Hardening (2026-08) ────────

def test_india_watchdog_is_manual_only_after_worker_migration():
    """With base generation owned by Railway, the legacy API recovery
    watchdog must not retain an automatic GitHub schedule that could launch
    heavy work back inside the web/API container. Keep it only as a manual
    diagnostic/rollback surface."""
    workflow = _load("daily_picks_in_watchdog.yml")
    on = workflow.get(True) or workflow.get("on")
    assert "workflow_dispatch" in on
    assert "schedule" not in on


def test_india_watchdog_targets_market_in():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert "market=IN" in src


def test_india_watchdog_calls_recover_not_generate_directly():
    """The watchdog must go through the governed /api/picks/recover path
    (which reuses attempt_governed_recovery's bounded, atomic reservation),
    never call /api/picks/generate directly and bypass that governance."""
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert "/api/picks/recover" in src
    # The docstring/comments reference /api/picks/generate for context (the
    # primary endpoint this watchdog is a fail-safe for) — what matters is
    # that no curl call in this workflow actually POSTs to it.
    assert '"https://stocksense-production-7e0d.up.railway.app/api/picks/generate' not in src


def test_india_watchdog_does_not_call_multibagger_endpoint():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert "multibagger" not in src.lower()


def test_primary_workflows_now_poll_status_not_just_trigger():
    """Phase A: primary IN/US workflows must verify actual completion via
    the shared poller, not report success from the trigger POST alone."""
    for name in ("daily_picks_in.yml", "daily_picks_us.yml"):
        src = open(os.path.join(_WORKFLOWS_DIR, name)).read()
        assert "poll_daily_picks_completion.sh" in src
        assert "/api/picks/status" in src or "poll_daily_picks_completion.sh" in src


# ── Follow-up correction (2026-08-10): already_fresh vs already_running ───────

def test_india_watchdog_distinguishes_already_fresh_from_already_running():
    """already_fresh (obligation satisfied) and already_running (obligation
    still in progress) must not be collapsed into one benign no-op branch —
    already_running must be monitored to a real terminal+published outcome
    via the shared poller, using the recovery response's own job_id."""
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert 'REASON" = "already_fresh"' in src
    assert 'REASON" = "already_running"' in src
    # already_running must feed into the shared poller with a bound job_id
    # (status=already_running, http_code=409) — not treated as an immediate
    # no-op the way already_fresh is.
    assert "poll_daily_picks_completion.sh" in src
    assert '"status":"already_running"' in src
    assert '"409"' in src


def test_india_watchdog_response_body_never_directly_interpolated_into_shell():
    """Shell/JSON handoff safety (follow-up correction, 2026-08-10): the
    backend-controlled recovery response body must cross step boundaries
    via an on-disk RUNNER_TEMP file, never via a GITHUB_OUTPUT expression
    interpolated directly into `run:` shell text — a response body
    containing a single quote could otherwise break out of
    `'${{ steps.X.outputs.body }}'` shell quoting and be interpreted as
    shell content."""
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert "RUNNER_TEMP" in src
    assert "steps.recover.outputs.body" not in src


def test_primary_workflows_response_body_never_directly_interpolated_into_shell():
    for name in ("daily_picks_in.yml", "daily_picks_us.yml"):
        src = open(os.path.join(_WORKFLOWS_DIR, name)).read()
        assert "RUNNER_TEMP" in src
        assert "steps.trigger.outputs.body" not in src


def test_india_primary_workflow_does_not_echo_raw_response_body():
    """Diagnostics/log-safety hardening (2026-08-10 follow-up, Finding 2):
    the trigger step must not echo the full/raw backend response body to
    the human-visible log — only bounded safe fields extracted via jq. The
    RUNNER_TEMP file-based handoff itself (used internally by jq/the
    poller) is unaffected; this only asserts against printing it whole."""
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in.yml")).read()
    assert "Trigger response body: $(cat" not in src
    assert "jq -c '{status, market, job_id}'" in src
    assert "RUNNER_TEMP" in src  # handoff mechanism itself still intact


def test_us_primary_workflow_does_not_echo_raw_response_body():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_us.yml")).read()
    assert "Trigger response body: $(cat" not in src
    assert "jq -c '{status, market, job_id}'" in src
    assert "RUNNER_TEMP" in src


def test_india_watchdog_does_not_echo_raw_recovery_response_body():
    src = open(os.path.join(_WORKFLOWS_DIR, "daily_picks_in_watchdog.yml")).read()
    assert "Recovery response body: $(cat" not in src
    assert "jq -c '{triggered, reason, market, job_id}'" in src
    assert "RUNNER_TEMP" in src
