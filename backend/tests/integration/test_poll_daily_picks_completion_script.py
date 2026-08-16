"""
Daily Picks Scheduler & Completion Reliability Hardening, follow-up
correction (2026-08-10) — Finding 4.

Executable behavioral tests for scripts/ci/poll_daily_picks_completion.sh
itself. Prior coverage (test_daily_picks_schedule_freeze.py) only asserted
static facts about the workflow YAML TEXT (e.g. "the file mentions
poll_daily_picks_completion.sh") — it never actually ran the script's
logic. That is insufficient for logic this critical to the Daily Picks
scheduler's success/failure determination, so this file invokes the real
script as a subprocess, with:
  - POLL_INTERVAL_SECS=0 and a small MAX_POLLS so tests run in well under a
    second each — no real sleeping, no live network.
  - A stub `curl` placed first on PATH (see `_stub_curl_dir` below) so the
    script's own polling-loop `curl ... /api/picks/status` calls are
    answered by a small, deterministic, ordered sequence of canned JSON
    responses instead of any real HTTP call. Nothing in this file talks to
    the real Railway backend or any live provider.
The script itself is NOT modified to support testing — the stub curl
approach requires zero changes to scripts/ci/poll_daily_picks_completion.sh,
keeping test-only code entirely out of the production script.
"""
import json
import os
import stat
import subprocess
import textwrap

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "ci", "poll_daily_picks_completion.sh")
_BASE_URL = "https://stub.invalid"
_MARKET = "IN"


def _stub_curl_dir(tmp_path, responses):
    """
    Build a directory containing a fake `curl` executable that returns the
    given list of JSON strings in order, one per invocation of the
    script's polling-loop `curl -sS --max-time 20 <status-url>` call —
    the ONLY curl invocation this script ever makes; the trigger/recovery
    body is always passed in as a CLI argument, never fetched by the
    script itself. Extra polls past the end of `responses` keep returning
    the last entry (so "polling window exhaustion" tests don't need to
    pad the list to MAX_POLLS length).
    Returns the stub directory path to prepend to PATH.
    """
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    responses_file = tmp_path / "responses.jsonl"
    responses_file.write_text("\n".join(responses) + "\n")
    counter_file = tmp_path / "curl_call_count"
    counter_file.write_text("0")

    # A minimal bash stub: every invocation increments a counter file and
    # echoes the corresponding line from responses.jsonl (or the last line
    # once the sequence is exhausted). Uses `flock`-free sequential reads —
    # safe here because the script under test never calls curl concurrently
    # with itself (one poll at a time, strictly sequential).
    stub = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Stub curl for scripts/ci/poll_daily_picks_completion.sh tests.
        # Only handles the exact form the script under test uses:
        #   curl -sS --max-time 20 "<status-url>"
        # Ignores all flags, always succeeds (exit 0), echoes the next
        # canned response line to stdout.
        COUNT=$(cat "{counter_file}")
        LINE_COUNT=$(wc -l < "{responses_file}")
        if [ "$COUNT" -ge "$LINE_COUNT" ]; then
          IDX=$LINE_COUNT
        else
          IDX=$((COUNT + 1))
        fi
        sed -n "${{IDX}}p" "{responses_file}"
        NEXT=$((COUNT + 1))
        echo "$NEXT" > "{counter_file}"
        exit 0
        """)
    curl_path = bin_dir / "curl"
    curl_path.write_text(stub)
    curl_path.chmod(curl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_dir)


def _run_poller(tmp_path, trigger_http_code, trigger_body, status_responses,
                 extra_env=None, max_polls="3"):
    """
    Invoke the real poll_daily_picks_completion.sh with a stub curl on
    PATH ahead of the real one (so jq — needed by the script — still
    resolves to the real binary; only `curl` is stubbed). Real `sleep` is
    still used by the script but POLL_INTERVAL_SECS=0 makes every sleep a
    no-op, so tests run near-instantly.
    """
    stub_dir = _stub_curl_dir(tmp_path, status_responses) if status_responses else None
    env = dict(os.environ)
    if stub_dir:
        env["PATH"] = stub_dir + os.pathsep + env["PATH"]
    env["POLL_INTERVAL_SECS"] = "0"
    env["MAX_POLLS"] = max_polls
    env["STALE_HEARTBEAT_SECS"] = "180"
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", _SCRIPT, _BASE_URL, _MARKET, trigger_http_code, trigger_body],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return result


def _status(**overrides):
    """A minimal, valid /api/picks/status-shaped response — override only
    the fields relevant to the case under test, matching the convention
    other test files in this repo use for fixture construction."""
    base = {
        "market": _MARKET,
        "job_id": "job-abc-123",
        "job_status": "running",
        "phase": "phase_2_scoring",
        "processed": 10,
        "total": 100,
        "last_runner_heartbeat_at": "2026-08-10T20:40:00Z",
        "last_progress_at": "2026-08-10T20:39:00Z",
        "has_today": False,
        "last_successful_generated_at": None,
        "derived_job_health": "ok",
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.mark.integration
class TestPollerAcceptedNewJobToPublishedSuccess:
    def test_1_accepted_running_then_completed_published_exits_zero(self, tmp_path):
        """Case 1: 202 accepted+job_id -> running -> completed+has_today=true
        +last_successful_generated_at present -> exit 0."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [
            _status(job_status="running"),
            _status(job_status="completed", has_today=True,
                     last_successful_generated_at="2026-08-10T20:45:00Z"),
        ]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode == 0, result.stderr
        assert "SUCCESS" in result.stdout


