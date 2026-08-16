#!/usr/bin/env bash
# Daily Picks Scheduler & Completion Reliability Hardening (2026-08).
#
# Shared completion-verification helper for the India and US-base Daily
# Picks GitHub Actions workflows. Reused (not duplicated) by both so the
# definition of "the trigger actually completed and published" lives in
# exactly one place.
#
# WHY THIS EXISTS: the primary workflows previously treated curl's exit
# code on the trigger POST as success. An HTTP 202 from /api/picks/generate
# means "accepted", not "completed" — the actual generation runs in a
# background task afterwards and can still fail, stall, or be interrupted
# by a Railway restart. This script polls the EXISTING, durable
# /api/picks/status endpoint (job_id / status / phase / heartbeat / progress
# / publication fields already maintained by backend/services/daily_picks.py
# and backend/services/postgres_store.py — no new job-lifecycle system is
# introduced here) until it can prove real completion, or fails loudly.
#
# USAGE:
#   poll_daily_picks_completion.sh <base_url> <market> <trigger_http_code> <trigger_body_json>
#
# trigger_http_code / trigger_body_json are what the caller got back from
# POSTing to /api/picks/generate (or /api/picks/recover) — this script
# reads them to decide which of the four documented outcomes applies
# instead of guessing:
#   A) 202 accepted            -> bind to the exact job_id and poll it
#   B) 409 already_running     -> bind to the existing job_id and poll it
#      (compatible in-progress run; NOT a duplicate, NOT a failure)
#   C) 200 already_fresh       -> success, no-op, no polling needed
#   D) anything else           -> genuine trigger failure, exit non-zero now
#
# EXIT CODE: 0 only when the exact bound job (or an already-fresh result)
# reaches a durably-published state for TODAY's market business date.
# Non-zero for every other outcome, including "we ran out of polls".
#
# TIMING LIMITATION (documented, not solved): GitHub Actions does not
# reliably expose the delta between the scheduled cron time and the actual
# runner dispatch time. This script logs its own wall-clock start time and
# the trigger-acceptance time it was handed as the only two data points
# available to distinguish "GitHub was late to dispatch us" from
# "generation itself was slow" — it cannot produce exact precision beyond
# that and does not pretend to.

set -uo pipefail

BASE_URL="${1:?usage: poll_daily_picks_completion.sh <base_url> <market> <trigger_http_code> <trigger_body_json>}"
MARKET="${2:?market required (IN|US)}"
TRIGGER_HTTP_CODE="${3:?trigger http code required}"
TRIGGER_BODY="${4:?trigger response body (JSON) required}"

# Operational tuning parameters — calibrated from real Production evidence,
# not from the originally-documented (and now known-wrong) "~10-20 minutes"
# generation-time claim.
#
# Follow-up correction (2026-08-11): both markets' FIRST natural post-merge
# Production runs exceeded the original 30 x 60s (30-minute) outer window
# while remaining continuously healthy the entire time (steady `processed`
# progress, fresh heartbeat, derived_job_health=ok at every single poll —
# never stalled, just genuinely still running):
#   - India job f260f473-3d41-497a-b844-b2e44d1db2bc: accepted ~21:23:13 UTC
#     2026-08-10, completed+published at 2026-08-10T22:09:20Z — ~46 min.
#   - US job b390a7e9-32e6-4f63-845f-4d2b03eb80b8: accepted ~06:42:48 UTC
#     2026-08-11, completed+published at 2026-08-11T07:24:31Z — ~41m43s.
# Both workflow runs reported FAILURE solely because the window was too
# short — a false-negative (the architecture correctly refused to claim
# success for a genuinely-unfinished job), not a false-success.
#
# 90 x 60s (90 minutes) is chosen as roughly 2x the largest observed
# healthy runtime (~46 min) as a conservative outer bound. This is an
# evidence-based estimate from an N=2 sample, NOT presented as
# mathematically optimal — it is expected to be revisited once more
# natural-run history accumulates. Deliberately NOT retained: the old
# "generation normally takes ~10-20 minutes" assumption, which production
# evidence has now disproven.
POLL_INTERVAL_SECS="${POLL_INTERVAL_SECS:-60}"
MAX_POLLS="${MAX_POLLS:-90}"
# A job is considered stuck if its heartbeat/progress hasn't moved in this
# long — matches the backend's own _HEARTBEAT_UNRESPONSIVE_SECS (180s)
# used by /api/picks/status's derived_job_health, so this script's
# stale-heartbeat judgement agrees with the backend's rather than
# inventing a second, competing threshold.
STALE_HEARTBEAT_SECS="${STALE_HEARTBEAT_SECS:-180}"

