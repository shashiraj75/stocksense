# INDEX

This is the entry point for the StockSense360 Engineering Handbook. `CLAUDE.md` at the repository root requires this index be consulted before implementing project changes — start here.

## Governing Standards (read first, every time)

These are binding on all future implementation work, not reference material to consult only when convenient.

- [SES-001 — Engineering Standards](SES/SES-001-Engineering-Standards.md) — scope discipline, no-regressions, evidence-over-assertion, risk-matching, commit hygiene.
- [SES-002 — Python Coding Standards](SES/SES-002-Python-Coding-Standards.md) — threshold registry, logging, engine return shape, typing, error handling.
- [SES-003 — Testing Standards](SES/SES-003-Testing-Standards.md) — pytest layout, the four test categories, isolation, sanity-checking your own tests.
- [SES-004 — Documentation Standards](SES/SES-004-Documentation-Standards.md) — where documents live, what every audit/sprint-report/roadmap must contain.
- [SES-005 — StockSense360 Branding Standard](SES/SES-005-StockSense360-Branding-Standard.md) — official product name, approved naming conventions, what's explicitly out of scope to rename.

## Specification Templates

Use these to scope work *before* implementation starts.

- [SSDS-001 — Feature Specification Template](SSDS/SSDS-001-Feature-Specification-Template.md) — for any user-facing feature or material behavior change.
- [SSDS-002 — Sprint Specification Template](SSDS/SSDS-002-Sprint-Specification-Template.md) — for scoping a numbered engineering sprint against the Roadmap.

## Engineering Audits

- [SEAR-001 — Selection Engine Engineering Audit](Architecture/Sprint-001-Selection-Engine-Audit.md) — the founding audit; source of the Roadmap's phases and most named risks/gaps referenced throughout the standards above.

## Roadmap and Releases

- [ROADMAP.md](ROADMAP.md) — the 5-phase, sprint-by-sprint master implementation plan derived from SEAR-001.
- [Releases/](Releases/) — one report per completed sprint (e.g. `Sprint-002-Engineering-Foundation.md`), each following SES-004 §3's required structure.
- [CHANGELOG.md](CHANGELOG.md)

## Domain Documentation

- [Architecture/](Architecture/) — system-level design and audits.
- [Domains/](Domains/), [Services/](Services/), [AI/](AI/), [Quantitative-Models/](Quantitative-Models/) — deep dives per area, populated as each is documented.
- [Testing/](Testing/), [Operations/](Operations/) — process documentation for those areas.
- [ADR/](ADR/) — architecture decision records.
- [Templates/](Templates/), [Diagrams/](Diagrams/), [Assets/](Assets/) — supporting material.

## How to use this handbook

1. Before starting any implementation task, read SES-001–004 if you haven't already internalized them this session.
2. Before starting a feature or a sprint, fill out the matching SSDS template.
3. While working, follow the relevant SES standard for the kind of change you're making.
4. When you finish a sprint, write its report under `Releases/` per SES-004 §3, and update this INDEX if you added a new governing document.