@pytest.mark.integration
class TestPollerAlreadyRunningJoinsExistingJob:
    def test_2_already_running_with_job_id_completed_published_exits_zero(self, tmp_path):
        """Case 2: 409 already_running+job_id -> completed+published -> exit 0."""
        trigger_body = json.dumps({"status": "already_running", "job_id": "job-abc-123"})
        responses = [
            _status(job_status="completed", has_today=True,
                     last_successful_generated_at="2026-08-10T20:45:00Z"),
        ]
        result = _run_poller(tmp_path, "409", trigger_body, responses)
        assert result.returncode == 0, result.stderr
        assert "SUCCESS" in result.stdout


@pytest.mark.integration
class TestPollerMissingJobIdIsRejected:
    def test_3_accepted_without_job_id_exits_nonzero(self, tmp_path):
        """Case 3: accepted/already_running WITHOUT job_id -> exit non-zero."""
        trigger_body = json.dumps({"status": "accepted"})
        result = _run_poller(tmp_path, "202", trigger_body, status_responses=[])
        assert result.returncode != 0
        assert "cannot safely bind to job identity" in result.stderr

    def test_3b_already_running_without_job_id_exits_nonzero(self, tmp_path):
        trigger_body = json.dumps({"status": "already_running"})
        result = _run_poller(tmp_path, "409", trigger_body, status_responses=[])
        assert result.returncode != 0
        assert "cannot safely bind to job identity" in result.stderr


@pytest.mark.integration
class TestPollerRejectsWrongJobId:
    def test_4_different_job_id_never_short_circuits_success(self, tmp_path):
        """Case 4: a status response with a job_id different from the bound
        one is never accepted as that job's success — the poller must keep
        polling (and eventually time out) rather than short-circuit."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-bound-1"})
        # Every canned response is for a DIFFERENT job (already completed
        # and published) — if the poller incorrectly accepted this, it
        # would exit 0. It must instead exhaust MAX_POLLS and fail.
        responses = [
            _status(job_id="job-OTHER-999", job_status="completed", has_today=True,
                     last_successful_generated_at="2026-08-10T20:45:00Z"),
        ]
        result = _run_poller(tmp_path, "202", trigger_body, responses, max_polls="2")
        assert result.returncode != 0, (
            "poller incorrectly reported success for a different job_id's completed+published state"
        )
        assert "does not match bound job_id" in result.stdout
        assert "polling window exhausted" in result.stderr


@pytest.mark.integration
class TestPollerTerminalFailureStates:
    def test_5_failed_status_is_immediate_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="failed")]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "terminal non-success status=failed" in result.stderr

    def test_6_interrupted_status_is_immediate_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="interrupted")]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "terminal non-success status=interrupted" in result.stderr

    def test_7_expired_status_is_immediate_failure(self, tmp_path):
        """Reconciled against the actual DB CHECK constraint on
        daily_picks_jobs.status (backend/services/postgres_store.py:1115)
        and the existing _JOB_STATUS_TERMINAL_FAILURE set in
        backend/services/premarket_finalizer.py:88 — 'expired' is a real,
        DB-enforced terminal failure state, not an invented one."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="expired")]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "terminal non-success status=expired" in result.stderr


