# SES-005 — StockSense360 Branding Standard

**Status:** Active — governing.
**Applies to:** all documentation, engineering standards, prompts, sprint reports, architecture documents, and code comments.
**Specializes:** SES-001, SES-004.

---

## 1. Official Product Name

The official product name is **StockSense360** — one word, capital S twice, no space before "360," no hyphen.

- Correct: `StockSense360`
- Incorrect: `StockSense`, `Stock Sense 360`, `Stock-Sense360`, `STOCKSENSE360` (except where case is structurally forced — see §3), `stocksense360` in prose.

"StockSense" alone is a legacy/shortened form from earlier in the project's history. It is not a separate product, sub-brand, or abbreviation to preserve — every new or edited piece of prose-facing text uses the full name.

## 2. Approved Naming Conventions

| Context | Form | Example |
|---|---|---|
| Prose, headings, titles | `StockSense360` | "StockSense360 is an AI-powered..." |
| First mention in a document | `StockSense360` (never abbreviated on first use) | — |
| Subsequent mentions in the same document | `StockSense360`, or "the platform" / "the tool" / "it" where that reads naturally — never silently drop to "StockSense" | — |
| Possessive | `StockSense360's` | "StockSense360's Selection Engine" |

There is no approved abbreviation (no "SS360," no "StockSense" as shorthand). If a document needs a shorter referent after first mention, use "the platform" or "the tool," not a clipped form of the name.

## 3. Documentation Naming Rules

- New documentation files use the product name in title-case prose form inside the document (`# StockSense360 — <Title>`), regardless of the filename's own casing convention.
- Existing filenames that already use a different casing or form for technical reasons (e.g. `STOCKSENSE_DOCUMENTATION.md`, an all-caps convention for a root-level doc file) are **not** renamed under this standard — see §6, Out of Scope. Only the *prose inside* such files is standardized.
- Generated artifacts (PDF/DOCX exports of documentation) carry the same in-content branding rules as their markdown source: cover titles, footers, and document metadata (title/author fields) use `StockSense360`.
- Every Engineering Handbook document (SES, SSDS, Architecture, Releases, ROADMAP) that names the product in prose uses `StockSense360`, not a shortened form, including in document titles and section headers.

## 4. Git Commit Naming Conventions

- A commit whose primary purpose is a branding/naming change uses the form: `docs(StockSense360): <what changed>` — matching the convention this very sprint's commit follows.
- A commit that incidentally touches branded text as part of a larger documentation change does not need the `(StockSense360)` scope — use it only when branding consistency is the commit's main point.
- Sprint/audit commit messages that name the product in their body use `StockSense360`, not "StockSense" or "the app."

## 5. Sprint and Architecture Naming Conventions

- Sprint reports, audits, and roadmaps refer to the product as `StockSense360` in their prose (titles, executive summaries, narrative sections). Internal subsystem names are unaffected by this rule — "the Selection Engine," "the IC engine," "Daily Picks," "Multibagger Screen," etc. remain their own established names and are not folded into the product name.
- A sprint report's title (e.g. `Sprint-002-Engineering-Foundation.md`) is not required to embed "StockSense360" in the filename — the existing `Sprint-NNN-<Topic>.md` convention (SES-004 already governs this) continues unchanged. The product name appears in the document's body where it's mentioned in prose.
- Architecture documents (audits, ADRs) follow the same rule: `StockSense360` in prose, established subsystem/engine names left alone.

## 6. Explicitly Out of Scope — Do Not Rename

This standard governs **prose and documentation content only**. The following are never renamed or altered under this standard, regardless of what name they currently use:

- Repository name
- Python package names and import paths (`services.*`, `api.*`, etc.)
- Database names, table names, schema identifiers
- URLs, API endpoints, deployment hostnames
- Deployment configuration (Railway/Render/Vercel service names, environment variable names, CI workflow filenames)
- Existing filenames that encode a technical or historical naming convention (e.g. `STOCKSENSE_DOCUMENTATION.md`)

A branding-consistency change is a documentation/prose change. If a future task wants to rename any of the items above, that is a separate, explicitly-scoped engineering task — not an extension of this standard.

## 7. Handling Existing Code-Level Mentions

Some non-documentation code currently contains the bare "StockSense" string in user-facing or external-facing text (UI copy, a FastAPI app title, a Telegram notification message, a User-Agent header, console log lines). These are **not** addressed by this standard's enforcement pass, because they are code, not documentation, and this standard's scope is documentation/standards/prompts/comments only (see §6 and the governing sprint's explicit "no functional code changes" rule).

If branding consistency in user-facing application copy is wanted, that is a follow-up, separately-scoped task against the specific files involved (frontend components, backend response strings, notification templates) — tracked as such rather than folded silently into a documentation-only sprint.
