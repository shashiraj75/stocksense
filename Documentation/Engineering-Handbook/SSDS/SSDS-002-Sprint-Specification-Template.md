# SSDS-002 — Sprint Specification Template

**Status:** Active — governing.
**Use when:** scoping any numbered sprint (Sprint #003 and onward) against the roadmap.
**Governed by:** SES-001 through SES-004.
**Reference implementation:** Sprint #002's brief and its resulting report (`Releases/Sprint-002-Engineering-Foundation.md`) — read both as the worked example before writing a new sprint spec from this template.

Copy this template into a new note (or directly into the sprint kickoff message) before work starts. The corresponding `Releases/Sprint-NNN-*.md` report, written at the end, should answer every section below with what actually happened, not just what was planned.

---

## 1. Objective

One or two sentences. What category of work is this (engineering foundation, intelligence improvement, portfolio intelligence, explainability, AI/future capability — per the Roadmap's phase structure) and which roadmap item number(s) does it execute?

## 2. Deliverables

A numbered list, each one independently completable and verifiable. Each deliverable states:
- What gets built/changed.
- What "done" looks like concretely (a file exists with X structure, a test suite passes, a doc is updated) — not "improve X," which can't be checked off.

## 3. Explicit Constraints

State plainly, every time, even if it feels repetitive:
- Does investment methodology or business logic change? (Default: no, unless the objective specifically requires it.)
- Are threshold values allowed to change? (Default: no — migrating a threshold's *location* is not the same as changing its *value*; if a value must change, that's called out separately and explicitly, ideally requiring stakeholder sign-off per the roadmap's High-Risk-Change guidance.)
- Is the migration/change scope allowed to broaden beyond the named files/systems? (Default: no.)
- Any other "do not" the task author wants enforced (do not refactor beyond what's needed, do not optimize prematurely, do not add new features beyond the stated deliverables).

## 4. Out of Scope

Explicitly name anything adjacent that will NOT be done this sprint, and where it's tracked instead (a future sprint number, a flagged follow-up task). This list should be non-empty for almost any real sprint — if nothing was deliberately deferred, the scope was probably too narrow to be interesting or too broad to be honest.

## 5. Verification Requirements

Per SES-001 §2–3 and SES-003: 
- Full test suite must pass — state how this will be confirmed (local run shown, CI run confirmed green via the Actions API/UI, both).
- Full application must still import/build.
- Any new claim of "no behavior changed" needs a corresponding test, not just a read-through.

## 6. Sprint Report Requirements

Per SES-004 §3 — the end-of-sprint report must include Files Changed, Architecture Changes, Risks, Migration Notes, Testing Status, and Recommendations for the next sprint. State here if this sprint needs anything beyond that baseline (e.g. a dependency graph update to the Roadmap, an audit-checklist re-run).

## 7. Definition of Done

Restate, in this sprint's own words, what "done" means — typically: all deliverables complete, full suite green locally and in CI, sprint report written and committed, no investment-logic or out-of-constraint changes snuck in. This section exists so "done" is checkable against the spec, not negotiated after the fact.