@pytest.mark.integration
class TestPollerHeartbeatStaleness:
    def test_8_unresponsive_derived_health_is_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="running", derived_job_health="unresponsive")]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "unresponsive" in result.stderr


@pytest.mark.integration
class TestPollerPublicationEvidenceRequired:
    def test_9_completed_but_has_today_false_is_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="completed", has_today=False,
                              last_successful_generated_at="2026-08-09T20:45:00Z")]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "publication evidence" in result.stderr

    def test_10_completed_but_no_publication_timestamp_is_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="completed", has_today=True,
                              last_successful_generated_at=None)]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode != 0
        assert "publication evidence" in result.stderr


@pytest.mark.integration
class TestPollerTimeout:
    def test_11_polling_window_exhaustion_is_failure(self, tmp_path):
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="running")]  # never reaches a terminal state
        result = _run_poller(tmp_path, "202", trigger_body, responses, max_polls="2")
        assert result.returncode != 0
        assert "polling window exhausted" in result.stderr


@pytest.mark.integration
class TestPollerAlreadyFreshNoop:
    def test_12_already_fresh_is_legitimate_success_noop(self, tmp_path):
        trigger_body = json.dumps({"status": "already_fresh", "market": _MARKET})
        # No status_responses supplied — a real bug here would surface as
        # a subprocess/curl-stub failure since no stub curl is even set up.
        result = _run_poller(tmp_path, "200", trigger_body, status_responses=[])
        assert result.returncode == 0, result.stderr
        assert "already_fresh" in result.stdout
        assert "SUCCESS: bound job_id=" not in result.stdout  # the polling-success message, not the no-op message


