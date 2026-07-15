# SES-006 — End-to-End Implementation and Release Standard

**Status:** Active — governing.
**Applies to:** material StockSense360 implementation work — see §2 for the exact boundary.
**Specializes:** SES-001. References SES-002 (Python), SES-003 (Testing), SES-004 (Documentation), SES-005 (Branding) rather than duplicating them.
**Version:** 1.0

---

## 1. Purpose

SES-006 governs the **complete lifecycle** of material StockSense360 implementation work — from investigation through production verification — as normally delivered through **one complete, end-to-end prompt**, not a chain of partial follow-ups discovered one gap at a time. SES-001 through SES-005 govern *how* code, tests, and documentation are written; SES-006 governs *what a complete piece of work must cover before it is represented as done*.

This standard exists because the individually-correct disciplines SES-001–005 already require (scope discipline, evidence over assertion, testing categories, documentation structure, correct branding) do not by themselves guarantee that a change's full footprint — backend, frontend, API/schema, workflows, database, user-facing copy, tests, documentation, and production verification — was considered *together*, in one pass, before the work is called complete. SES-006 closes that gap.

## 2. Applicability

SES-006 applies **by default** to:

- bug fixes;
- features;
- production logic changes;
- frontend changes;
- backend changes;
- API changes;
- scheduler and workflow changes;
- database and persistence changes;
- user-facing copy changes;
- material documentation-driven behavior changes.

**Narrower prompts remain valid** for:

- read-only investigation;
- evidence collection;
- design study (an SSDS-001/002-style specification with no coding authorized);
- documentation-only correction;
- production incident triage;
- an explicitly authorized one-step operational action (e.g. a single approved database transaction, a single approved push).

