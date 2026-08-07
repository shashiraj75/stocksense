# Documentation Current-State Reconciliation Ledger (2026-08)

Mechanically generated from `git log --first-parent f178c9117dd54d17dd6054e64be90a5be4fb6ca9..origin/main` (authoritative origin/main at time of generation: `8f9693b0eecc5ba68490dee4d44b8ba86f5f079d`), cross-checked against `git log --full-history` for the same range (428 commits, reflecting per-PR-branch commit structure before squash-merge) to confirm no commit reachable from origin/main since baseline is dropped by first-parent-only traversal. Workstream tags are derived mechanically from commit subject keywords and changed top-level file paths. This table is the mechanical inventory; the narrative workstream deep-dives (current-state lifecycle classification, superseded/final-architecture calls) live in `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` and are cross-referenced, not duplicated, here.

| # | SHA | Date | Type | PR | Workstream | Areas Touched | Subject |
|---|---|---|---|---|---|---|---|
| 1 | `8e60a88900` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): add sector subtotal rows |
| 2 | `05ab65d23f` | 2026-07-11 | DIRECT |  | Portfolio | backend, frontend | fix(portfolio): improve table hierarchy and nonblocking signals |
| 3 | `b26796bd27` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): pin Symbol column and bound Sector Total between two lines |
| 4 | `1e8b2efbdc` | 2026-07-11 | DIRECT |  | Portfolio | backend | fix(portfolio): fall back to score_snapshots so Signal isn't "Not cached" all day |
| 5 | `8e11877121` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): self-heal stuck quote rows via refetchInterval |
| 6 | `9c1c803b1b` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): streamline signal summary and missing signal states |
| 7 | `38469befea` | 2026-07-11 | DIRECT |  | Portfolio | backend, frontend | fix(portfolio): normalize healthcare sector display |
| 8 | `1e94027164` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): refresh missing holding signals safely |
| 9 | `9492fb8008` | 2026-07-11 | DIRECT |  | Portfolio | frontend | fix(portfolio): merge sector heading into subtotal row |
| 10 | `272137e61c` | 2026-07-12 | DOC-ONLY |  | Daily Picks | Documentation | docs: record portfolio hotfix closure and daily picks hardening preflight |
| 11 | `b6690c907d` | 2026-07-12 | DIRECT |  | Multibagger | Documentation, frontend | fix(frontend): standardize horizon copy and clarify research screens |
| 12 | `ee59541ceb` | 2026-07-12 | DIRECT |  | Dashboard | frontend | fix(dashboard): remove horizon chips, defer Market Sentiment card |
| 13 | `c0c0dc04cf` | 2026-07-12 | DIRECT |  | Dashboard | frontend | fix(dashboard): compact quick access and mover cards |
| 14 | `050dc96180` | 2026-07-12 | DIRECT |  | Dashboard | frontend | fix(dashboard): compact market overview headings |
| 15 | `0e02d55ffb` | 2026-07-12 | DIRECT |  | Stock Detail | frontend | fix(stock): support safe intraday chart intervals for India |
| 16 | `299046c711` | 2026-07-12 | DIRECT |  | Validation Engine | backend, frontend | fix(stock-detail): prevent false signals on unavailable data |
| 17 | `5b8dd5c8b7` | 2026-07-12 | DIRECT |  | Stock Detail | frontend | fix(stock-detail): route unsupported symbols to dashboard |
| 18 | `536fd3d41f` | 2026-07-12 | DIRECT |  | Learning Alpha Engine | backend | fix(alpha-engine): preserve prediction market labels |
| 19 | `8fb23e8cb2` | 2026-07-12 | DIRECT |  | Learning Alpha Engine | backend | feat(alpha-engine): add shadow canonical observations |
| 20 | `f45ed1fe7f` | 2026-07-12 | DIRECT |  | Paper Trading | backend, frontend | fix(alpha-engine): contain legacy learning influence |
| 21 | `93363068cc` | 2026-07-13 | DIRECT |  | CI/Workflow | .github | chore(actions): add read-only secret sync check |
| 22 | `d1550f9696` | 2026-07-13 | DIRECT |  | CI/Workflow | .github | chore(actions): remove completed secret sync check |
| 23 | `e3404a327e` | 2026-07-13 | DIRECT |  | Validation Engine | backend | fix(universe): preserve symbol validation after refresh |
| 24 | `6b166f8615` | 2026-07-13 | DIRECT |  | Learning Alpha Engine | backend | fix(outcomes): support progressive horizon resolution |
| 25 | `e90a8f51f1` | 2026-07-13 | DIRECT |  | Learning Alpha Engine | backend | fix(outcomes): lock backfills to deterministic manifests |
| 26 | `36d4b33837` | 2026-07-14 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(market-integrity): harden outcome persistence and manifests |
| 27 | `bdc98f1141` | 2026-07-14 | DOC-ONLY |  | Documentation | Documentation | docs(phase-1a6): reconcile documentation with production migration and natural-run evidence |
| 28 | `0c4d331a45` | 2026-07-14 | DIRECT |  | Daily Picks | backend | fix(picks): fail-closed US premarket finalizer base-job provenance |
| 29 | `ea753d9d02` | 2026-07-15 | DIRECT |  | Daily Picks | Documentation, frontend | fix(picks): contain stale India price references |
| 30 | `90e03c3e9c` | 2026-07-15 | DIRECT |  | Daily Picks | frontend | fix(picks): block cross-market retained payloads |
| 31 | `7d52ac9c1a` | 2026-07-15 | DIRECT |  | Validation Engine | Documentation, frontend | fix(picks): withhold unverified performance metrics |
| 32 | `13c47be838` | 2026-07-15 | DIRECT |  | Validation Engine | Documentation, backend | fix(validation): route symbols by explicit market |
| 33 | `02ae2f4f0f` | 2026-07-15 | DIRECT |  | Daily Picks | .github, Documentation, backend, frontend | fix(picks): move US premarket finalizer to 6am ET |
| 34 | `861388ea25` | 2026-07-15 | DOC-ONLY |  | Documentation | CLAUDE.md, Documentation | docs(engineering): establish SES-006 end-to-end release standard |
| 35 | `39814bfe6f` | 2026-07-15 | DIRECT |  | Daily Picks | Documentation, backend, frontend | fix(picks): align US premarket schedule copy |
| 36 | `797b11a725` | 2026-07-15 | DIRECT |  | Multibagger | .github, Documentation, README.md, backend | fix(scheduling): prevent US workload overlap before premarket |
| 37 | `fbf27897c8` | 2026-07-16 | DIRECT |  | Multibagger | .github, Documentation, backend, frontend | fix(multibagger): move full refreshes to weekly lifecycle |
| 38 | `5ade20cd00` | 2026-07-16 | DIRECT |  | Multibagger | .github, Documentation, backend, frontend | fix(multibagger): finalize weekly refresh architecture |
| 39 | `ec3cef0f3f` | 2026-07-16 | DIRECT |  | Daily Picks | Documentation, backend | fix(picks): add India session-freshness backend gate |
| 40 | `b83b12ac99` | 2026-07-16 | DIRECT |  | NSE Instrument Master | Documentation, backend | fix(picks): add NSE bhavcopy last-resort price correction |
| 41 | `1687854fc7` | 2026-07-16 | DIRECT |  | Daily Picks | .github, Documentation, backend, frontend | fix(picks): move India Daily Picks to 2:07 AM IST, fix stale copy |
| 42 | `1beabc7642` | 2026-07-16 | DIRECT |  | Stock Detail | frontend | feat(stock): show Day Open on the stock detail quote card |
| 43 | `aed1768b04` | 2026-07-16 | DIRECT |  | Stock Detail | frontend | fix(stock): keep quote stat pills on one line |
| 44 | `378f91d681` | 2026-07-16 | DIRECT |  | Stock Detail | frontend | fix(stock): make the quote stat pill row's scroll affordance visible |
| 45 | `793fa9aa83` | 2026-07-16 | DIRECT |  | Stock Detail | frontend | fix(stock): abbreviate quote stat pill labels to reclaim width |
| 46 | `a965d4edbc` | 2026-07-16 | DIRECT |  | Stock Detail | frontend | fix(stock): use D- prefix for day-range pill labels instead of bare O/H/L |
| 47 | `645a7c86e1` | 2026-07-16 | DIRECT |  | Stock Detail | Documentation, frontend | fix(stock): fix 7 HIGH-severity findings from Stock Detail page audit |
| 48 | `1e3c62716e` | 2026-07-16 | DIRECT |  | Stock Detail | Documentation, frontend | fix(stock): fix 15 MEDIUM/LOW findings from Stock Detail page audit |
| 49 | `883b304399` | 2026-07-16 | DIRECT |  | Daily Picks | Documentation, backend, frontend | fix(score-history): add market scoping to score_snapshots |
| 50 | `58632dee92` | 2026-07-16 | DIRECT |  | Stock Detail | Documentation, frontend | fix(stock): remove duplicate Confidence display, warn on low-confidence trade levels |
| 51 | `970e0d64c5` | 2026-07-16 | DIRECT |  | Prediction Engine | Documentation, backend | fix(prediction): scale BUY/SELL target price floors by confidence |
| 52 | `fa0afe3d0e` | 2026-07-16 | DIRECT |  | News | Documentation, backend | fix(news): fix stale news feed and relevance-matching bugs |
| 53 | `0af3dbd0e2` | 2026-07-16 | DIRECT |  | SEC PIT Fundamentals | Documentation, backend | fix(sec-edgar): cap unbounded _facts_cache to prevent Railway OOM |
| 54 | `37cf15992b` | 2026-07-16 | DIRECT |  | Validation Engine | Documentation, frontend | feat(picks): add High Conviction Only (>=85%) filter to Picks page |
| 55 | `0ef83a734f` | 2026-07-16 | DIRECT |  | Paper Trading | Documentation, frontend | feat(paper-trade): add risk-based position sizing suggestion |
| 56 | `4a8a07ccfa` | 2026-07-16 | DOC-ONLY |  | Multibagger | Documentation | docs: reconcile stale pending-push status lines across all release docs |
| 57 | `637ef34984` | 2026-07-16 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(daily-picks): release per-horizon raw data after ranking, log adaptation timing |
| 58 | `b6d812e2b4` | 2026-07-16 | DIRECT |  | Paper Trading | Documentation, frontend | feat(paper-trading): add horizon summary strip and overdue-position flag |
| 59 | `07cad133eb` | 2026-07-16 | DIRECT |  | Paper Trading | frontend | fix(paper-trading): fold horizon summary strip back into the header row |
| 60 | `02920908e0` | 2026-07-16 | DIRECT |  | NSE Instrument Master | Documentation, backend | fix(postgres): route alpha_observations insert through a real cursor |
| 61 | `d022e2dc68` | 2026-07-17 | DIRECT |  | Daily Picks | frontend | feat(picks): show live generation-progress counter in header |
| 62 | `8c12a2abee` | 2026-07-17 | DIRECT |  | NSE Instrument Master | backend | fix(picks): surface why NSE bhavcopy correction silently failed |
| 63 | `d5bb8dd59b` | 2026-07-17 | DIRECT |  | Daily Picks | backend | fix(quote): key static company-name cache by (symbol, market) |
| 64 | `09b3aec20e` | 2026-07-17 | DIRECT |  | Daily Picks | backend | fix(picks): use last VALID close date for bhavcopy staleness check |
| 65 | `9568a9fc5d` | 2026-07-17 | DIRECT |  | Intelligence Engine/Universe Builder | backend | fix(universe): abort universe write if EITHER market's fetch is thin |
| 66 | `0453fc0180` | 2026-07-17 | DIRECT |  | Validation Engine | backend | feat(validation): add confidence-band hit-rate table (audit, additive-only) |
| 67 | `f068156b23` | 2026-07-17 | DIRECT |  | Validation Engine | backend | fix(confidence): rescale BUY confidence over empirical [60,80] range |
| 68 | `ffd8df7886` | 2026-07-17 | DIRECT |  | Daily Picks | backend | feat(picks): add automatic orphan-job recovery for Daily Picks jobs |
| 69 | `4922553fb2` | 2026-07-17 | DIRECT |  | Validation Engine | backend | feat(picks): connect confidence to real validated track record |
| 70 | `f7ba90aee2` | 2026-07-17 | DIRECT |  | Caching/Egress Containment | backend | fix(caches): cap 11 unbounded per-symbol caches to stop recurring OOMs |
| 71 | `8dc61340f5` | 2026-07-17 | DIRECT |  | Validation Engine | backend | fix(confidence): rescale SELL over its real range, investigate HOLD |
| 72 | `203250ca72` | 2026-07-17 | DIRECT |  | Daily Picks | backend | feat(alpha): make production-learning graduation criteria observable |
| 73 | `976c0d438c` | 2026-07-17 | DIRECT |  | Daily Picks | backend | fix(picks): add market filtering to GET /api/picks/performance |
| 74 | `85a55e12bb` | 2026-07-17 | DIRECT |  | Learning Alpha Engine | backend | fix(outcomes): populate real benchmark returns in the live resolver |
| 75 | `40b0a42d53` | 2026-07-17 | DIRECT |  | Validation Engine | frontend | feat(picks): surface historical track record on the Daily Picks page |
| 76 | `49ce493ba1` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(daily-picks): add implementation register and decision log |
| 77 | `f06ae2a612` | 2026-07-18 | DIRECT |  | Other/No-op | (none) | noop |
| 78 | `c06453fdab` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): establish canonical implementation register |
| 79 | `2d721bb38b` | 2026-07-18 | DIRECT |  | Daily Picks | Documentation, backend | fix(picks): preserve genuine zero factor scores |
| 80 | `08ba36e0e8` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-009 DP-010 deployment status |
| 81 | `e40dcdc84a` | 2026-07-18 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(regime): anchor cluster IDs to semantic labels |
| 82 | `614b226957` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-017 deployment status |
| 83 | `108b311381` | 2026-07-18 | DIRECT |  | Paper Trading | frontend | fix(paper-trading): prevent header row from overflowing viewport width |
| 84 | `54c4edd9d6` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile register DPD cross-references with decision log |
| 85 | `90462e1edd` | 2026-07-18 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(portfolio): preserve hard position caps, surface cash (DP-020) |
| 86 | `69053819eb` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-020 deployment status |
| 87 | `9cdfe29655` | 2026-07-18 | DIRECT |  | Validation Engine | Documentation, backend | feat(validation): add production-pipeline replay foundation |
| 88 | `55e4c4750c` | 2026-07-18 | DIRECT |  | Validation Engine | Documentation, backend | fix(validation): clarify replay scope and harden contracts |
| 89 | `9e63d51373` | 2026-07-18 | DIRECT |  | Validation Engine | Documentation, backend | fix(validation): model raw quality provenance correctly |
| 90 | `9378ec7b25` | 2026-07-18 | DIRECT |  | Validation Engine | Documentation, backend | fix(portfolio): enforce position cap for single pick |
| 91 | `bd552a1619` | 2026-07-18 | DOC-ONLY |  | Documentation | Documentation | docs(register): record DP-021 commit SHA and deployment status |
| 92 | `ee80144f78` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-021 deployment status |
| 93 | `09000ee467` | 2026-07-18 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(regime): bound regime features to semantic domain |
| 94 | `ef2f256e52` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-018 deployment status |
| 95 | `f9d10be8b0` | 2026-07-18 | DIRECT |  | Learning Alpha Engine | Documentation, backend | fix(regime): reject non-finite regime features |
| 96 | `d9c2c8252f` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-018 finite-value hardening |
| 97 | `0468a4c890` | 2026-07-18 | DIRECT |  | Validation Engine | Documentation, backend | feat(validation): add regime multiplier A/B comparator |
| 98 | `e6adb811d1` | 2026-07-18 | DOC-ONLY |  | Daily Picks | Documentation | docs(picks): reconcile DP-019 shadow deployment |
| 99 | `1b10b7c3c8` | 2026-07-18 | MERGE | 1 | NSE Instrument Master | backend | Merge pull request #1 from shashiraj75/phase-c0-instrument-master-foundation |
| 100 | `fd15a835d8` | 2026-07-19 | MERGE | 2 | NSE Instrument Master | backend | Merge pull request #2 from shashiraj75/phase-c1-d1-source-registry-offline-validators |
| 101 | `d475e7783a` | 2026-07-19 | MERGE | 3 | NSE Instrument Master | backend | Merge pull request #3 from shashiraj75/phase-c2-d2-consumer-approval-contracts |
| 102 | `89ca461cc2` | 2026-07-20 | MERGE | 4 | Multibagger | backend | Merge pull request #4 from shashiraj75/fix/job-lease-atomicity |
| 103 | `336ff9b638` | 2026-07-20 | MERGE | 5 | Postgres Schema Init | backend | Merge pull request #5 from shashiraj75/fix/postgres-multistatement-atomicity |
| 104 | `a148161e85` | 2026-07-21 | MERGE | 6 | NSE Instrument Master | backend | Merge pull request #6 from shashiraj75/fix/nse-registry-live-contracts |
| 105 | `a219054d41` | 2026-07-21 | MERGE | 8 | Daily Picks | backend | Merge pull request #8 from shashiraj75/fix/daily-picks-bounded-memory |
| 106 | `191f15188c` | 2026-07-21 | MERGE | 10 | Validation Engine | Documentation, backend, frontend | Merge pull request #10 from shashiraj75/fix/dp-026-point-in-time-fundamentals |
| 107 | `079477d704` | 2026-07-21 | MERGE | 11 | Documentation | Documentation | Merge pull request #11 from shashiraj75/docs/dp-026-production-closure |
| 108 | `a62c6ee782` | 2026-07-22 | MERGE | 12 | Validation Engine | Documentation, backend, frontend | Merge pull request #12 from shashiraj75/fix/dp-026-point-in-time-remediation |
| 109 | `92e4875c28` | 2026-07-22 | MERGE | 13 | Documentation | Documentation | Merge pull request #13 from shashiraj75/docs/dp-026-post-merge-verification |
| 110 | `13baa84788` | 2026-07-22 | MERGE | 14 | Daily Picks | Documentation, backend, frontend | Merge pull request #14 from shashiraj75/fix/us-daily-picks-generation-reliability |
| 111 | `82b32f6d97` | 2026-07-22 | MERGE | 15 | Daily Picks | backend | Merge pull request #15 from shashiraj75/fix/daily-picks-cache-status-migration-init-order |
| 112 | `74513eeb4f` | 2026-07-22 | MERGE | 16 | SEC PIT Fundamentals | backend | Merge pull request #16 from shashiraj75/fix/sec-edgar-facts-cache-oversized-cap |
| 113 | `3df79b1a92` | 2026-07-22 | MERGE | 17 | Daily Picks | Documentation | Merge pull request #17 from shashiraj75/docs/us-daily-picks-incident-closure |
| 114 | `612e558dbc` | 2026-07-22 | MERGE | 18 | Alerts UI | frontend | Merge pull request #18 from shashiraj75/feat/alerts-collapsible-add-form |
| 115 | `1c33ae309e` | 2026-07-22 | MERGE | 19 | Multibagger | frontend | Merge pull request #19 from shashiraj75/chore/remove-redundant-page-level-market-badge |
| 116 | `29539d6114` | 2026-07-24 | MERGE | 20 | Daily Picks | backend | Merge pull request #20 from shashiraj75/fix/us-daily-picks-memory-headroom |
| 117 | `37bfe39427` | 2026-07-25 | MERGE | 21 | Validation Engine | backend, frontend | Merge pull request #21 from shashiraj75/fix/validation-job-universe-identity |
| 118 | `67a1f13fb9` | 2026-07-25 | MERGE | 22 | Market Leadership | Documentation, backend, frontend | Merge pull request #22 from shashiraj75/feat/shadow-market-leadership-context |
| 119 | `37ac795e17` | 2026-07-25 | MERGE | 23 | Market Leadership | Documentation | Merge pull request #23 from shashiraj75/docs/reconcile-validation-leadership-release |
| 120 | `d83488f0fa` | 2026-07-26 | MERGE | 24 | Validation Engine | backend | Merge pull request #24 from shashiraj75/fix/validation-public-error-sanitization |
| 121 | `4851f8593c` | 2026-07-26 | MERGE | 25 | Validation Engine | Documentation | Merge pull request #25 from shashiraj75/docs/reconcile-validation-error-sanitization |
| 122 | `0c3926cc5f` | 2026-07-26 | MERGE | 26 | Validation Engine | backend | Merge pull request #26 from shashiraj75/fix/validation-benchmark-evidence-integrity |
| 123 | `ffb98787a0` | 2026-07-26 | MERGE | 27 | Validation Engine | Documentation | Merge pull request #27 from shashiraj75/docs/reconcile-validation-benchmark-evidence |
| 124 | `224738421a` | 2026-07-26 | MERGE | 28 | Validation Engine | backend | Merge pull request #28 from shashiraj75/fix/validation-benchmark-evidence-final-hardening |
| 125 | `4c721f1a9f` | 2026-07-26 | MERGE | 29 | Validation Engine | Documentation | Merge pull request #29 from shashiraj75/docs/reconcile-validation-benchmark-hardening |
| 126 | `226f596784` | 2026-07-27 | MERGE | 30 | Trade Postmortem | .github, backend | Merge pull request #30 from shashiraj75/feature/postgres-paper-trading-verification |
| 127 | `ba4d1101f8` | 2026-07-27 | MERGE | 31 | Postgres Schema Init | .github, backend | Merge pull request #31 from shashiraj75/feature/schema-init-fail-closed |
| 128 | `5bcd1a0956` | 2026-07-28 | MERGE | 32 | Trade Postmortem | Documentation, backend, frontend | Merge pull request #32 from shashiraj75/feature/complete-trade-postmortem |
| 129 | `a718726416` | 2026-07-28 | MERGE | 33 | Trade Postmortem | Documentation, backend | Merge pull request #33 from shashiraj75/feature/trade-postmortem-sprint2-durability |
| 130 | `3689d6ffd1` | 2026-08-03 | MERGE | 34 | Paper Trading | backend | Merge pull request #34 from shashiraj75/feature/postgres-paper-trading-verification |
| 131 | `588a4b0ed2` | 2026-08-03 | MERGE | 35 | Trade Postmortem | Documentation, backend, frontend | Merge pull request #35 from shashiraj75/feature/trade-postmortem-sprint3a-price-path |
| 132 | `5170692f27` | 2026-08-07 | MERGE | 36 | Trade Postmortem | Documentation, backend, frontend | Merge pull request #36 from shashiraj75/feature/postmortem-explainability-phase |
| 133 | `8f9693b0ee` | 2026-08-07 | DIRECT |  | Watchlist | frontend | fix(watchlist): URL-encode symbol in remove mutation DELETE request |

