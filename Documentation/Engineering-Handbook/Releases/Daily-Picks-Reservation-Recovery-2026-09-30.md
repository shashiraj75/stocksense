# Daily Picks reservation recovery — 2026-09-30

## Incident and evidence

Production IN worker deployment `0dd672de-af96-409e-992b-8e21350c27af`
and US worker deployment `1966a607-f673-4975-8e57-b2e6cda15382` were
CRASHED. Runtime logs report `unexpected reservation outcome=started`
(IN: 2026-09-29 20:45:38 UTC; US: 2026-09-30 06:04:16 UTC).
Both run main commit `3928d8d72ab00cd2ebaacf03c78cda7303dc44a4`.
The IN status API reports last successful generation at
`2026-09-23T23:54:15.713951+00:00`, local generation date 2026-09-24,
and an interrupted attempt with no phase/progress. API health remains OK.

The atomic producer `_reserve_job_with_lease` returns `started` after
committing the job and resource lease. The worker and governed recovery
checked for `reserved`, rejected success, and never invoked generation.
The committed queued job then required stale-job reconciliation. The API
watchdog is intentionally disabled by external-worker-only mode; the old
GitHub generation/watchdog workflows are manual-only after migration.
Restarting the old worker image cannot fix this deterministic mismatch.

PR #119 contained the two-line fix but remained a draft with failing CI.
CI run 36562341575: 6530 passed, 370 skipped, one failure in the governed
recovery test, whose mock still returned the invalid `reserved` value.

## Changes and scope

- Worker and recovery accept the producer's `started` success outcome.
- Correct the old recovery test mock. The worker regression now obtains
  its outcome from the real atomic reservation body with an isolated fake
  connection, checks both markets, exact job identity, and lease release.
- Test resource contention, duplicate suppression, and failure-shaped
  or incomplete terminal states; they must not report successful publication.
- Add an independent read-only GitHub publication monitor at 11:30 UTC
  Monday–Friday, after both generation windows. Missing/stale publications
  or an unavailable status API fail the workflow. Zero-BUY successful days
  are accepted. Notification delivery follows repository Actions settings;
  no external messaging integration is added. This checks publication date,
  not individual stock price provenance, and never triggers heavy work.

Schedules remain IN 20:37 UTC Sun–Thu (02:07 IST Mon–Fri), US 06:00 UTC
Mon–Fri. No schema, scoring, provider, portfolio, frontend, or model change.
No historical picks are overwritten to make the incident appear recovered.
The existing last-good publication and subprocess/resource isolation remain.

## Verification and release gate

Targeted reservation/lifecycle/schedule tests: 123 passed before adding
the six terminal/lease cases. Reservation, monitor, recovery and India
session-freshness tests: 49 passed including those six cases.
Full local suite: 6541 passed, 366 skipped, one Yahoo network failure in
the unchanged market-leadership experiment test under the local sandbox
(269 seconds). That exact test passed on rerun with network access (1.58s).
Six additional terminal/lease cases were added after full-suite collection
and passed in the targeted run. GitHub CI on the final candidate and actual
production recovery are required before closure.
The prior unchanged frontend preview build was successful on `704b25b5`.

At 09:09 UTC Sep 30, the existing NSE calendar resolves Sep 29 as the
latest completed session. Validate the expected session again at recovery
time and inspect actual published reference dates; a new generation timestamp
alone does not establish fresh provider prices.

## Operational acceptance and rollback

Before release, verify no active generation or validation job. Merge only
after checks pass. Confirm the new main SHA on the API and both workers.
Run/observe the isolated IN worker, check durable completion and publication,
inspect reference dates, and check API health plus worker memory/logs.
An exit-zero cron container may be stopped normally; do not equate that
with a crash. Confirm a later scheduled execution separately.

If generation fails after reservation, preserve the last successful payload
and diagnose its terminal error. Do not re-enable heavy in-API generation.
The old image is known broken; rollback to it restores the outage, so prefer
a narrow corrective fix unless the release introduces a wider regression.

## Risks and outstanding evidence

The deterministic reservation crash occurs before data acquisition; it
does not establish that every downstream provider is healthy. Existing
provider freshness gates, DB publication checks and memory guards still
require a real run. Railway's stopped-worker metrics returned all-zero
samples and cannot prove peak memory usage. API memory was about 3.19 GB
at inspection, reinforcing the need to preserve isolated cron workers.
Natural scheduled-run verification is pending until a scheduled run is
observed on the corrected SHA. Do not claim full recovery from deployment alone.
