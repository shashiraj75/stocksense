# INDEX

This is the entry point for the StockSense360 Engineering Handbook. `CLAUDE.md` at the repository root requires this index be consulted before implementing project changes — start here.

## Governing Standards (read first, every time)

These are binding on all future implementation work, not reference material to consult only when convenient.

- [SES-001 — Engineering Standards](SES/SES-001-Engineering-Standards.md) — scope discipline, no-regressions, evidence-over-assertion, risk-matching, commit hygiene.
- [SES-002 — Python Coding Standards](SES/SES-002-Python-Coding-Standards.md) — threshold registry, logging, engine return shape, typing, error handling.
- [SES-003 — Testing Standards](SES/SES-003-Testing-Standards.md) — pytest layout, the four test categories, isolation, sanity-checking your own tests.
- [SES-004 — Documentation Standards](SES/SES-004-Documentation-Standards.md) — where documents live, what every audit/sprint-report/roadmap must contain.
- [SES-005 — StockSense360 Branding Standard](SES/SES-005-StockSense360-Branding-Standard.md) — official product name, approved naming conventions, what's explicitly out of scope to rename.

## System Design Specifications (SSDS)

- [SSDS-000 — StockSense360 System Architecture](SSDS/SSDS-000-StockSense360-System-Architecture.md) — the master architecture document; the single source of truth for how every component of the platform fits together. Read this before designing anything that spans more than one engine or service.
- [SSDS-001 — Feature Specification Template](SSDS/SSDS-001-Feature-Specification-Template.md) — for any user-facing feature or material behavior change.
- [SSDS-002 — Sprint Specification Template](SSDS/SSDS-002-Sprint-Specification-Template.md) — for scoping a numbered engineering sprint against the Roadmap.
- [SSDS-003 — StockSense360 Business Quality Engine](SSDS/SSDS-003-StockSense360-Business-Quality-Engine.md) — full design spec (framework, metrics, sector adaptation, output contract, validation strategy) for the engine answering "is this fundamentally an outstanding business worthy of long-term ownership?" Implementation-ready, not yet built.

## Glossary

- [StockSense360 Product Glossary](Glossary/StockSense360-Product-Glossary.md) — the canonical name for every engine, score, and feature across the platform. No future document should invent an alternative name for a concept already named there.

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

1. Before starting any implementation task, read SES-001–005 if you haven't already internalized them this session.
2. Check the [Product Glossary](Glossary/StockSense360-Product-Glossary.md) before naming any engine, score, or feature — use the name it specifies, don't invent a new one.
3. For anything spanning more than one engine or service, read [SSDS-000](SSDS/SSDS-000-StockSense360-System-Architecture.md) first. Before starting a feature or a sprint, fill out the matching SSDS-001/002 template.
4. While working, follow the relevant SES standard for the kind of change you're making.
5. When you finish a sprint, write its report under `Releases/` per SES-004 §3, and update this INDEX if you added a new governing document.