@pytest.mark.integration
class TestPollerMalformedOrUnrecognizedTrigger:
    def test_13_malformed_trigger_response_is_failure(self, tmp_path):
        """A trigger body that isn't valid JSON must be reported as a
        genuine trigger failure via the fixed 'malformed_trigger_response'
        label — never silently treated as a success, and (diagnostics/
        log-safety hardening, 2026-08-10 follow-up) never by reproducing
        the raw un-parseable content in the log."""
        RAW_GARBAGE = "not valid json at all -- canary-should-not-appear-xyz123"
        result = _run_poller(tmp_path, "503", RAW_GARBAGE, status_responses=[])
        assert result.returncode != 0
        assert "malformed_trigger_response" in result.stderr
        assert RAW_GARBAGE not in result.stdout
        assert RAW_GARBAGE not in result.stderr
        assert "canary-should-not-appear-xyz123" not in result.stdout
        assert "canary-should-not-appear-xyz123" not in result.stderr

    def test_13b_resource_busy_409_is_failure(self, tmp_path):
        """A well-formed but unrecognized status/http_code combination
        (409 resource_busy) is a genuine failure, and — diagnostics/
        log-safety hardening — the failure log reports only bounded safe
        fields (http_code/status/market/job_id_present), never an
        arbitrary backend `message` string, even though this particular
        response happens not to carry one."""
        trigger_body = json.dumps({"status": "resource_busy", "market": _MARKET})
        result = _run_poller(tmp_path, "409", trigger_body, status_responses=[])
        assert result.returncode != 0
        assert "did not yield a recognized success outcome" in result.stderr
        assert "status=resource_busy" in result.stderr
        assert f"market={_MARKET}" in result.stderr

    def test_13c_unrecognized_trigger_message_field_never_echoed(self, tmp_path):
        """(Finding 4, item 7) Feed a well-formed-but-unrecognized trigger
        response whose `message` field contains a canary string, and prove
        the canary never appears anywhere in the poller's output — the
        failure log must report only parsed status/http_code/market/
        job_id-presence, never the free-form backend message."""
        CANARY = "CANARY-SECRET-LOOKING-MESSAGE-987"
        trigger_body = json.dumps({
            "status": "durable_job_state_unavailable",
            "market": _MARKET,
            "message": f"Could not verify durable active job state — internal detail: {CANARY}",
        })
        result = _run_poller(tmp_path, "503", trigger_body, status_responses=[])
        assert result.returncode != 0
        assert CANARY not in result.stdout
        assert CANARY not in result.stderr

    def test_13d_missing_job_id_failure_never_echoes_raw_body(self, tmp_path):
        """The 'accepted/already_running but no job_id' failure path also
        must not reproduce the raw trigger body — only job_id_present=false
        plus the other bounded safe fields."""
        trigger_body = json.dumps({
            "status": "accepted",
            "message": "no job_id here on purpose — canary-body-content-should-not-leak",
        })
        result = _run_poller(tmp_path, "202", trigger_body, status_responses=[])
        assert result.returncode != 0
        assert "job_id_present=false" in result.stderr
        assert "canary-body-content-should-not-leak" not in result.stdout
        assert "canary-body-content-should-not-leak" not in result.stderr

    def test_13e_safe_operational_fields_still_visible_in_normal_polling(self, tmp_path):
        """(Finding 4, item 8) Sanity check that the log-safety hardening
        did not over-sanitize into complete silence: normal per-poll status
        fields (job_id/market/status/phase/processed/total/heartbeat/
        last_progress/has_today/last_successful_generated_at) remain fully
        visible in the success path."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-visible-1"})
        responses = [
            _status(job_status="running", processed=3, total=10, phase="scoring",
                    job_id="job-visible-1"),
            _status(job_status="completed", has_today=True,
                    last_successful_generated_at="2026-08-10T20:45:00Z",
                    job_id="job-visible-1"),
        ]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode == 0, result.stderr
        assert "job_id=job-visible-1" in result.stdout or "bound_job_id=job-visible-1" in result.stdout
        assert "phase=scoring" in result.stdout
        assert "processed=3" in result.stdout
        assert "total=10" in result.stdout


@pytest.mark.integration
class TestPollerZeroValuesSurviveShellHandling:
    def test_genuine_zero_processed_and_total_are_logged_not_lost_to_truthiness(self, tmp_path):
        """Proves processed=0/total=0 (genuine zero, e.g. before the first
        batch completes) survive the script's jq/bash handling and are
        logged as the literal string '0', never coerced to 'null' or
        silently dropped — the script's own jq expressions
        (`if .processed == null then "null" else (.processed|tostring) end`)
        are specifically written to distinguish real zero from absent."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [
            _status(job_status="running", processed=0, total=0),
            _status(job_status="completed", has_today=True,
                    last_successful_generated_at="2026-08-10T20:45:00Z", processed=0, total=0),
        ]
        result = _run_poller(tmp_path, "202", trigger_body, responses)
        assert result.returncode == 0, result.stderr
        assert "processed=0 total=0" in result.stdout
        assert "processed=null" not in result.stdout
        assert "total=null" not in result.stdout


