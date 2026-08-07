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