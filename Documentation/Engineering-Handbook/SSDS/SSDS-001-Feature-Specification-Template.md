# SSDS-001 — Feature Specification Template

**Status:** Active — governing.
**Use when:** proposing or implementing any user-facing feature or material change to existing feature behavior (Portfolio, Paper Trade, Daily Picks, Multibagger, Watchlist, Alerts, etc.).
**Governed by:** SES-001 through SES-004.

Copy this template into the relevant `Documentation/Engineering-Handbook/Domains/` (or `Services/`) subfolder, named `<feature-name>-spec.md`, and fill in every section before implementation starts. A section that doesn't apply gets an explicit "N/A — because ___", not silence.

---

## 1. Problem Statement

What specifically is broken, missing, or confusing today? Cite evidence — a user-reported symptom, a live API response, a screenshot, a specific file:line. "It would be nice if..." is not a problem statement; "users see dashes in the Signal column because the staggered-query hook reports `isLoading: false` for not-yet-unlocked rows" is.

## 2. Goal and Non-Goals

- **Goal:** the specific, testable outcome this feature/fix achieves.
- **Non-goals:** what this explicitly does *not* attempt to solve, even if related. (SES-001 §1 scope discipline applies here directly — name the boundary before writing code, not after someone asks why something adjacent wasn't touched.)

## 3. Affected Surfaces

- **Backend:** which files/services/routers, and whether this touches the Selection Engine (prediction_engine.py, quality_factors.py, multibagger_scorecard.py, alpha_engine/) — if so, flag it for extra scrutiny per SES-001 §4.
- **Frontend:** which pages/components.
- **Data:** any new/changed Postgres tables, columns, or cache shapes.
- **Both markets?** Does this apply to IN, US, or both — and if only one, why (data availability, methodology difference, etc.)?

## 4. Design

- The actual approach — described concretely enough that someone else could implement it from this section alone.
- Any explicit trade-off made and why (e.g. "we drop the Order Book/Revenue ratio from this formula because no data source has it for any stock, IN or US — faking it would be worse than omitting it," from the Multibagger Elite tier work, is the level of specificity expected here).
- If this changes a number a user already trusts (a threshold, a formula, a verdict label), say what changes and what stays the same.

## 5. Explainability and Transparency Impact

Per the standing "make the tool transparent, build investor confidence" principle established this engagement: does this feature introduce or resolve a "looks contradictory but isn't" pairing (e.g. Confidence vs. Conviction)? If it introduces a new metric/label, what does the user-facing copy say about what it means and how it differs from anything adjacent?

## 6. Data and Edge Cases

- What happens when required data is missing, stale, or partially available? (Don't default to silently treating missing as zero or as a pass — state the actual fallback and why it's the right one.)
- What's the behavior for a brand-new stock with no history, a stock with no news coverage, a financial-sector stock if sector-specific exemptions matter here?

## 7. Testing Plan

Per SES-003: which categories (unit/integration/regression/golden) will cover this, and specifically what the golden/regression cases are if this touches gate or scoring logic. If this is a pure UI change with no backend logic, say so explicitly and describe the manual browser verification plan instead (per the root engineering guidance: start the dev server, test the golden path and edge cases, before claiming done).

## 8. Rollout and Risk

- Reversibility: can this be turned off/rolled back without a data migration? If not, why is that acceptable here?
- Anything that touches a scheduled job (GitHub Actions cron, a background loop in `main.py`) — confirm the trigger conditions explicitly (see the Daily Picks workflow-dispatch step-skip incident from this engagement as the cautionary example: a job can show "success" while its actual trigger step was silently skipped).

## 9. Open Questions

Anything genuinely undecided, with who needs to decide it (a stakeholder/product call vs. a pure engineering judgment call).