@pytest.mark.integration
class TestPollerWindowCalibration:
    """Follow-up correction (2026-08-11): MAX_POLLS default raised from 30
    to 90 after both markets' first natural Production runs (~42-46 min)
    exceeded the old 30-minute outer window while remaining continuously
    healthy. These tests prove (A) poll #30 is no longer a special/magic
    boundary a healthy job can run past, (B) the NEW outer bound is still
    enforced (via a small test-only MAX_POLLS override, never a real
    90-minute or even 90-second wait), and (K) the script's actual
    Production default (MAX_POLLS unset) is now 90, not 30."""

    def test_a_healthy_job_survives_past_old_30_poll_boundary_then_completes(self, tmp_path):
        """Simulate 32 consecutive healthy 'running' responses (past the
        OLD 30-poll boundary) for the exact same bound job_id, with
        derived_job_health=ok throughout, followed by a completed+published
        response. Uses a test-only MAX_POLLS=40 (not the real 90) so the
        test still runs near-instantly (POLL_INTERVAL_SECS=0) while
        proving the old 30-poll cutoff has no special significance anymore."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        running_responses = [
            _status(job_status="running", processed=i, total=100, derived_job_health="ok")
            for i in range(1, 33)  # 32 healthy polls, past the old MAX_POLLS=30
        ]
        responses = running_responses + [
            _status(job_status="completed", has_today=True,
                    last_successful_generated_at="2026-08-11T07:24:31Z"),
        ]
        result = _run_poller(tmp_path, "202", trigger_body, responses, max_polls="40")
        assert result.returncode == 0, result.stderr
        assert "SUCCESS" in result.stdout
        assert "attempt 31/40" in result.stdout
        assert "attempt 32/40" in result.stdout

    def test_b_new_outer_bound_still_enforced_with_small_override(self, tmp_path):
        """The expanded window is not unbounded: a job that keeps running
        forever without completing must still exit non-zero once the
        (test-overridden, small) outer bound is hit."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [_status(job_status="running", derived_job_health="ok")]
        result = _run_poller(tmp_path, "202", trigger_body, responses, max_polls="4")
        assert result.returncode != 0
        assert "polling window exhausted (4 x" in result.stderr

    def test_k_production_default_max_polls_is_90_not_30(self, tmp_path):
        """Invoke the script with MAX_POLLS left UNSET (deleted from the
        env, not just omitted from the override dict) and confirm from its
        own logged output that it computed the real Production default of
        90, not the old 30. POLL_INTERVAL_SECS is still forced to 0 so the
        test doesn't actually wait 90 minutes — this only asserts the
        *bound* the script computes, not a real exhaustion of it."""
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        responses = [
            _status(job_status="completed", has_today=True,
                    last_successful_generated_at="2026-08-11T07:24:31Z"),
        ]
        stub_dir = _stub_curl_dir(tmp_path, responses)
        env = dict(os.environ)
        env["PATH"] = stub_dir + os.pathsep + env["PATH"]
        env["POLL_INTERVAL_SECS"] = "0"
        env.pop("MAX_POLLS", None)  # ensure truly unset -> script's own default applies
        env["STALE_HEARTBEAT_SECS"] = "180"

        result = subprocess.run(
            ["bash", _SCRIPT, _BASE_URL, _MARKET, "202", trigger_body],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "attempt 1/90" in result.stdout
        assert "attempt 1/30" not in result.stdout


@pytest.mark.integration
class TestPollerResumesAfterTransientUnreachableStatus:
    def test_status_endpoint_unreachable_then_recovers(self, tmp_path):
        """Not one of the 13 numbered cases, but proves the retry-on-
        curl-failure branch: a transient status-endpoint outage must not
        be treated as job failure — the poller should just keep polling."""
        # Simulate an unreachable status endpoint on the first call by
        # having the stub return an empty line (the script treats an
        # empty STATUS_JSON the same as a curl failure and retries).
        responses = [
            "",
            _status(job_status="completed", has_today=True,
                    last_successful_generated_at="2026-08-10T20:45:00Z"),
        ]
        trigger_body = json.dumps({"status": "accepted", "job_id": "job-abc-123"})
        result = _run_poller(tmp_path, "202", trigger_body, responses, max_polls="3")
        assert result.returncode == 0, result.stderr
        assert "status endpoint unreachable" in result.stdout
        assert "SUCCESS" in result.stdout
