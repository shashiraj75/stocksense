# SES-004 — Documentation Standards

**Status:** Active — governing.
**Applies to:** everything under `Documentation/`.
**Specializes:** SES-001.

---

## 1. Where things live

| Kind of document | Location |
|---|---|
| User-facing feature documentation | `Documentation/STOCKSENSE_DOCUMENTATION.md` (+ generated `.pdf`/`.docx`) |
| Engineering audits | `Documentation/Engineering-Handbook/Architecture/` (e.g. SEAR-001) |
| Sprint reports | `Documentation/Engineering-Handbook/Releases/` (e.g. `Sprint-002-Engineering-Foundation.md`) |
| Roadmaps | `Documentation/Engineering-Handbook/ROADMAP.md` |
| Governing standards (this family) | `Documentation/Engineering-Handbook/SES/` |
| Specification templates | `Documentation/Engineering-Handbook/SSDS/` |
| Domain/service/AI/quant-model deep dives | the matching `Documentation/Engineering-Handbook/{Domains,Services,AI,Quantitative-Models}/` folder |

Don't create a new top-level documentation file or folder if an existing one already fits — extend what exists. If genuinely nothing fits, name the new location explicitly in the change rather than burying a new doc inside an unrelated folder.

## 2. Every audit, report, or standard names its evidence

A claim about the codebase's current state (a count, a threshold value, a behavior) is either:
- a direct quote/citation of a `file:line`, or
- the output of a command actually run, shown or referenced.

SEAR-001 is the reference example throughout: every finding ties back to a specific grep result, a specific file's line numbers, or a specific live-data test. A documentation change that asserts something about the code without one of these is not meeting this standard — go verify it first.

## 3. Sprint reports — required sections

Every sprint report (see `Releases/Sprint-002-Engineering-Foundation.md` as the reference) covers, at minimum:
- **Files Changed** — new and modified, one line each, what it's for.
- **Architecture Changes** — or an explicit "none" if there weren't any; don't omit the section.
- **Risks** — named honestly, including risks the sprint's own work introduced or left open. A sprint report with zero risks listed should be treated as suspicious, not as evidence of a clean sprint.
- **Migration Notes** — anything a future engineer touching this code needs to know that isn't obvious from the diff alone.
- **Testing Status** — what's covered, what isn't (see SES-003 §5) and how the suite was verified (local run, CI run, both).
- **Recommendations for the next sprint** — concrete, not vague; ideally tied to specific roadmap items.

## 4. Audits — required sections

An engineering audit (see SEAR-001 as the reference) covers, per the review-area structure already established: a scored Executive Summary, Strengths, Weaknesses, Critical Issues, tiered Priority Improvements (High/Medium/Nice-to-have), an honest Estimated Engineering Effort, and a Recommended Sprint Roadmap. An audit that only lists problems without an effort/priority framing isn't actionable — don't stop short of that.

## 5. Roadmaps

A roadmap converts audit findings into sequenced, sized work (see `ROADMAP.md`'s phase/sprint structure). Every roadmap item states: priority, business value, engineering effort, risk, dependencies, files likely affected, estimated time, and which sprint it's assigned to. A dependency graph accompanies any roadmap with more than a handful of items, showing what must happen before what.

## 6. Honesty about gaps and inputs

If an instruction references a document, standard, or input that doesn't actually exist in the repository, say so explicitly and proceed on verified inputs only — don't fabricate content to fill the gap. (This happened once already, with "SES Standards"/"SSDS Specifications" referenced before they existed — the correct response was to say so plainly, which is also how this very document came to exist.)

## 7. Keep INDEX.md current

`Documentation/Engineering-Handbook/INDEX.md` is the entry point every other document — and `CLAUDE.md` — points to. Any new governing document (a new SES/SSDS number, a new major folder's first real content) gets a line in INDEX.md in the same change that adds it. An ungoverned document nobody can find from the index isn't really part of the handbook yet.