WORKFLOW_START_TIME_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[poll] workflow_start_time_utc=${WORKFLOW_START_TIME_UTC} market=${MARKET} trigger_http_code=${TRIGGER_HTTP_CODE}"
echo "[poll] NOTE: GitHub Actions does not expose exact scheduled-vs-dispatch delta;"
echo "[poll] the above is the only dispatch-timing evidence available to this script."

need() { command -v "$1" >/dev/null 2>&1 || { echo "[poll] FATAL: missing required tool: $1" >&2; exit 3; }; }
need curl
need jq

# Follow-up correction (2026-08-10, diagnostics/log-safety hardening): the
# trigger response body is never reproduced verbatim in any log line below
# (it can carry an arbitrary free-form backend `message` string). If it
# isn't even valid JSON, that's reported via the fixed label
# "malformed_trigger_response" rather than echoing whatever the un-parseable
# content was. Otherwise, only bounded, known-safe fields (http_code,
# parsed status if present, market, whether job_id was present/absent) are
# logged — normal per-poll status logging further down is unaffected.
if echo "$TRIGGER_BODY" | jq -e . >/dev/null 2>&1; then
  TRIGGER_BODY_VALID_JSON=1
  TRIGGER_STATUS="$(echo "$TRIGGER_BODY" | jq -r '.status // empty')"
  JOB_ID="$(echo "$TRIGGER_BODY" | jq -r '.job_id // empty')"
else
  TRIGGER_BODY_VALID_JSON=0
  TRIGGER_STATUS=""
  JOB_ID=""
fi

if [ "$TRIGGER_BODY_VALID_JSON" -ne 1 ]; then
  echo "[poll] FAILURE: malformed_trigger_response market=${MARKET} http_code=${TRIGGER_HTTP_CODE} — trigger response body was not valid JSON." >&2
  exit 1
fi

# Outcome C: already fresh — success, no duplicate job, no polling.
if [ "$TRIGGER_HTTP_CODE" = "200" ] && [ "$TRIGGER_STATUS" = "already_fresh" ]; then
  echo "[poll] outcome=already_fresh market=${MARKET} — treating as SUCCESS, no polling needed."
  exit 0
fi

# Outcome A/B: 202 accepted (new job) or 409 already_running (compatible
# existing job) — both are legitimate, both carry a job_id to bind to.
if { [ "$TRIGGER_HTTP_CODE" = "202" ] && [ "$TRIGGER_STATUS" = "accepted" ]; } \
   || { [ "$TRIGGER_HTTP_CODE" = "409" ] && [ "$TRIGGER_STATUS" = "already_running" ]; }; then
  if [ -z "$JOB_ID" ] || [ "$JOB_ID" = "null" ]; then
    echo "[poll] FAILURE: trigger_http_code=${TRIGGER_HTTP_CODE} status=${TRIGGER_STATUS} market=${MARKET} job_id_present=false — cannot safely bind to job identity." >&2
    exit 1
  fi
  echo "[poll] outcome=${TRIGGER_STATUS} market=${MARKET} bound_job_id=${JOB_ID} — polling /api/picks/status for completion."
else
  # Outcome D: genuine trigger failure (409 resource_busy, 503
  # durable_job_state_unavailable, non-2xx, unrecognized status, etc). Only
  # the parsed status/http_code/market/job_id-presence are logged — never
  # the raw body or an arbitrary backend `message` value.
  echo "[poll] FAILURE: trigger did not yield a recognized success outcome. http_code=${TRIGGER_HTTP_CODE} status=${TRIGGER_STATUS:-<none>} market=${MARKET} job_id_present=$([ -n "$JOB_ID" ] && [ "$JOB_ID" != "null" ] && echo true || echo false)" >&2
  exit 1
fi

