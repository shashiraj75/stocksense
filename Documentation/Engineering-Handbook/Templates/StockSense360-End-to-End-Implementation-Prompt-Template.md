# StockSense360 End-to-End Implementation Prompt Template

StockSense360 Senior Programmer End-to-End Implementation Standard
Standard: SES-006
Version: 1.1

Copy this template into a new prompt for any material StockSense360 implementation work (see [SES-006 §2](../SES/SES-006-End-to-End-Implementation-and-Release-Standard.md#2-applicability) for what counts). Fill in every bracketed placeholder. For every item in the PROMPT COVERAGE DECLARATION, mark it **INCLUDED**, **EXCLUDED WITH REASON**, or **NOT APPLICABLE** — an item marked excluded with no reason does not satisfy SES-006 §5.

---

## [Feature/Change Title]

### Objective

[One or two sentences: what is changing and why.]

### Current Verified Production State

- Deployed commit: `[SHA]`
- [Any other currently-true, load-bearing production facts — active holds, in-progress work, known incidents.]

### Pre-Existing Working-Tree State

[Output of `git status --short`, `git diff --cached --name-only`, `git log --oneline --decorate -5`, captured before this work begins.]

### Protected Files

[List every file/directory that must be preserved exactly — not staged, unstaged, restored, edited, deleted, or committed as part of this work, even if it appears related.]

---

## PROMPT COVERAGE DECLARATION

| Item | Status | Reason (required if excluded) |
|---|---|---|
| Implementation | INCLUDED / EXCLUDED WITH REASON | |
| Backend compatibility | INCLUDED / EXCLUDED WITH REASON | |
| Frontend consistency | INCLUDED / EXCLUDED WITH REASON | |
| Repository-wide consistency audit | INCLUDED / EXCLUDED WITH REASON | |
| Tests | INCLUDED / EXCLUDED WITH REASON | |
| Documentation | INCLUDED / EXCLUDED WITH REASON | |
| Final Release Review | INCLUDED / EXCLUDED WITH REASON | |
| Commit | INCLUDED / EXCLUDED WITH REASON | |
| Push | INCLUDED / EXCLUDED WITH REASON | |
| Deployment | INCLUDED / EXCLUDED WITH REASON | |
| Production verification | INCLUDED / EXCLUDED WITH REASON | |
| Natural-run verification | INCLUDED / NOT APPLICABLE | |

## MULTIDISCIPLINARY REVIEW DECLARATION

Per [SES-006 §3A](../SES/SES-006-End-to-End-Implementation-and-Release-Standard.md#3a-multidisciplinary-principal-engineering-intelligence) (role definitions and concern lists are not repeated here). For each lens, mark **MATERIALLY APPLICABLE**, **CONSIDERED — NO MATERIAL ACTION REQUIRED**, or **NOT APPLICABLE — <reason>**.

| Lens | Status |
|---|---|
| Data Engineer | |
| Principal Data Engineer | |
| Data Architect | |
| ML Engineer | |
| Data Scientist / Quant | |
| MLOps Engineer | |
| Financial Domain Expert | |
| Compliance Officer | |

## EVIDENCE REUSE DECLARATION

Per [SES-006 §19A](../SES/SES-006-End-to-End-Implementation-and-Release-Standard.md#19a-evidence-reuse--non-repetition--execution-efficiency-standard).

- Authoritative base SHA: [SHA]
- Reusable prior evidence: [what's being reused, and from what prior verified state]
- Invalidation conditions: [what would make reused evidence stale]
- Checks that do NOT need repetition: [list, with reason]
- Checks that MUST be rerun because this change can invalidate them: [list]

### Safety Boundary

[State explicitly: is there one? If yes — per SES-006 §18 — name it (backfill / DB write or migration / destructive operation / production job trigger / unresolved OOM or resource risk / active production incident / incomplete evidence needed before a second operation / deployment blocked by an active process), where this prompt's work stops, why it stops there, what separate authorization is needed to continue, and what evidence must exist before the next step is safe. If no safety boundary applies, say so explicitly — do not leave this section silently blank.]

---

## Scope

### Backend Scope

[Services, routers, persistence paths affected. Or "none."]

### Frontend Scope

[Pages, shared components, API clients affected. Or "none."]

### API / Schema / Type Scope

[Routes, request/response shapes, TypeScript/Python types affected. Or "none."]

### Database Scope

[Tables, columns, migrations affected. Or "none — read/write path only, no schema change."]

### Workflow / Scheduler Scope

[GitHub Actions workflows, cron schedules, background jobs affected. Or "none."]

### Non-Negotiable Safety Rules

[Anything from SES-006 §8 (India/US separation, fail-closed behavior, provenance, GPI-0 or other active holds, etc.) that this specific change must not weaken — name each one relevant to this change explicitly, don't just cite the section number.]

---

## Repository-Wide Terminology Search

[The exact search terms/patterns to run before implementation, per SES-006 §6 — function names, route paths, schema fields, UI copy strings, old values being replaced.]

## Affected Pages / Headings / Labels

[Every user-facing surface — page, heading, badge, tooltip, loading/empty/error state — that plausibly needs review, per SES-006 §7. State explicitly which are actually affected vs. reviewed-and-found-unaffected.]

---

## Test Requirements

[Per SES-006 §9 / SES-003: which categories apply (unit/integration/regression/golden/frontend), specific new-behavior boundary cases, specific failure paths, specific legacy-compatibility cases. State explicitly: no live-provider calls, no production writes, from any test.]

## Documentation Requirements

[Per SES-006 §10 / SES-004: which existing documents need updating, whether a new release document is required (and its working title), what must NOT be claimed as fixed by this change (explicit non-claims), rollback instructions to be written.]

## Rollback

[How to revert this change if it needs to be undone after deployment — exact files/commits, and any ordering constraint between them.]

---

## Final Release Review

[Per SES-006 §12 — to be executed, not pre-filled: `git status --short`, `git diff --name-only`, `git diff --stat`, `git diff --cached --name-only`, changed-file classification, test results, artifact cleanup, protected-file proof, scope compliance, production-safety state.]

## Commit Message

```
[type(scope): summary]
```

## Production Safety Gate

[Per SES-006 §14 — the exact read-only checks this specific change needs before push: which endpoints, which log patterns, which metrics.]

## Push / Deploy

[Per SES-006 §15 — confirm this is the default "push the approved commit, allow normal deployment" path, or state the explicit reason it isn't.]

## Post-Deployment Verification

[Per SES-006 §16 — the exact checks after deployment: health, logs, memory, affected API smoke tests, affected frontend pages, cross-market checks, integrity-hold preservation.]

## Natural-Run Verification

[Per SES-006 §17 — if applicable: what the next natural occurrence must produce as evidence, and confirmation this deployment will be described as "awaiting natural-run verification" until that evidence exists. If not applicable, say so.]

---

## Final Evidence Report

[Use the companion [Release Evidence Template](StockSense360-End-to-End-Release-Evidence-Template.md) to structure the final report.]

## Exact Completion Outcome

End with exactly one of SES-006 §20's required completion states, adapted to this change:

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
