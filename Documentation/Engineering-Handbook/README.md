# StockSense360 Engineering Handbook

This handbook is the governing reference for how engineering work on StockSense360 gets planned, built, tested, and documented. `CLAUDE.md` at the repository root requires `INDEX.md` be consulted before implementing project changes — **start there**, not here.

## What governs

- **`SES/` (StockSense Engineering Standards)** — binding standards for every future implementation: [SES-001 Engineering Standards](SES/SES-001-Engineering-Standards.md), [SES-002 Python Coding Standards](SES/SES-002-Python-Coding-Standards.md), [SES-003 Testing Standards](SES/SES-003-Testing-Standards.md), [SES-004 Documentation Standards](SES/SES-004-Documentation-Standards.md).
- **`SSDS/` (StockSense Specification & Design Standards)** — templates used to scope work before it's built: [SSDS-001 Feature Specification Template](SSDS/SSDS-001-Feature-Specification-Template.md), [SSDS-002 Sprint Specification Template](SSDS/SSDS-002-Sprint-Specification-Template.md).

These two folders are not optional reading — they are the standard every audit, sprint, feature, and test in this repository is expected to be held to from this point forward.

## What else lives here

- **`Architecture/`** — system-level design and engineering audits, starting with SEAR-001 (the Selection Engine audit that the current Roadmap and most of the SES standards are derived from).
- **`ROADMAP.md`** — the phased, sprint-by-sprint master implementation plan.
- **`Releases/`** — one report per completed sprint.
- **`Domains/`, `Services/`, `AI/`, `Quantitative-Models/`** — per-area deep dives, populated incrementally.
- **`Testing/`, `Operations/`** — process documentation.
- **`ADR/`** — architecture decision records.
- **`Templates/`, `Diagrams/`, `Assets/`** — supporting material.

See [INDEX.md](INDEX.md) for the full, linked map of everything in this handbook.