for i in $(seq 1 "$MAX_POLLS"); do
  STATUS_JSON="$(curl -sS --max-time 20 "${BASE_URL}/api/picks/status?market=${MARKET}")"
  CURL_RC=$?
  if [ $CURL_RC -ne 0 ] || [ -z "$STATUS_JSON" ]; then
    echo "[poll] attempt ${i}/${MAX_POLLS}: status endpoint unreachable (curl_rc=${CURL_RC}) — will retry."
    sleep "$POLL_INTERVAL_SECS"
    continue
  fi

  RESP_JOB_ID="$(echo "$STATUS_JSON" | jq -r '.job_id // empty')"
  JOB_STATUS="$(echo "$STATUS_JSON" | jq -r '.job_status // empty')"
  PHASE="$(echo "$STATUS_JSON" | jq -r '.phase // empty')"
  PROCESSED="$(echo "$STATUS_JSON" | jq -r 'if .processed == null then "null" else (.processed|tostring) end')"
  TOTAL="$(echo "$STATUS_JSON" | jq -r 'if .total == null then "null" else (.total|tostring) end')"
  HEARTBEAT="$(echo "$STATUS_JSON" | jq -r '.last_runner_heartbeat_at // empty')"
  LAST_PROGRESS="$(echo "$STATUS_JSON" | jq -r '.last_progress_at // empty')"
  HAS_TODAY="$(echo "$STATUS_JSON" | jq -r 'if .has_today == null then "null" else (.has_today|tostring) end')"
  LAST_SUCCESS_AT="$(echo "$STATUS_JSON" | jq -r '.last_successful_generated_at // empty')"
  DERIVED_HEALTH="$(echo "$STATUS_JSON" | jq -r '.derived_job_health // empty')"

  echo "[poll] attempt ${i}/${MAX_POLLS} market=${MARKET} bound_job_id=${JOB_ID} resp_job_id=${RESP_JOB_ID} job_status=${JOB_STATUS} phase=${PHASE} processed=${PROCESSED} total=${TOTAL} heartbeat=${HEARTBEAT} last_progress_at=${LAST_PROGRESS} has_today=${HAS_TODAY} last_successful_generated_at=${LAST_SUCCESS_AT} derived_job_health=${DERIVED_HEALTH}"

  # An OLD or DIFFERENT-market job must never satisfy this monitor — the
  # status response's job_id must exactly match the one this workflow
  # bound to at trigger time.
  if [ "$RESP_JOB_ID" != "$JOB_ID" ]; then
    echo "[poll] attempt ${i}/${MAX_POLLS}: status job_id (${RESP_JOB_ID:-<none>}) does not match bound job_id (${JOB_ID}) yet — continuing to poll (may be a transient read of a newer/older row)."
    sleep "$POLL_INTERVAL_SECS"
    continue
  fi

  # Terminal non-success set, reconciled against the actual DB CHECK
  # constraint on daily_picks_jobs.status (backend/services/postgres_store.py):
  #   CHECK (status IN ('queued', 'running', 'completed', 'failed',
  #                      'interrupted', 'expired'))
  # 'expired' is a real, DB-enforced terminal state (follow-up correction,
  # 2026-08-10) — omitting it here would let an expired job silently keep
  # this script polling until timeout instead of failing immediately.
  if [ "$JOB_STATUS" = "failed" ] || [ "$JOB_STATUS" = "interrupted" ] || [ "$JOB_STATUS" = "expired" ]; then
    echo "[poll] FAILURE: bound job_id=${JOB_ID} reached terminal non-success status=${JOB_STATUS}." >&2
    exit 1
  fi

  if [ "$DERIVED_HEALTH" = "unresponsive" ]; then
    echo "[poll] FAILURE: bound job_id=${JOB_ID} heartbeat stale past backend's own unresponsive threshold (derived_job_health=unresponsive)." >&2
    exit 1
  fi

  if [ "$JOB_STATUS" = "completed" ]; then
    # Completion alone is NOT success — publication evidence is required
    # too (has_today true AND last_successful_generated_at is today's UTC
    # date, matching /api/picks/status's own has_today semantics).
    if [ "$HAS_TODAY" = "true" ] && [ -n "$LAST_SUCCESS_AT" ] && [ "$LAST_SUCCESS_AT" != "null" ]; then
      echo "[poll] SUCCESS: bound job_id=${JOB_ID} completed and has_today=true (fresh picks durably published) for market=${MARKET}."
      exit 0
    fi
    echo "[poll] FAILURE: bound job_id=${JOB_ID} reports job_status=completed but publication evidence (has_today/last_successful_generated_at) is absent or not for today — NOT reporting success." >&2
    exit 1
  fi

  sleep "$POLL_INTERVAL_SECS"
done

echo "[poll] FAILURE: polling window exhausted (${MAX_POLLS} x ${POLL_INTERVAL_SECS}s) without bound job_id=${JOB_ID} reaching a verified-published completed state." >&2
exit 1
