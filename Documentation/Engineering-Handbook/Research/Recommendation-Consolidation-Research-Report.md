# Recommendation Consolidation Intelligence — Research Report (companion to SSDS-009)

**Purpose of this document:** SSDS-009 states *what* the design is and *where* each decision boundary sits. This report states *why* the Hybrid model was chosen over the alternatives, with the evidence and risk analysis behind that choice — the reasoning a future engineer would otherwise have to re-derive.

---

## 1. Alternatives Considered

### A. Weighted Composite Model

**Why it's tempting:** simple, familiar from other investing tools, easy to implement quickly.

**Why it was rejected:** this codebase has a confirmed, *already-documented* double-counting precedent — Business Quality was deliberately kept as a parallel, non-replacing field at Epic 001's own integration specifically *because* `fund_score` (the legacy ratio score) and `quality_score` (`quality_factors.py`'s own composite) already overlap with it. A weighted blend would re-introduce exactly the problem Epic 001 went out of its way to avoid, three epics ago. This is not a hypothetical risk — it is a re-opening of an already-closed issue.

### B. Rule-Based Decision Matrix

**Why it's tempting:** explicit, auditable, easy to test.

**Why it was not selected alone:** the combinatorial space is already large with four core engines plus two legacy scores plus macro/risk-reward/pledge inputs — an exhaustively enumerated rule set would need constant expansion as new combinations arise in real data, and any combination the rule author didn't anticipate falls through to an undefined or overly generic default. This is the same brittleness concern any rule-based system faces as its input space grows, not unique to this codebase, but real here given the existing input count.

### C. Hybrid Model (selected)

Combines B's strength (explicit, auditable rules) for the **narrow, already-well-defined hard-gate layer**, where rules genuinely are the right tool (a liquidity-distress rejection is categorical, not a matter of degree), with a **narrative-template layer driven by engine agreement/disagreement counts** for the larger, more nuanced space — never introducing a new blended score at all.

## 2. Evidence Supporting the Recommended Design

- **The Strategic Decision sprint's own selection criteria directly favor this design**: Recommendation Consolidation was chosen over Risk Intelligence specifically because it requires no new data and carries the lowest duplication risk. A weighted-composite design would partially undermine that reasoning by introducing exactly the kind of new, opaque number the Strategic Decision's own Architecture Check warned against ("How can we create a new opaque meta-score?" — explicitly listed as a question RCI must *not* answer).
- **Valuation Intelligence's own cross-engine gate (Sprint #006/#007) is a working, validated prototype of the hybrid approach's core idea** — reading another engine's grade to condition one engine's own behavior, without recomputing or blending scores. RCI generalizes this pattern rather than inventing a new one.
- **Every prior engine's own grade-banding (STRONG_BUY/BUY/HOLD/WATCH/AVOID/REJECTED) is itself a categorical, non-blended design choice** — the Thesis Conviction label (Strong/Moderate/Weak/Conflicted) directly extends this established convention rather than introducing a numerically precise alternative.

## 3. Risk of Each Alternative

| Alternative | Primary risk |
|---|---|
| Weighted Composite | Silent double-counting of already-overlapping legacy scores; false precision; renormalization bugs on missing data |
| Rule-Based Matrix (alone) | Combinatorial brittleness; undefined behavior on novel combinations; rule-set maintenance burden growing faster than engine count |
| Hybrid (selected) | Narrative-template combinatorics as a milder version of the same risk B has alone — named explicitly in SSDS-009 §9 as a real, ongoing risk to monitor, not eliminated, only reduced by using agreement-counts rather than exhaustive enumeration |

## 4. Double-Counting Analysis

Full input map performed in SSDS-009 §5.E. **Two real, pre-existing overlaps were found** (not introduced by this design): `fund_score`↔Business Quality and `quality_score`↔Business Quality, both already known and already handled via Epic 001's "parallel field" decision — RCI's contribution is to make this overlap *visible* to the user for the first time (an `overlap_group` tag driving an automatic narrative caveat), not to introduce a new one. **No new overlap was found among the four core engines** — each one's own Evidence Checkpoint and Sprint #007's Double-Counting Assessment already confirmed mutual independence, inherited here rather than re-derived.

## 5. Cross-Engine Interaction Analysis

RCI introduces exactly one new interaction: it *reads* every engine's output, but **no engine reads RCI's output** — a strictly one-directional dependency, the same shape Valuation Intelligence's own cross-engine gate already established (it reads Business Quality/Financial Strength/Growth Intelligence's grades; none of them read Valuation Intelligence's). RCI does not create a cycle, does not feed back into any engine's own scoring, and does not alter the existing Prediction Engine confidence pipeline's order or behavior.

## 6. Unresolved Questions

Carried forward explicitly from SSDS-009 §9, not resolved here:

1. Should the Engine-Output Contract's new fields be added to all four engines in one cross-cutting sprint, or incrementally, one engine at a time?
2. Should `invalidation_conditions` be generated from a fixed rule set or derived more dynamically per company?
3. Where exactly should the Thesis Conviction label's boundaries sit, and what evidence would justify them? (Explicitly deferred to a future calibration-equivalent sprint — no outcome data exists yet to answer this.)

## 7. Recommendation

**Proceed with the Hybrid model (SSDS-009 §4.C) as the foundation for Epic 005's implementation.** Sprint #002 should be a **contract-design sprint**, not a feasibility study — this report and SSDS-009 both confirm no new data-provider feasibility question exists; the genuinely open work is finalizing the Engine-Output Contract's exact shape and building a pure, fixture-tested consolidation function before touching any real data.

---

*This report is research only — the reasoning behind SSDS-009's model selection. No production code, tests, or providers were modified or evaluated empirically this sprint.*