## Summary Totals

- TOTAL COMMITS (first-parent range, baseline exclusive to origin/main inclusive): 133
- MERGE COMMITS: 34
- DIRECT COMMITS (non-merge, first-parent): 99
- DOC/TEST-ONLY COMMITS (every changed path under `Documentation/` or `*.md`): 22
- FEATURE/FIX COMMITS (non-merge, touches at least one non-doc path): 84
- UNCLASSIFIED COMMITS: 0

Full-history traversal returns 428 commits (individual commits inside each PR branch before merge); the 133-row first-parent ledger is used for reconciliation because it matches how work actually landed on `main`. Every PR-branch commit inside the 34 merge commits above is accounted for by the corresponding merge entry and PR number; none is separately relitigated here to avoid double-counting.
## Per-Workstream Current-State Disposition (added 2026-08-07, second pass)

Every one of the 133 first-parent commits above carries a `Workstream` tag.
This section resolves each workstream to a **final current-state
classification**, grounded in a direct read of the code listed under
"Evidence" for each row (not commit messages or historical doc claims). Use
`git grep`/`Read` on the cited paths to re-verify independently. Lifecycle
labels are exactly the eight from the governing prompt.

| Workstream (commit count) | Classification | Evidence | Note |
|---|---|---|---|
| Daily Picks (31) | LIVE USER-FACING / LIVE BACKEND OPERATIONAL | `backend/services/daily_picks.py`, `.github/workflows/daily_picks_in.yml` (cron `37 20 * * 0-4` = 2:07 AM IST), `daily_picks_us.yml` (`0 6 * * 1-5`), `daily_picks_us_premarket.yml` (`0 10/11 * * 1-5`, DST-aware dual cron); `frontend/src/app/picks` | Final architecture per this ledger's 31 commits: orphan-job recovery, per-horizon memory release, bounded caches, India bhavcopy last-resort correction + session-freshness gate, US premarket finalizer with fail-closed base-job provenance, confidence rescaling tied to validated track record. All merged and on `main`. |
| Validation Engine (24) | LIVE BACKEND / OPERATIONAL | `backend/api/routers/validation.py`, `backend/services/validation_engine.py` | Job-identity/universe-routing hardening (`fix/validation-job-universe-identity`), public error sanitization (`fix/validation-public-error-sanitization`), benchmark-evidence integrity + hardening (2 PR pairs, each followed by a docs-reconcile PR), production-pipeline replay foundation. Router is included in `main.py` and reachable at `/api/validation`. |
| Learning Alpha Engine (11) | FEATURE-FLAGGED OFF (contained) | `backend/services/alpha_engine/containment.py` (`ENV_VAR = "LEARNING_ALPHA_PRODUCTION_ENABLED"`, unset by default), `backend/services/alpha_engine/ic_engine.py` | Regime-multiplier A/B comparator, finite-value/domain hardening, and provenance fixes all shipped, but every production code path is gated behind `LEARNING_ALPHA_PRODUCTION_ENABLED=1`, which is unset in this repo checkout and referenced only by tests (`test_containment.py`, `test_regime_multiplier_ab.py`, etc.) as a positive control. Do not overclaim: this codebase proves the containment gate exists and defaults closed, not that it is toggled on anywhere in production. |
| Stock Detail (10) | LIVE USER-FACING | `frontend/src/app/stock/[symbol]` | Sequential UX hardening pass (HIGH + MEDIUM/LOW audit-finding fixes, quote-pill layout, Day Open display, intraday interval safety for India, unsupported-symbol routing to dashboard). Route exists and is wired; no flag gate found. |
| Portfolio (9) | LIVE USER-FACING | `frontend/src/app/portfolio`, `backend/api/routers/portfolio.py` (included in `main.py`) | Table-hierarchy, sector-subtotal, and self-healing signal-refresh fixes. No flag gate. |
| Multibagger (7) | LIVE USER-FACING — final weekly-refresh architecture | `.github/workflows/multibagger_refresh.yml` (India, `cron: "30 21 * * 5"` = Sat 3:00 AM IST), `multibagger_refresh_us.yml` (US, `cron: "0 8 * * 0"` = Sun 3/4:00 AM ET); `backend/services/multibagger_schedule.py`'s `in_scheduled_day()` gate; `frontend/src/app/multibagger` | Commits `fix(multibagger): move full refreshes to weekly lifecycle` and `fix(multibagger): finalize weekly refresh architecture` are the terminal state — superseded any earlier nightly-refresh design. Both workflow YAMLs explicitly document GitHub Actions' scheduled-cron delivery as "best-effort ... observed [to be unreliable]" and separately note the backend accepts the scheduled call any time within the scheduled day via `in_scheduled_day()` — i.e. DESIGNED cron time and OBSERVED dispatch reliability are two different things; the backend is deliberately tolerant of GH Actions' delivery slop. |
| NSE Instrument Master (7) | FOUNDATION / UNINTEGRATED | `backend/services/instrument_master/` (source registry, offline validators, parser, consumer-approval contracts) | Every consumer of `services/instrument_master` found in this checkout is a test file or the standalone `scripts/instrument_master_offline_cli.py`. No `api/routers/*.py` or production `services/*.py` (daily_picks, prediction_engine, stock_universe) imports it. This is a real, tested foundation layer with no wired production consumer yet. |
| Paper Trading (6) | LIVE USER-FACING | `frontend/src/app/paper-trading`, `backend/api/routers/paper_trading.py` (included in `main.py`) | Header-row/horizon-summary layout fixes, risk-based position-sizing suggestion. Live and reachable. |
| Trade Postmortem (5, ledger scope only — full lifecycle spans PRs #30-#36, most of which land as single "MERGE" rows) | LIVE USER-FACING — RELEASE COMPLETE | See prior pass: `Current-Release-Status.md`, Evidence Coverage Matrix reconciliation (this PR). | Already fully reconciled in the first pass of this PR; not re-litigated here beyond confirming no new commits landed since (origin/main unchanged at `8f9693b0`). |
| Dashboard (3) | LIVE USER-FACING | `frontend/src/app/dashboard` | Layout compaction (mover cards, market-overview headings), horizon-chip removal / Market Sentiment card deferral. Cosmetic/UX only — no backend or flag change. Recorded per spec's "NO DELTA AFTER BASELINE" instruction: there IS a delta (3 UX commits), so that fallback does not apply here; it is LIVE USER-FACING with minor layout changes only. |
| Postgres Schema Init (2) | LIVE BACKEND / OPERATIONAL — fail-closed | `backend/services/postgres_store.py` (`fail-closed` design, confirmed by direct grep), `fix/postgres-multistatement-atomicity`, `fix/job-lease-atomicity` (PR #4/#5) | Schema initialization and job-lease/multi-statement atomicity commits are both merged; `postgres_store.py` today implements fail-closed semantics (verified by source read, not just commit message). |
| Market Leadership (2 direct + `feat/shadow-market-leadership-context`, `feat/market-leadership-trend-context`, and 3 reconciliation-docs PRs merged as part of PR #21-#23) | FEATURE-FLAGGED OFF / DEPLOYED DORMANT | `backend/services/market_leadership/configuration.py`: `engine_enabled()` reads `MARKET_LEADERSHIP_ENGINE_ENABLED`, `ui_enabled()` additionally requires `MARKET_LEADERSHIP_UI_ENABLED`, `scoring_enabled()` requires `MARKET_LEADERSHIP_SCORING_ENABLED` — all three default to unset/off (`os.getenv(...) == "1"`, no default `"1"` anywhere in source). Every setter of these three env vars in this checkout is a test file (`tests/integration/market_leadership/*`, `tests/regression/market_leadership/*`). `api/routers/leadership.py` exists and is included in `main.py`, but its own comment states the flag gate means "an unauthorized/disabled" response is the expected default behavior. No production caller sets these flags. Cannot be classified LIVE without a documented prior release record (none found) — classified DEPLOYED DORMANT (code shipped, route reachable, but gated off by default) rather than SHADOW, since there is no evidence of even a shadow-evaluation cron running it. |
| SEC EDGAR (live provider, `sec_edgar_adapter.py`) — the workstream this ledger tags "SEC PIT Fundamentals" for the 2 direct commits above is actually about this system, not the DP-033 point-in-time store below; see the corrected split immediately below | LIVE BACKEND / OPERATIONAL — feeds live US scoring | `backend/services/us_financial_strength_adapter.py` calls `services.sec_edgar_adapter.fetch_us_fundamentals_sec_edgar()` directly (live acquisition) and resolves through `us_provider_precedence.resolve_field()`; `backend/services/prediction_engine.py:759-916` calls `compute_us_financial_strength()` and feeds its `grade` into `_apply_financial_strength_adjustment()`, which adjusts `confidence` for US-market predictions. This is not shadow-only: live SEC EDGAR data measurably influences the live US confidence score. `_facts_cache` on `sec_edgar_adapter.py` was capped (PR #16, `fix/sec-edgar-facts-cache-oversized-cap`) as part of the OOM-containment sweep, confirming it runs in the live request/job path, not an offline-only script. |
| **SEC PIT store (DP-033 persisted point-in-time fact store, `backend/services/sec_pit_store.py`) — a genuinely separate system from the live SEC EDGAR provider row above; corrected 2026-08-07 after this reconciliation's first pass conflated the two** | VALIDATION / REPLAY ONLY — does NOT feed live scoring | Repo-wide grep of every caller/importer of `sec_pit_store`, `get_facts_as_of_replay`, `ingest_symbol`, `sec_pit_facts`, `sec_pit_symbol_registry`, `sec_pit_ingestion_runs` finds exactly one production consumer: `backend/services/validation_engine.py`'s `_get_fundamentals_as_of_replay()` (line ~1111), which calls only `sec_pit_store.get_facts_as_of_replay()` / `get_symbol_registry_entry()` — the function's own docstring states it is "genuinely acquisition-free" and "no code path here can reach `services.sec_edgar_adapter`'s live functions under any circumstance." `sec_pit_store.ingest_symbol()` (acquisition) is called only by the standalone `scripts/sec_pit_ingest.py` offline/administrative tool — never by `validation_engine.py` or any request/job path. Neither `prediction_engine.py` nor `daily_picks.py` imports `sec_pit_store` at all — confirmed by grep, zero hits. **Do not read the live SEC EDGAR provider row above as evidence for this row; they are different systems with different consumers.** |
| CI/Workflow (2) | LIVE BACKEND / OPERATIONAL | `.github/workflows/` | Read-only secret-sync check added then removed same week (`chore(actions): add`/`remove read-only secret sync check`) — net no-op on current `main`; not a live workflow today. |
| Watchlist (1 in first-parent range: `8f9693b0`, plus earlier watchlist work predating this ledger's scope) | LIVE USER-FACING | `frontend/src/app/watchlist`, `backend/api/routers/watchlist.py` | URL-encoding DELETE fix (`8f9693b0eecc5ba68490dee4d44b8ba86f5f079d`) is the tip of `origin/main` at time of this reconciliation — fixes a real bug (symbols/names with spaces or punctuation, e.g. "AUTO.CORP.OF GOA", failed to unwatch) by encoding the symbol path segment. Confirmed present in current `main.py` route wiring. |
| Alerts UI (1) | LIVE USER-FACING | `frontend/src/app/alerts`, `backend/api/routers/alerts.py` | Collapsible add-form UX only; no backend/flag change. |
| Intelligence Engine / Universe Builder (1 direct here; broader work spans `feat(alpha)`/`feat(picks)` graduation-observability commits tagged under other workstreams) | SHADOW / EXPERIMENTAL | `backend/services/daily_picks.py:1424-1431`: `if os.getenv("INTELLIGENCE_ENGINE_SHADOW_ENABLED") == "1"`, default off; `backend/services/postgres_store.py:406`: "Populated only when INTELLIGENCE_ENGINE_SHADOW_ENABLED=1 (default off)"; `backend/api/routers/picks.py:283` notes the flag "has never [been enabled]" as of that comment. | Genuinely shadow: writes to its own table only when explicitly enabled, never enabled by any non-test code path found in this checkout. |
| Caching/Egress Containment (1 direct here; the broader Sprint spans the 11-cache-cap commit `f7ba90aee2` plus the later "bounded TTL caching" fix already merged to `origin/main` per this repo's working-tree history) | LIVE BACKEND / OPERATIONAL | `f7ba90aee2d9ab221b4e9b796a3764925e268a91` caps 11 previously-unbounded per-symbol caches; `0af3dbd0e2` caps `sec_edgar_adapter._facts_cache`; `d5bb8dd59b` keys the static company-name cache by `(symbol, market)` to prevent cross-market collisions | All merged to `main`; this is production memory-safety hardening, not experimental. |
| News (1) | LIVE USER-FACING (backend service, no dedicated frontend route beyond existing stock/dashboard news feeds) | `fa0afe3d0e50d6cb1dc6f2469e37054caf7f313a` "fix(news): fix stale news feed and relevance-matching bugs" | Bug fix to an already-live feature; no lifecycle change. |
| Prediction Engine (1 direct here; target-price-floor scaling is the specific spec callout) | LIVE BACKEND / OPERATIONAL | `970e0d64c524d069261b5e3edccdb02e0f9be68c` "fix(prediction): scale BUY/SELL target price floors by confidence"; consumed by `backend/services/prediction_engine.py`, surfaced via `backend/api/routers/predictions.py` (`/api/predictions`) and rendered on `frontend/src/app/stock/[symbol]` | Confirmed live: the router is included in `main.py`, and the frontend Stock Detail page is the actual consumer of predicted target prices. |
| RCI | NO UPDATE REQUIRED | `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` lines ~420-450 | No RCI-tagged commit exists in this ledger's 133-row range; `RCI_LIVE_STOCK_ANALYSIS_ENABLED` is already accurately documented there as disabled, with counter-only observability deployed. Confirmed by grep of the doc and `backend/services/` — no code or doc change required. |
| Landing page (root `/`) | NO DELTA AFTER BASELINE | `frontend/src/app` (no `page.tsx`-level commits at the root route found in this ledger's Dashboard/Stock Detail/Portfolio-tagged rows) | Distinct from the Dashboard workstream above (`/dashboard`), which did receive layout commits; the bare landing/auth-gate route itself has no post-baseline commits in this ledger. |
| Global market-selector badge cleanup | Folded into Stock Detail / Portfolio / Dashboard rows above | `chore/remove-redundant-page-level-market-badge` (merged as PR #19, `1c33ae309e`) | Single-purpose chore PR removing a redundant per-page badge once a global one existed; classified LIVE USER-FACING (cosmetic cleanup of an already-shipped element), fully captured by the ledger's row 19/`chore/remove-redundant-page-level-market-badge` merge entry — not a distinct ongoing workstream. |

**Zero-unclassified confirmation:** every commit in the 133-row ledger carries
one of the `Workstream` values enumerated in the table above (or the
Documentation/CI/No-op catch-alls, which are inherently terminal —
doc-reconciliation and CI-only changes do not require a lifecycle
classification of a product surface). Cross-referencing the ledger's
`Workstream` column against this table's left column accounts for all 133
rows with no residual "Other/Unclassified" tag.

## Cross-Cutting Inventories (added 2026-08-07, second pass)

### Feature flags (repo-wide grep, `os.getenv`/`environ.get` over `backend/` excluding tests, plus `NEXT_PUBLIC_*` in `frontend/src/utils/featureFlags.ts`)

| Flag | Side | Purpose | Default | Fail-open/closed | Lifecycle |
|---|---|---|---|---|---|
| `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` / `NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED` | Backend + Frontend | Gates per-trade `/postmortem/[tradeId]` route and `GET .../current-report` (PR #35/#36 release) | unset (off) | fail-closed | LIVE USER-FACING — reported enabled in Production per `Trade-Postmortem-Explainability-Production-Closure.md`'s own validation evidence (48hr stability observation, natural lifecycle verification on trade 280, frontend activation, authenticated smoke test); production toggle state itself is sourced from that closure doc's record, not independently re-derived from a repo checkout |
| `TRADE_POSTMORTEM_DAILY_ENABLED` / `NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED` | Backend + Frontend | Gates a separate, older Sprint 1 daily-batch postmortem surface (`isTradePostmortemDailyEnabled()`, `frontend/src/utils/featureFlags.ts`) — not the same feature as the row above | unset (off), fail-closed by design | fail-closed | Repo default is disabled; **Production runtime state not independently verified during this reconciliation** — no closure document in this repo makes a specific claim about this pair's Production toggle state, so none is asserted here |
| `MARKET_LEADERSHIP_ENGINE_ENABLED` | Backend | Master gate for the leadership engine | unset (off) | fail-closed | FEATURE-FLAGGED OFF |
| `MARKET_LEADERSHIP_UI_ENABLED` | Backend | Gates `api/routers/leadership.py` responses (requires engine also enabled) | unset (off) | fail-closed | FEATURE-FLAGGED OFF |
| `MARKET_LEADERSHIP_SCORING_ENABLED` | Backend | Gates scoring influence; enforced to `services/` grep-only via a regression test | unset (off) | fail-closed | FEATURE-FLAGGED OFF |
| `MARKET_LEADERSHIP_SHADOW_ENABLED` / `MARKET_LEADERSHIP_VALIDATION_ENABLED` | Backend | Shadow-eval / validation-only paths | unset (off) | fail-closed | SHADOW / EXPERIMENTAL |
| `LEARNING_ALPHA_PRODUCTION_ENABLED` | Backend | Master containment gate, `services/alpha_engine/containment.py` | unset (off) | fail-closed | FEATURE-FLAGGED OFF (contained) |
| `INTELLIGENCE_ENGINE_SHADOW_ENABLED` | Backend | Gates shadow-only universe/ranking observations, `daily_picks.py` + `postgres_store.py` | unset (off) | fail-closed | SHADOW / EXPERIMENTAL |
| `DAILY_PICKS_STARTUP_CATCHUP_ENABLED` | Backend | Startup catch-up run for missed Daily Picks jobs | not independently re-derived this pass (present, purpose per name) | n/a | LIVE BACKEND/OPERATIONAL (part of the reliability hardening in the 31-commit Daily Picks workstream) |
| `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN` / `..._US` | Backend | Per-market gate for valuation-intelligence confidence contribution | not independently re-derived this pass | n/a | LIVE BACKEND/OPERATIONAL (consumed by prediction_engine per-market) |
| `ENABLE_FINNHUB_FOR_IN` | Backend | Enables Finnhub as an India-market data source | not independently re-derived this pass | n/a | LIVE BACKEND/OPERATIONAL or FALLBACK (name implies opt-in secondary provider) |
| `VERCEL_PREVIEW_ORIGIN_REGEX` | Backend | Optional CORS allowance for a project-scoped Vercel preview pattern | unset (no preview CORS) | fail-closed | Confirms the "temporary Postmortem Preview CORS...removed" claim in Current-Release-Status.md — no hardcoded preview allowance remains in `backend/api/main.py`. |
| `USE_POSTGRES` | Backend | Postgres vs SQLite backing store selector | not independently re-derived this pass | n/a | LIVE BACKEND/OPERATIONAL |
| `PRICE_ALERTS_ENFORCEMENT` | Backend | Alerts enforcement toggle | not independently re-derived this pass | n/a | LIVE BACKEND/OPERATIONAL (Alerts UI is live; this flag's own current value not independently verified) |

Production runtime state (which of these are actually toggled on in Railway/Vercel right now) is stated only where an existing closure doc (e.g. Trade Postmortem's) makes that claim explicitly; otherwise this table reports only what the code's *default* does, per the governing prompt's Step 5 instruction not to infer production state from code defaults.

### API inventory delta (FastAPI routers, `backend/api/routers/` baseline vs current)

`git diff --name-status` between baseline and origin/main shows **no added or removed router files** — `alerts.py, auth.py, backtest.py, feedback.py, leadership.py, multibagger.py, news.py, paper_trading.py, picks.py, portfolio.py, predictions.py, screener.py, stocks.py, validation.py, watchlist.py` are the same 15 router modules at both points. All 15 are `include_router`'d in `backend/api/main.py`. This means every API surface change since baseline is an *endpoint-level* change inside an existing router (e.g. `fix(picks): add market filtering to GET /api/picks/performance`), not a new top-level API area — those are captured in the per-workstream table above, not re-enumerated endpoint-by-endpoint here due to time budget in this pass.

### Frontend route inventory delta (`frontend/src/app/` baseline vs current)

`git ls-tree -d` diff between baseline and origin/main for `frontend/src/app` is **empty** — zero new or removed top-level route directories since the 11-Jul-2026 baseline. Every frontend commit in the ledger modified pages/components inside an already-existing route (Stock Detail, Portfolio, Dashboard, Multibagger, Paper Trading, Postmortem, Watchlist, Alerts, Picks, Validation).

### Persistence / migrations delta

`backend/scripts/migrations/` did not exist at baseline; it now contains one migration, `phase_1a6_drop_predictions_market_default.sql`, corresponding to the ledger's `docs(phase-1a6): reconcile documentation with production migration and natural-run evidence` entry. No other schema/migration files were added in this range per `git ls-tree` diff of that directory. (Table/column/index/trigger-level diffing of `postgres_store.py`'s inline DDL was not separately re-derived this pass beyond the fail-closed classification already confirmed above.)

### GitHub Actions workflow delta (`.github/workflows/` baseline vs current)

`git ls-tree -d` diff shows the **same 9 workflow files** at baseline and current (`backend_postgres_integration.yml, backend_tests.yml, daily_picks_in.yml, daily_picks_us.yml, daily_picks_us_premarket.yml, frontend_tests.yml, keep_alive.yml, multibagger_refresh.yml, multibagger_refresh_us.yml`) — no workflow file added or removed net of the ledger's add-then-remove `chore(actions)` secret-sync-check pair. Designed cron times, confirmed by direct read: India Daily Picks `37 20 * * 0-4` UTC (2:07 AM IST, Mon-Fri IST calendar via Sun-Thu UTC), US Daily Picks `0 6 * * 1-5` UTC, US premarket finalizer dual cron `0 10/11 * * 1-5` UTC (DST-aware), India Multibagger `30 21 * * 5` UTC (Sat 3:00 AM IST), US Multibagger `0 8 * * 0` UTC (Sun 3/4:00 AM ET). Both Multibagger workflows' own comments explicitly document that GitHub Actions' scheduled-dispatch delivery is "best-effort" and "has been observed" to be unreliable, separately from the designed cron time — the backend's `in_scheduled_day()` gate is the documented mitigation, accepting the scheduled call any time within the intended day rather than requiring exact-minute delivery. This distinction (designed vs. observed) is not re-litigated with fresh dispatch-reliability data in this pass; it is reported as already documented in the workflow files themselves.

### Data-provider role inventory (not exhaustively re-derived this pass; high-confidence classifications only, grounded in code read above)

| Provider | Role | Evidence |
|---|---|---|
| yfinance | PRODUCTION PRIMARY/FALLBACK (per-field, resolved via `us_provider_precedence.resolve_field()`) | `services/us_financial_strength_adapter.py` fetches yfinance alongside SEC EDGAR for the same 16 unified fields; precedence resolution decides which wins per field |
| SEC EDGAR (via `sec_edgar_adapter.py`, live acquisition) | LIVE PROVIDER INPUT to US financial-strength scoring | `prediction_engine.py:759-916` calls `compute_us_financial_strength()`, which is fed by `sec_edgar_adapter.fetch_us_fundamentals_sec_edgar()`; confidence adjustment is applied for US-market predictions |
| SEC PIT store (DP-033, `sec_pit_store.py`, persisted point-in-time facts) | VALIDATION/REPLAY ONLY | Sole production consumer is `validation_engine.py`'s `_get_fundamentals_as_of_replay()`, acquisition-free by design; ingestion (`ingest_symbol()`) runs only via the standalone `scripts/sec_pit_ingest.py`. Not imported by `prediction_engine.py` or `daily_picks.py` — does not influence live scoring. |
| screener.in (via `services/screener_data.py`) | PRODUCTION PRIMARY (India fundamentals) | Direct module presence + `SCREENER_EMAIL`/`SCREENER_PASSWORD`/`SCREEN_BATCH_SIZE` env vars found in the repo-wide flag grep above, implying authenticated production scraping, not an offline-only script |
| NSE (`nse_client.py`) / BSE (`bse_data.py`) | PRODUCTION PRIMARY (India quotes/bhavcopy) | Direct consumers of the India Daily Picks bhavcopy-correction and session-freshness-gate commits in this ledger |
| Finnhub | FALLBACK, opt-in (`ENABLE_FINNHUB_FOR_IN`) | Flag name and default-off convention consistent with other opt-in secondary sources in this codebase; not independently traced to a call site this pass |
| NSE Instrument Master's own source registry | FOUNDATION ONLY | See workstream table above — no production consumer found |

Full exhaustive per-endpoint API delta, per-table persistence delta, and complete data-provider inventory (news/benchmark/valuation sources not listed above) were not separately re-derived line-by-line in this pass beyond what is reported here; this is stated as a known limitation below rather than asserted as complete.

## Corrected and Deepened Inventories (added 2026-08-07, third pass)

This section supersedes the "directional" API/persistence/provider
inventories from the second pass with content-level, not filename-level,
diffing.

### API delta — endpoint-level (`git diff` of router file contents, baseline vs `origin/main`, `+` lines matching `@router.(get|post|put|patch|delete)`)

New endpoints added since baseline:

| Method | Path | Router | Auth | Mutation/Read | Feature gate |
|---|---|---|---|---|---|
| GET | `/api/predictions/signals/cached-batch` | predictions.py | authenticated (per existing router pattern) | Read | none found |
| GET | `/api/context` (leadership router) | leadership.py | per `MARKET_LEADERSHIP_UI_ENABLED` gate | Read | `MARKET_LEADERSHIP_UI_ENABLED` (+ `ENGINE_ENABLED`) |
| PATCH | `/api/paper-trading/trade/{trade_id}` | paper_trading.py | authenticated, user-scoped | Mutation | none found beyond standard auth |
| GET | `/api/paper-trading/postmortem/daily` | paper_trading.py | authenticated | Read | `TRADE_POSTMORTEM_DAILY_ENABLED` |
| GET | `/api/paper-trading/postmortem/{trade_id}` | paper_trading.py | authenticated, user-scoped | Read | `TRADE_POSTMORTEM_DAILY_ENABLED` (legacy Sprint 1 per-trade path, distinct from `current-report` below) |
| GET | `/api/paper-trading/{trade_id}/current-report` | paper_trading.py | authenticated, user-scoped | Read-only, no write/backfill | `TRADE_POSTMORTEM_PRICE_PATH_ENABLED` |
| POST | `/api/paper-trading/postmortem/{trade_id}/generate` | paper_trading.py | authenticated, user-scoped | Mutation (generates/persists a report) | `TRADE_POSTMORTEM_DAILY_ENABLED` path (per file's own naming convention — not independently re-verified line-by-line which flag guards this specific handler beyond the router's dominant Sprint 1 naming) |

`picks.py` (+199/-42), `multibagger.py` (+237/-21), `validation.py`
(+23/-6), and `stocks.py` (+17/-3) show substantial content diffs with
**zero new route decorators** — confirming these are materially changed
existing-endpoint response bodies/query handling, not new API surface.
Concretely identified from commit subjects (not re-derived line-by-line for
every parameter): `GET /api/picks/performance` gained market filtering
(commit `976c0d4`, "add market filtering to GET /api/picks/performance").
The remaining handler-level changes in these four files were not
individually enumerated parameter-by-parameter in this pass — reported as
a known limitation rather than claimed complete.

`portfolio.py`, `watchlist.py`, and `alerts.py` show **zero diff** at the
router-file level despite ledger commits tagged to those workstreams — this
is not an omission: the Watchlist DELETE-encoding fix (`8f9693b0`) and the
Alerts collapsible-form change are both frontend-only (confirmed by
`git show --stat` on each commit: `8f9693b0` touches only
`frontend/src/`, the Alerts PR touches only
`frontend/src/app/alerts/page.tsx`). No backend route changed for either.

### Persistence delta — actual DDL (inline schema-init code in `backend/services/postgres_store.py`, `git diff` baseline vs `origin/main`)

New tables: `paper_trade_entry_snapshot`, `paper_trade_exit_snapshot`,
`paper_trade_postmortem_outbox`, `paper_trade_postmortem_report`,
`alpha_observations`, `multibagger_refresh_jobs`, `heavy_workload_leases`,
`multibagger_staging`.

New columns (selected, not exhaustive): `predictions.market`,
`outcomes.market` (both `NOT NULL`), `score_snapshots.market`,
`paper_trades.recommendation_source` /
`daily_pick_run_id` / `daily_pick_rank` / `recommendation_generated_at` /
`recommendation_reference_price` / `recommendation_entry_low` /
`recommendation_entry_high` / `recommendation_original_stop_loss` /
`recommendation_original_target` / `model_version` /
`execution_slippage_pct` / `signal_override` /
`levels_modified_after_entry` / `level_history_contract_version` /
`stop_modified_after_entry` / `target_modified_after_entry`.

New indexes (selected): `idx_score_snapshots_symbol_market`,
`idx_paper_trade_entry_snapshot_user`, `idx_paper_trade_exit_snapshot_user`,
`idx_paper_trade_pm_outbox_claim`, `idx_paper_trade_pm_outbox_user`,
`idx_paper_trade_pm_report_user`, `idx_paper_trade_pm_report_status`,
`idx_paper_trade_pm_report_market_date`,
`idx_alpha_observations_market_horizon_session`,
`idx_alpha_observations_run`, `idx_alpha_observations_symbol_market_horizon`,
`idx_heavy_workload_leases_owner`, `idx_multibagger_staging_job`,
`idx_daily_picks_cache_market_status`.

New constraints: `chk_paper_trades_level_history_contract_version`,
`chk_paper_trades_governed_level_history_tuple`.

`heavy_workload_leases` is the job-lease table backing the
`fix/job-lease-atomicity` (PR #4) and orphan-job-recovery work in the Daily
Picks workstream. This DDL-level pass covers `postgres_store.py`'s inline
schema only — it does not separately re-verify every table against the
`sec_pit_store.py`, `instrument_master`, or `market_leadership` modules'
own possibly-separate schema-init code in this pass; noted as a limitation.

### Data-provider role inventory — corrected, call-site-traced (not env-var-name-inferred)

| Provider | Classification | Call-site evidence |
|---|---|---|
| yfinance | PRODUCTION PRIMARY/FALLBACK (per-field via `us_provider_precedence.resolve_field()`) | `us_financial_strength_adapter.py` fetches yfinance alongside SEC EDGAR for the same 16 fields |
| SEC EDGAR (live, `sec_edgar_adapter.py`) | LIVE PROVIDER INPUT | `prediction_engine.py:759-916` → `us_financial_strength_adapter.py` → `sec_edgar_adapter.fetch_us_fundamentals_sec_edgar()`; feeds live US confidence adjustment |
| SEC PIT store (DP-033, `sec_pit_store.py`) | VALIDATION/REPLAY ONLY | Sole consumer `validation_engine.py`'s acquisition-free replay path; ingestion only via standalone `scripts/sec_pit_ingest.py`; not imported by `prediction_engine.py`/`daily_picks.py` — corrected from the second pass, which incorrectly conflated this with the SEC EDGAR row above |
| screener.in (`screener_data.py`) | PRODUCTION PRIMARY (India fundamentals) | Authenticated production scraping (`SCREENER_EMAIL`/`SCREENER_PASSWORD`/`SCREEN_BATCH_SIZE`), consumed by the India Daily Picks/Multibagger fundamentals-refresh path |
| NSE (`nse_client.py`) | PRODUCTION PRIMARY (India quotes/bhavcopy) | Direct consumer of the India Daily Picks bhavcopy-correction and session-freshness-gate commits |
| BSE (`bse_data.py`) | PRODUCTION FALLBACK (India), not independently re-traced to a specific call site this pass beyond module presence | — |
| Finnhub | PRODUCTION FALLBACK, opt-in (`ENABLE_FINNHUB_FOR_IN`) | Flag-gated; exact call site not re-traced this pass |
| NSE Instrument Master's source registry | FOUNDATION ONLY | No production consumer (see workstream table above) |
| Market Leadership's own data sources | Not independently traced this pass — the engine itself is FEATURE-FLAGGED OFF/DEPLOYED DORMANT, so whatever sources it reads are gated behind the same off-by-default flags | — |
| News/RSS sources | Not independently re-traced to specific call sites this pass; `fa0afe3d0e` ("fix stale news feed") confirms a live, already-shipped news feed exists and is maintained, consistent with LIVE USER-FACING | — |
| Benchmark/index data (for price-path contradictions, MFE/MAE) | Confirmed ABSENT as a distinct acquisition path — the Evidence Matrix's own JSON explicitly states "no benchmark acquisition exists" for every `CONTRADICTIONS`-section factor requiring it (see the Evidence Coverage Matrix's `D. REQUIRES_NEW_ACQUISITION` rows) | `postmortem_evidence_coverage_matrix.json` |

**Known limitation, stated honestly:** BSE, Finnhub, and Market Leadership's
own upstream data sources were not traced to their exact call sites with
the same rigor as yfinance/SEC EDGAR/SEC PIT/screener.in/NSE in this pass —
their classifications above are lower-confidence than the others and are
marked as such rather than asserted with the same certainty.
