# StockSense360 Claude Development Guide

Before implementing any project change, read, in order:

1. [`Documentation/Engineering-Handbook/INDEX.md`](Documentation/Engineering-Handbook/INDEX.md) — the handbook entry point.
2. `Documentation/Engineering-Handbook/SES/SES-001` through `SES-006`, as applicable to the change (SES-006 is the end-to-end implementation and release lifecycle standard — see below).
3. [`Documentation/Engineering-Handbook/Operations/Current-Release-Status.md`](Documentation/Engineering-Handbook/Operations/Current-Release-Status.md) — the current release, feature-flag, and validation-gate state; do not assume a document's own historical claim still reflects production.
4. Whatever architecture, specification, or release document governs the specific area being changed (found via the INDEX).

**Material implementation work — bug fixes, features, production logic changes, frontend changes, backend changes, API changes, scheduler/workflow changes, database/persistence changes, user-facing copy changes, and material documentation-driven behavior changes — follows SES-006 by default**, normally delivered as one complete, end-to-end prompt (investigation → implementation → repository-wide consistency → tests → documentation → Final Release Review → commit → production safety gate → push → deployment → verification → natural-run verification where applicable). A narrower read-only, evidence-collection, design-only, or documentation-only prompt is valid only when the user explicitly requests that narrower scope, or the work is a production-incident triage or a single explicitly-authorized operational action.

Before starting SES-006-governed work, state the prompt coverage declaration and any safety boundary (SES-006 §5, §18) — do not begin implementation silently.

Do not narrow scope by silently treating related frontend, documentation, workflow, testing, or deployment work as "out of scope" — if something is genuinely excluded, say so explicitly with a reason, per SES-006 §5.

All SES-006-governed work must also comply with (A) the Multidisciplinary Principal Engineering Intelligence Standard (SES-006 §3A) and (B) the Evidence Reuse / Non-Repetition / Execution Efficiency Standard (SES-006's efficiency section) — materially applicable multidisciplinary concerns are mandatory, but a non-applicable role must never be used to manufacture work merely to appear compliant.

Do not duplicate the full Engineering Handbook here — this file only points to it.