Even for excluded work, ordinary scope and safety rules (SES-001, and this repository's standing safety rules on destructive/irreversible actions) still apply in full — exclusion from SES-006 is not exclusion from care.

## 3. Senior Programmer Role

For work SES-006 governs, act simultaneously as:

- senior software engineer;
- systems architect;
- production reliability engineer;
- forensic reviewer;
- test engineer;
- release manager;
- technical-documentation owner.

This means accountability for correctness, backward compatibility, decision integrity (no scoring/ranking/confidence logic silently altered), production safety, user-facing consistency, complete testing, and evidence-based release verification — not just for writing code that runs.

## 4. Investigation Before Modification

Before writing a line of implementation code:

- inspect the complete execution path the change touches, not just the function being edited;
- inspect callers and consumers of anything being changed;
- identify frontend/backend/API/database/workflow dependencies;
- review relevant existing tests and documentation for the area;
- identify protected files and unrelated local changes (see §12, and this repository's standing instruction to preserve unrelated staged/modified/untracked items exactly);
- do not code from assumptions — if a fact about the codebase's current behavior matters to the change, verify it (SES-001 §3, evidence over assertion) rather than infer it.

## 5. Prompt Coverage Declaration

Every future implementation prompt governed by SES-006 must begin with a visible declaration stating, at minimum:

- standard identifier and version (`SES-006`, the version in effect);
- Implementation: included/excluded;
- Backend compatibility: included/excluded;
- Frontend consistency: included/excluded;
- Repository-wide consistency audit: included/excluded;
- Tests: included/excluded;
- Documentation: included/excluded;
- Final Release Review: included/excluded;
- Commit: included/excluded;
- Push: included/excluded;
- Deployment: included/excluded;
- Production verification: included/excluded;
- Natural-run verification: included/not applicable;
- Safety boundary, if any (see §18).

**Any excluded item must carry a written reason** — "excluded" with no reason is not a valid declaration under this standard. See §5's companion template, [`StockSense360-End-to-End-Implementation-Prompt-Template.md`](../Templates/StockSense360-End-to-End-Implementation-Prompt-Template.md).

## 6. Repository-Wide Impact Audit

Before implementation, search the repository for every reference to what the change affects:

- affected functions;
- API routes;
- schemas;
- TypeScript/Python types;
- frontend consumers;
- shared utilities;
- configuration;
- workflows;
- documentation;
- terminology;
- headings;
- labels;
- badges;
- tooltips;
- loading, empty, success, and error states;
- tests and fixtures.

Classify **every match** as one of:

1. active and affected;
2. active but unaffected;
3. valid historical evidence;
4. deliberate compatibility fixture;
5. unrelated.

Update only matches classified 1. Do not touch 2-5 — see §11 on preserving historical evidence, matching the discipline already used across this repository's Product Integrity workstreams.

## 7. Full-Stack Consistency

Review, where applicable to the change:

- backend services;
- routers;
- database models;
- persistence paths;
- frontend pages;
- shared components;
- API clients;
- types;
- landing page;
- dashboard;
- Daily Picks;
- stock detail;
- paper trading;
- alerts;
- screener;
- heatmap;
- portfolio surfaces;
- responsive/mobile behavior;
- accessibility;
- user-local timestamps and timezone behavior.

**Do not require unrelated files to change merely because they exist on this list** — this is a review checklist, not a mandate to touch every surface on every change. A change to one page's copy does not require editing the heatmap.

## 8. Decision and Data Integrity

Every change governed by SES-006 must preserve, unless the change is explicitly and narrowly scoped to alter one of these (a rare, high-scrutiny case):

- India/US market separation;
- price and timestamp provenance;
- fail-closed behavior;
- legacy-payload compatibility;
- missing-data truthfulness (never fabricate a value the codebase doesn't actually have);
- Phase 1A and Phase 1A.3 safeguards;
- GPI-0 or any other active integrity hold;
- database consistency;
- source-job provenance;
- exact-market routing;
- provider and benchmark semantics.

## 9. Testing Standard

SES-003 governs test structure, categories, and hygiene — this section adds what SES-006-governed work must additionally confirm is covered where applicable:

- focused unit tests;
- regression tests;
- integration tests;
- API tests;
- frontend component/page tests;
- workflow/config parsing tests;
- timezone and DST tests;
- legacy-data tests;
- failure-path tests;
- the full backend suite;
- frontend typecheck;
- frontend production build;
- no live-provider calls from tests;
- no production writes from tests.

## 10. Documentation Standard

SES-004 governs where documents live and what they contain — this section adds what SES-006-governed work must additionally produce:

- current documentation updated to match the new behavior;
- architecture/contract documents updated when the change affects them;
- release documentation created or updated (per SES-004 §3/§4's required sections);
- rollback instructions;
- known limitations, stated explicitly;
- explicit non-claims (what this change does *not* fix, so it can't be mistaken for having fixed it);
- historical incident evidence preserved accurately, never rewritten to erase what actually happened (see §11);
- the handbook index (`INDEX.md`) updated when a new governing document is added, per SES-004 §7.

## 11. Final Consistency Audit

Repeat §6's repository-wide search **after** implementation. Every remaining old or superseded reference must be explained as one of: historical evidence, deliberate fixture, compatibility requirement, or unrelated. **No unexplained active stale reference may remain.** A remaining match with no classification is a failed audit, not an acceptable residual.

## 12. Final Release Review

Before committing, show:

- `git status --short`;
- `git diff --name-only`;
- `git diff --stat`;
- `git diff --cached --name-only`;
- a classification of every changed file (core to this change / conditionally-permitted-and-justified / must not be here);
- targeted-test results;
- full regression results;
- frontend typecheck/build results;
- generated-artifact cleanup (no stray caches, local DB files, or test-run byproducts staged);
- protected-file proof (unrelated pre-existing modified/staged/untracked files remain byte-identical);
- scope compliance (nothing outside the declared coverage changed);
- production-safety state (is anything currently active that a deploy would interrupt).

**No commit may occur before this review passes.**

## 13. Commit Hygiene

Reference SES-001 §5 for the general commit-hygiene rules (logical units, explicit behavior-change statement, no `--no-verify`). SES-006 adds:

- explicit path-based staging (`git add <exact paths>`), never `git add .` or `git add -A`;
- no unrelated files in the commit;
- no amendment of another commit unless explicitly authorized;
- a commit message stating what changed and why;
- final staged-file proof (`git diff --cached --name-only` shown after staging, before committing).

## 14. Production Safety Gate

Before push/deployment, run read-only checks appropriate to the change, which may include:

- `/health`;
- validation status;
- India generation status;
- US generation status;
- backfill state;
- resolver/outcome state;
- active Railway deployments;
- Railway memory and process stability.

If any check indicates an active operation the deploy would interrupt, or an unhealthy/unstable state: the commit remains local, push/deployment stops, and the exact blocker is reported — never silently retried, never overridden.

## 15. Push and Deployment

- push only the reviewed, approved commit;
- allow the normal Git-triggered deployment (Railway/Vercel) to run — do not manually trigger it;
- no manual restart or redeploy unless separately, explicitly authorized;
- verify the exact deployed SHA matches the pushed commit;
- verify GitHub commit status checks;
- report Railway and Vercel status accurately, including when either reports something unexpected;
- if deployment fails, make an explicit rollback decision and state it — do not leave the state ambiguous.

## 16. Post-Deployment Verification

- health check;
- startup/runtime log review;
- memory/restart check;
- affected API smoke tests (read-only);
- affected frontend page verification;
- cross-market checks where the change could plausibly cross India/US boundaries;
- confirmation no unintended job was triggered by the deploy itself;
- confirmation no unrelated data drifted;
- confirmation any active integrity hold (e.g. GPI-0) is preserved;
- screenshots or rendered evidence where the change is user-facing and a live, explicitly-provided production URL exists to verify against — see §17 for what to do when it doesn't.

## 17. Natural-Run Verification

Required whenever the change affects a schedule, background job, Daily Picks generation, premarket finalization, outcome resolution, recurring validation, or any other delayed/lifecycle operation that cannot be directly, safely exercised during the deployment turn itself.

A deployment must be described as **awaiting natural-run verification** — never as fully verified — until actual natural evidence (a real scheduled firing, observed read-only) exists. State the exact evidence the next natural occurrence must produce, and do not manually trigger the operation to manufacture that evidence early unless separately, explicitly authorized as a controlled canary (its own safety boundary, per §18).

## 18. Safety Boundaries

**One complete, end-to-end prompt is the default** for material implementation work under this standard. A split into multiple prompts is permitted **only** for a named safety reason, such as:

- a historical backfill;
- a database write or migration;
- a destructive operation;
- a production job trigger;
- an unresolved OOM or resource risk;
- an active production incident;
- incomplete evidence needed before a second operation can be safely evaluated;
- a deployment blocked by an active production process.

**The boundary must be declared at the start of the prompt**, not discovered silently near the end: where the work stops, why it stops there, what separate authorization is needed to continue, and what evidence must exist before the next step is safe.

## 19. Self-Correction Rule

If a prompt governed by this standard is later found to have been incomplete against SES-006 (a required section skipped, a coverage item silently excluded with no reason):

- stop using that prompt;
- state exactly which SES-006 requirement it missed;
- replace it with **one** complete, corrected prompt that covers the full remaining lifecycle;
- do not stack multiple partial add-on prompts patching the gap piecemeal, unless a genuine safety boundary (§18) requires the split.

## 20. Required Completion States

Every piece of SES-006-governed work ends in exactly one evidence-based outcome, stated verbatim in that form (adapted to the specific change):

```
IMPLEMENTED, TESTED, DOCUMENTED, COMMITTED, PUSHED AND DEPLOYED —
AWAITING NATURAL-RUN VERIFICATION
```

```
IMPLEMENTED, TESTED AND COMMITTED —
DEPLOYMENT BLOCKED: <exact reason>
```

```
IMPLEMENTATION BLOCKED —
<exact reason>
```

**Unsupported success claims are not permitted.** "Deployed" requires a matched SHA; "tested" requires shown results; "verified in production" requires actual evidence, not an assumption that a correct-looking diff must have worked.

---

## Compliance Matrix

Every piece of SES-006-governed work — and both companion templates — track compliance against the same checklist:

- [ ] Senior role established
- [ ] Current state captured
- [ ] Scope defined
- [ ] Safety boundary declared
- [ ] Protected files identified
- [ ] Dependency audit completed
- [ ] Backend reviewed
- [ ] Frontend reviewed
- [ ] API/types reviewed
- [ ] Database reviewed
- [ ] Workflows reviewed
- [ ] User-facing consistency reviewed
- [ ] Historical evidence preserved
- [ ] Targeted tests passed
- [ ] Full regression passed
- [ ] Frontend typecheck/build passed
- [ ] Final consistency search completed
- [ ] Documentation updated
- [ ] Final Release Review passed
- [ ] Explicit staging confirmed
- [ ] Commit confirmed
- [ ] Production gate passed or blocker recorded
- [ ] Push confirmed or blocker recorded
- [ ] Deployment SHA confirmed or not applicable
- [ ] Post-deployment verification completed or blocked
- [ ] Natural-run verification completed, pending, or not applicable
- [ ] Final evidence report completed

## Companion Templates

- [`StockSense360-End-to-End-Implementation-Prompt-Template.md`](../Templates/StockSense360-End-to-End-Implementation-Prompt-Template.md) — the prompt-author-facing template implementing §5's coverage declaration.
- [`StockSense360-End-to-End-Release-Evidence-Template.md`](../Templates/StockSense360-End-to-End-Release-Evidence-Template.md) — the evidence-report template implementing §12 and §20's completion states.

## Relationship to SES-001 through SES-005

SES-006 does not replace or restate SES-001 (general engineering discipline), SES-002 (Python coding), SES-003 (testing categories and hygiene), SES-004 (documentation structure and location), or SES-005 (branding) — all five remain independently authoritative in their existing domains. SES-006 is the lifecycle standard sitting above them: it tells you *when* to invoke each one and *what a complete pass through all of them together* must look like for material implementation work.
