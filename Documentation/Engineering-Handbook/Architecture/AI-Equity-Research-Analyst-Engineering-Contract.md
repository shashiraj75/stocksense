# AI Equity Research Analyst — Engineering Contract

**Document ID:** Epic 008B — Engineering Contract  
**Status:** Active — governing for every future AI Equity Research Analyst implementation  
**Capability status:** Future; this contract does not make the capability live or authorize an implementation  
**Scope:** Engineering boundaries, data ownership, report and evidence contracts, consumer integration, performance, and conversational-AI compatibility  
**Applies to:** All future work that creates, retrieves, renders, explains, stores, or compares Research Analyst evidence or responses  

---

## 1. Purpose and Authority

This document is the non-negotiable engineering contract for StockSense360’s future AI Equity Research Analyst.

The Analyst is an **evidence-grounded research and explanation capability**. It is not a new investment-decision engine, a replacement recommendation engine, a source of market data, a broker, a portfolio allocator, or a trading system.

Its only permitted intellectual role is to help users understand validated StockSense360 evidence already produced by the platform or explicitly approved future context. It must preserve the platform’s architecture rather than create an alternative decision path.

This contract is intentionally narrower than a product vision document. It specifies what every implementation must do, must reuse, must disclose, and must never do.

### 1.1 Governing hierarchy

An implementation must comply with all of the following, in this order:

1. `CLAUDE.md`, the Engineering Handbook `INDEX.md`, applicable SES standards, SSDS specifications, and the Product Glossary;
2. `Documentation/MASTER-ROADMAP.md` for platform direction and current roadmap status;
3. the current release-status register for live deployment state, feature flags, validation gates, and operational blockers;
4. Epic 008A — *AI Research Analyst: Concept and Safety Specification*;
5. this Engineering Contract; and
6. an implementation-specific design, test plan, rollout plan, and release approval.

Where an implementation proposal conflicts with a higher governing source, the proposal is invalid. Where this contract identifies a real architectural gap during implementation, the gap must be documented and resolved explicitly; it must not be bypassed with an undocumented shortcut.

### 1.2 Terminology

- **Owner:** the component that computes and is authoritative for a datum, score, status, conclusion, or evidence item.
- **Consumer:** a component that receives an owned output but does not recalculate or redefine it.
- **Snapshot:** a versioned, immutable, structured representation of already-computed intelligence for a defined scope and as-of time.
- **Evidence item:** an auditable atomic claim or fact with an owner, status, provenance, timestamp, and identifier.
- **Research report:** the structured stock-level output assembled from a snapshot before any conversational wording is generated.
- **Research answer:** a user-question-specific explanation derived from a report or snapshot. It does not become a new source of truth.
- **Presentation derivation:** deterministic formatting, ordering, grouping, labelling, or citation selection that does not calculate a new investment metric or change an existing conclusion.

---

## 2. Non-Negotiable Platform Invariants

Every future Analyst implementation must preserve these invariants.

1. **Prediction Engine authority remains intact.** The Prediction Engine remains the sole source of truth for the live final signal, composite score, confidence, target/stop-loss logic, and existing enforced gates. The Analyst may describe these outputs; it may not replace, revise, rank above, or silently reinterpret them.

2. **One engine, one responsibility.** The Analyst owns report assembly, evidence referencing, controlled explanation, and conversational presentation. It owns no technical indicator, financial metric, valuation calculation, quality score, risk score, signal, target, stop loss, confidence score, market-regime score, portfolio score, or trade decision.

3. **No duplicate calculations.** The Analyst must never re-fetch raw provider data or independently recompute an output already owned by an existing engine. It must consume validated outputs through the shared intelligence snapshot only.

4. **No master score or shadow signal.** The Analyst must not create a new composite score, a “research score,” a replacement confidence value, a hidden ranking, a probability of return, or an alternate BUY/HOLD/SELL recommendation.

5. **Additive and read-only.** Analyst construction and failure must not mutate Prediction Engine output, the shared prediction cache, Daily Picks records, Multibagger results, portfolio data, paper-trading data, or any source-engine state.

6. **Evidence before wording.** A sentence is allowed only when its factual content is supported by the snapshot. Natural-language fluency is never a substitute for an evidence identifier, current status, provenance, or as-of timestamp.

7. **Market and horizon are first-class scope fields.** Market, symbol identity, horizon, currency, data timestamps, source availability, and feature status must travel with every snapshot and evidence item. India and US behavior belongs in market adapters and owning engines, never in Analyst prompt logic or narrative templates.

8. **Horizon integrity.** Short-, medium-, long-, and investment-horizon conclusions must not be mixed or silently substituted. Long-term and investment analysis must not use short-term trading logic as its primary thesis.

9. **State taxonomy is preserved.** `SUPPORTED`, `MISSING`, `UNAVAILABLE`, `NOT_APPLICABLE`, `FEATURE_DISABLED`, `STALE`, and `EXECUTION_ERROR` are distinct states. They must never be collapsed into an implied negative result, a generic “no data,” or a positive inference.

10. **Existing product behavior remains independently correct.** When the Analyst is unavailable, disabled, slow, or rejected by a guard, existing stock research, Prediction Engine outputs, Daily Picks, Multibagger, Portfolio, and Paper Trading must continue according to their own contracts.

11. **User decision boundary.** The Analyst supplies research and explanation. It does not guarantee outcomes, claim certainty, instruct a user to trade, allocate capital, or place a trade.

---

## 3. Required Architecture

The future Analyst must be implemented as a bounded consumer layer.

```text
Registered source engines and approved context
        |
        v
IntelligenceSnapshotBuilder  --> immutable IntelligenceSnapshot
        |                                  |
        |                                  v
        |                         ContractValidator
        |                                  |
        v                                  v
ResearchReportAssembler  <--- validated snapshot only
        |
        +--> deterministic report sections, evidence ledger, disclosures
        |
        v
Optional LLM Narrative Renderer (strictly bounded)
        |
        v
AnswerGuard / CitationValidator / SafetyPolicy
        |
        v
Consumer-specific renderer or conversational API
```

### 3.1 Component responsibilities

| Component | Mandatory responsibility | Explicit prohibition |
|---|---|---|
| **IntelligenceSnapshotBuilder** | Collects already-computed, registered owner outputs for one defined scope; creates an immutable versioned snapshot. | Must not calculate investment metrics, fetch providers directly, mutate source output, or call an LLM. |
| **ContractValidator** | Validates schema version, scope, timestamps, state taxonomy, ownership, evidence IDs, and internal consistency. | Must not “repair” missing evidence by inference or silently substitute a different horizon/market. |
| **ResearchReportAssembler** | Deterministically maps validated evidence into report sections, counter-evidence, limitations, and citations. | Must not create a score, signal, target, prediction, or uncited factual claim. |
| **Optional LLM Narrative Renderer** | Converts a bounded report and user question into plain-language explanation under a strict output schema. | Must not browse, retrieve external data, calculate, invoke tools, change labels, create evidence, or issue action instructions. |
| **AnswerGuard** | Validates that every material response claim maps to permitted evidence, approved inference rules, or a disclosed unknown; applies safety and policy constraints. | Must not silently rewrite a failed answer into a plausible but uncited answer. |
| **Consumer renderer** | Shows the approved report or answer in the appropriate product surface. | Must not independently fetch or recompute an omitted report field. |

### 3.2 Mandatory dependency direction

The only permitted dependency direction is:

```text
Owner engine / approved context -> snapshot -> report -> narrative -> consumer
```

The following dependency directions are prohibited:

```text
Analyst -> market-data provider
Analyst -> raw financial-statement parser
Analyst -> indicator calculator
Analyst -> engine internals for recomputation
LLM -> database / network / arbitrary tools
Consumer -> provider or engine re-run to fill a missing report section
Analyst -> Prediction Engine mutation or cache mutation
```

---

## 4. Report Architecture and Section-by-Section Data Ownership

A Research Report is a structured contract, not free-form prose. Sections may be hidden only when their owner reports an explicit non-supported state; they may never be invented from adjacent evidence.

### 4.1 Required report envelope

Every report must include the following envelope before any substantive section is rendered:

- `report_contract_version`
- `snapshot_id` and immutable `snapshot_hash`
- `symbol`, canonical instrument identity, `market`, exchange context, and currency
- `horizon`
- `generated_at`, `data_as_of`, and component-level as-of timestamps
- source-engine versions or contract versions where available
- top-level snapshot status: `COMPLETE`, `PARTIAL`, `STALE`, `UNAVAILABLE`, or `INVALID`
- explicit feature-status map for optional intelligence inputs
- disclosure that the report is research and explanation, not an instruction or guarantee

### 4.2 Mandatory report sections

| Report section | Authoritative owner(s) | Analyst’s permitted use | Analyst must not do | Required absence behavior |
|---|---|---|---|---|
| **1. Scope and freshness** | SnapshotBuilder; market adapter; source metadata | State symbol, market, horizon, timestamps, currency, availability, and freshness. | Assume a market, horizon, exchange, or current price. | State the missing/ambiguous scope and stop the affected analysis. |
| **2. Current decision context** | Prediction Engine | Display the exact final signal, composite score, confidence, trade levels, and enforced-gate outcome already produced by the engine. | Recalculate, relabel, soften/harden, or replace the signal/confidence/score. | “Current decision output unavailable”; no substitute recommendation. |
| **3. Investment thesis** | Rule-based Explainability Layer; RCI/Evidence Summary where available; named engine outputs | Group supported evidence into a plain-language thesis with citations. | Create a new thesis fact that lacks evidence; treat RCI as an authority above its source engines. | Show only supported source evidence and clearly state incompleteness. |
| **4. Business quality** | Business Quality Engine | Explain approved quality, durability, governance, distress, or capital-allocation evidence. | Recompute quality factors, scores, gates, or financial-statement metrics. | Preserve owner status exactly; `FEATURE_DISABLED` is context, not negative evidence. |
| **5. Financial strength** | Financial Strength Intelligence | Explain approved leverage, coverage, cash-flow, balance-sheet, or solvency evidence. | Parse statements or calculate ratios independently. | Identify market/sector non-applicability and incomplete coverage without inference. |
| **6. Growth** | Growth Intelligence | Explain validated growth, quality-of-growth, reinvestment, or trend evidence. | Recalculate growth rates or use legacy fields as modern Growth Intelligence evidence. | Mark as missing/unavailable/not applicable according to owner output. |
| **7. Valuation** | Valuation Intelligence | Explain validated valuation perspective and constraints. | Calculate a second valuation model, fair value, or target price. | A disabled valuation feature must be disclosed as disabled, never interpreted as cheap/expensive. |
| **8. Technical/timing and trade framework** | Prediction Engine and existing technical/indicator owners | Explain existing indicators, entry logic, target/stop-loss values, risk/reward, and horizon context. | Calculate indicators, create new levels, or instruct execution. | State that the technical/trade framework is unavailable for the selected scope. |
| **9. Risks, counter-evidence, and conflicts** | Risk/gate owners; named engines; Explainability Layer; RCI conflict output where enabled | Present negative evidence, disagreement, active gates, and unresolved risks beside supportive evidence. | Hide, average away, or resolve conflict without evidence. | Explain what is missing and refuse a conclusive synthesis where needed. |
| **10. Thesis invalidation and monitoring conditions** | Existing deterministic explanation and recommendation outputs; owner-supplied conditions | State explicit already-defined invalidation conditions and what evidence would change the current conclusion. | Invent thresholds, dates, catalysts, or monitoring triggers. | Say no approved invalidation condition is currently available. |
| **11. Market and macro context** | Approved Global Market Context / market-regime owner | Provide only the owned, timestamped market-context evidence relevant to the selected market/horizon. | Generalize a macro fact into a stock-specific causal claim without evidence. | State that current context is unavailable or stale. |
| **12. Historical change narrative** | Persisted versioned snapshots and change-comparison service | Compare only two named snapshots and identify observed changes. | Reconstruct historical evidence from current live values or infer missing history. | Mark historical comparison as unavailable/legacy-insufficient. |
| **13. Portfolio context** | Future Portfolio Intelligence only, with authorized user context | Explain approved concentration, overlap, diversification, or exposure context. | Claim portfolio awareness before the approved portfolio context exists; access another user’s data. | Omit or state “portfolio context not connected for this report.” |
| **14. Consumer-specific status** | Consumer owner plus snapshot metadata | State whether information is live, persisted-at-generation, simulated, or screening-only. | Treat a paper trade as live execution or a screen pass as a recommendation. | Show the explicit consumer limitation. |
| **15. Evidence ledger and disclosures** | SnapshotBuilder and ReportAssembler | Render source ownership, evidence IDs, status, provenance, timestamps, and user-decision boundary. | Hide citations because prose is “obvious” or too long. | A report without a valid evidence ledger is invalid and must not render as grounded research. |

### 4.3 Minimum report order

A stock-level Research Report must appear in this logical order:

1. scope, freshness, and limitations;
2. current decision context;
3. direct answer or research question framing;
4. supportive evidence by named owner;
5. counter-evidence, active risks, and conflicts;
6. thesis invalidation / what could change;
7. consumer-specific context where permitted;
8. evidence ledger and disclosure.

No consumer may rearrange the report in a way that places a positive conclusion ahead of the corresponding risk, conflict, limitation, or unavailable-data disclosure.

---

## 5. Existing Engine Reuse and Zero-Duplication Contract

### 5.1 Registered source owners

Only an approved source owner may populate the corresponding report domain.

| Domain | Registered source owner | Analyst role |
|---|---|---|
| Final signal, composite score, confidence, target, stop loss, risk/reward, horizon decision | Prediction Engine | Read, cite, and explain only. |
| Deterministic bull/bear reasoning and metric-linked explanations | Rule-based Explainability Layer | Reuse verbatim or paraphrase with retained evidence identity. |
| Business quality / quality gates | Business Quality Engine | Reuse output and owner status only. |
| Financial strength | Financial Strength Intelligence | Reuse output and owner status only. |
| Growth | Growth Intelligence | Reuse output and owner status only. |
| Valuation | Valuation Intelligence | Reuse output and owner status only. |
| Additive evidence synthesis / conflicts | RCI Evidence Summary, where available and enabled | Reuse as a non-authoritative presentation input; retain source-engine traceability. |
| Global/market context | Approved market-context owner | Reuse with strict market/horizon/as-of labels. |
| Portfolio fit, concentration, overlap, allocation context | Future Portfolio Intelligence | Use only after separately approved implementation and authorization. |
| Daily Pick record and generated-at evidence | Daily Picks persistence owner | Use immutable generation-time snapshot only. |
| Multibagger screen membership and scorecard checks | Multibagger screen/scorecard owner | Describe as screening evidence only, not as a final stock recommendation. |
| Simulated position / paper-trade history | Paper Trading owner | Describe as simulation-only context, never as a trading instruction. |

A source engine may gain or lose registered-owner status only through an approved architecture and contract update. The Analyst must not create an informal alternate owner merely because a field is convenient to access.

### 5.2 Absolute prohibitions

The Analyst, SnapshotBuilder, ReportAssembler, LLM renderer, and consumer UI must not:

- instantiate a provider client or request raw provider data;
- call `yfinance`, scraper code, news retrieval, financial-statement retrieval, market-data retrieval, or arbitrary web search to complete a report;
- calculate RSI, MACD, EMA, ATR, valuation ratios, growth rates, debt ratios, quality factors, risk scores, target prices, stop-loss levels, or ranking values;
- re-run a source engine separately for each report section;
- use a legacy Daily Picks `growth_score` or `valuation_score` as modern Growth or Valuation Intelligence evidence;
- infer a result from the absence of a feature, a missing field, or an unsupported market/sector;
- write into a source-engine response object, cache entry, persisted Daily Picks record, portfolio record, or paper-trade record;
- create a derived number that could be interpreted as an investment score, probability, or recommendation.

### 5.3 Permitted presentation derivations

The Analyst may perform only the following deterministic presentation derivations:

- select an approved subset of evidence items for a requested topic;
- sort items by owner-provided severity, freshness, relevance tag, or explicit report priority;
- group evidence by domain, support, risk, conflict, or data state;
- format values already supplied by the owner using market-aware display utilities;
- compare two versioned snapshots on exact stored values;
- determine whether a referenced evidence item supports, weakens, conflicts with, or is insufficient for a claim using approved deterministic rules;
- create an evidence coverage summary that counts statuses without converting them into a score.

Any derivation outside this list requires an explicit contract amendment and new tests.

### 5.4 Cache safety

The shared prediction cache is a source-owned performance mechanism. Analyst construction must be copy-on-read:

- a snapshot is built from a defensive deep copy or a source-provided immutable view;
- no report field may be appended to a cached Prediction Engine object in place;
- Analyst output is never stored back into the core prediction cache;
- failure in report assembly, LLM rendering, or consumer rendering must not alter a valid cached prediction;
- cache-safety regression tests are mandatory before every Analyst release.

---

## 6. Shared Intelligence Snapshot Contract

### 6.1 Why one shared snapshot is mandatory

A stock may be consumed by Stock Detail, Portfolio, Daily Picks, Multibagger, Paper Trading, a future conversational interface, and future notification/explanation surfaces. These consumers must not obtain slightly different intelligence by separately recalculating or fetching it.

The `IntelligenceSnapshot` is the single reusable hand-off object for all Analyst work. It separates:

- **authoritative engine computation** from **report assembly**;
- **live state** from **historical persisted state**;
- **global stock intelligence** from **authorized user-specific portfolio context**; and
- **facts/evidence** from **LLM-created wording**.

### 6.2 Snapshot types

| Snapshot type | Purpose | Mutability and retention | Permitted consumers |
|---|---|---|---|
| **LiveIntelligenceSnapshot** | Current stock-level explanation for one symbol/market/horizon. | Immutable after build; cacheable according to owner freshness rules; not treated as historical proof after expiry. | Stock Detail, user-initiated Research Answers, Paper Trading read-only explanation. |
| **PersistedIntelligenceSnapshot** | Evidence captured with a meaningful historical event such as a Daily Picks generation, report publication, or recommendation-change record. | Append-only and versioned; never reconstructed from today’s values. | Daily Picks history, change narratives, audits, longitudinal research. |
| **PortfolioContextSnapshot** | User-specific approved portfolio data at a defined as-of time. | Separately authorized, access-controlled, versioned, and never embedded into a global stock snapshot. | Portfolio only; future portfolio-aware Analyst responses after separate approval. |
| **ScreeningContextSnapshot** | Immutable Multibagger screening/scorecard result for a defined refresh. | Versioned by screen definition and refresh; explicitly labelled screening-only. | Multibagger research explanations, comparison surfaces. |
| **SimulationContextSnapshot** | Paper-trade position state and simulation history. | Access-controlled, versioned, and clearly labelled simulated. | Paper Trading explanation and post-trade learning only. |

### 6.3 Required schema

The concrete transport format may evolve, but every snapshot must expose equivalent fields.

```json
{
  "contract_version": "1.x",
  "snapshot_id": "immutable-id",
  "snapshot_hash": "content-hash",
  "snapshot_type": "live|persisted|portfolio_context|screening_context|simulation_context",
  "scope": {
    "symbol": "canonical-symbol",
    "instrument_id": "stable-instrument-id",
    "market": "IN|US",
    "exchange": "canonical-exchange",
    "currency": "ISO-4217",
    "horizon": "short|medium|long|investment"
  },
  "timestamps": {
    "generated_at": "ISO-8601",
    "data_as_of": "ISO-8601",
    "expires_at": "ISO-8601-or-null"
  },
  "availability": {
    "overall_status": "COMPLETE|PARTIAL|STALE|UNAVAILABLE|INVALID",
    "owner_statuses": {}
  },
  "decision_context": {},
  "engine_outputs": {},
  "market_context": {},
  "evidence_items": [],
  "provenance": {},
  "consumer_context": {},
  "disclosures": []
}
```

### 6.4 Required evidence-item schema

Every material claim must map to one or more evidence items with fields equivalent to:

```json
{
  "evidence_id": "stable-within-snapshot-id",
  "owner": "PredictionEngine|BusinessQuality|FinancialStrength|Growth|Valuation|...",
  "domain": "decision|quality|financial_strength|growth|valuation|risk|macro|portfolio|screening|simulation",
  "status": "SUPPORTED|MISSING|UNAVAILABLE|NOT_APPLICABLE|FEATURE_DISABLED|STALE|EXECUTION_ERROR",
  "tier": "CONFIRMED|LIKELY|UNKNOWN",
  "claim_type": "fact|owner_conclusion|risk|counter_evidence|invalidation|limitation",
  "label": "human-readable label",
  "value": "typed owner-provided value or null",
  "display_value": "owner-approved display value or null",
  "source_reference": "provider/source category where permitted",
  "as_of": "ISO-8601-or-null",
  "captured_at": "ISO-8601",
  "market": "IN|US",
  "horizon": "short|medium|long|investment|not_applicable",
  "provenance_ref": "traceable upstream reference",
  "reason": "required for non-supported states",
  "invalidation_ref": "optional evidence id"
}
```

### 6.5 Snapshot validation rules

The `ContractValidator` must reject a snapshot when any of the following applies:

- no market, horizon, symbol, contract version, or `generated_at` is present;
- a material evidence item has no owner or identifier;
- a numerical factual claim has no matching typed source value;
- a `FEATURE_DISABLED`, `MISSING`, `UNAVAILABLE`, `NOT_APPLICABLE`, `STALE`, or `EXECUTION_ERROR` state lacks its required reason;
- evidence from another market, currency, symbol identity, or horizon has been mixed without explicit approved comparison context;
- a persisted snapshot lacks an immutable identifier or hash;
- user-specific portfolio or paper-trade data appears in a global stock snapshot;
- a consumer supplies a field that is not present in the validated snapshot.

An invalid snapshot produces no grounded report. The correct output is a bounded availability/error response, not a fabricated fallback report.

---

## 7. Explainability Contract

### 7.1 Mandatory qualities

Every material stock-specific explanation must:

1. name the selected symbol, market, horizon, and as-of context;
2. distinguish **confirmed platform evidence**, **bounded inference from named evidence**, and **unknown/unavailable information**;
3. include relevant counter-evidence, risks, active gates, and conflicts;
4. include data freshness and meaningful limitations;
5. identify what evidence or condition would change the conclusion when an approved invalidation condition exists;
6. preserve the separate meanings of recommendation, confidence, risk, conviction, and data completeness;
7. retain source-engine ownership; and
8. end with the user-decision boundary for any response that discusses a stock thesis, signal, or action-adjacent topic.

### 7.2 Claim classes

| Claim class | Allowed form | Evidence requirement |
|---|---|---|
| **Confirmed fact** | “The current long-horizon signal is HOLD.” | Exact owned value and evidence ID. |
| **Owner conclusion** | “The Valuation Intelligence output is unavailable for this scope.” | Owner status, reason, timestamp, and evidence ID. |
| **Bounded inference** | “Taken together, the available evidence supports a cautious thesis rather than a high-conviction one.” | Explicit cited supporting and counter-evidence; no new score or action. |
| **Unknown / unavailable** | “The platform does not currently have approved evidence to answer that.” | Explicit missing/unavailable state. |
| **Educational explanation** | “Price-to-earnings is a valuation multiple.” | Must be clearly educational and must not be framed as current stock evidence unless tied to a snapshot. |

### 7.3 Prohibited explanation patterns

The following are invalid even when they appear persuasive:

- a claim with no evidence ID or approved inference rule;
- a “because” statement that lacks a named owner value;
- combining multiple moderate signals into a stronger conclusion without an owner-approved rule;
- hiding an active gate, counter-evidence, conflict, stale input, or feature-disabled status;
- treating a Multibagger screen pass as a BUY recommendation;
- treating a paper-trade simulation result as live investment success;
- saying “the model believes” when the result is actually a deterministic engine output;
- saying “the Analyst recommends” as a substitute for the Prediction Engine’s signal;
- presenting target prices, stop losses, or timelines that the source owner did not produce.

### 7.4 Evidence presentation requirement

Every consumer must make it possible to inspect, at minimum:

- report/snapshot timestamp;
- market and horizon;
- named evidence owners;
- selected evidence items and status;
- freshness/availability limitations;
- counter-evidence or conflicts; and
- whether content is live, persisted-at-generation, screening-only, or simulated.

The exact UI may differ by consumer, but hiding the evidence ledger behind untraceable prose is prohibited.

---

## 8. Evidence Contract

### 8.1 Evidence is not a score

Evidence items are traceability units. Counting, ordering, or grouping them must not produce a synthetic score, confidence value, or recommendation.

### 8.2 Required evidence states

| State | Meaning | Required user-facing treatment |
|---|---|---|
| `SUPPORTED` | Owner supplied valid evidence for this scope. | May support or weaken a claim, subject to citations. |
| `MISSING` | Expected data field is absent. | State that the evidence is missing; do not infer direction. |
| `UNAVAILABLE` | Data/service could not supply evidence now. | State unavailable with reason if safely exposable; do not infer direction. |
| `NOT_APPLICABLE` | The evidence is not meaningful for this market, sector, instrument, or horizon. | State non-applicability; do not treat it as failure. |
| `FEATURE_DISABLED` | Capability exists but is deliberately inactive. | Disclose disabled state; never count it as adverse evidence. |
| `STALE` | Evidence exists but exceeds its permitted freshness policy. | Disclose staleness and limit confidence of related explanation; do not present as current. |
| `EXECUTION_ERROR` | A computation failed after valid invocation. | Isolate the failure and disclose bounded unavailability without raw internal error details. |

### 8.3 Provenance and freshness

For every time-sensitive evidence item, the snapshot must retain:

- data-as-of timestamp;
- snapshot capture timestamp;
- owner/component version where available;
- source/provenance category;
- applicable market and horizon;
- freshness classification under the owner’s policy; and
- status/reason when not supported.

The Analyst must not make a current-tense factual claim from stale evidence without an explicit stale-data disclosure in the same visible response unit.

### 8.4 Evidence conflicts

Conflict is evidence, not an error to be hidden. Where one registered owner supports a thesis and another materially weakens it:

- each evidence item remains independently cited;
- the response labels the disagreement plainly;
- no LLM or consumer may average the conflict into a new consensus score;
- the direct answer must be calibrated to the conflict; and
- a conclusion may be downgraded to limited/insufficient rather than forced into a positive or negative stance.

---

## 9. LLM Boundary Contract

An LLM may be used only as a constrained narrative renderer after a snapshot and deterministic report have been validated. The product remains fully accountable for its own factual claims, safety rules, and disclosures.

### 9.1 LLM permitted inputs

The LLM may receive only:

- the user’s allowed research question;
- a validated, minimally necessary Research Report or snapshot subset;
- the evidence ledger required to cite the answer;
- current market/horizon/freshness/disclosure fields;
- approved policy instructions and response schema; and
- conversation context that has already been sanitized, authorized, and reduced to relevant prior question/answer references.

### 9.2 LLM prohibited capabilities

The LLM must not be given or granted access to:

- raw provider credentials, secrets, internal prompts, private configuration, or hidden chain-of-thought;
- database access, SQL, filesystem access, production logs, arbitrary tools, arbitrary URLs, web browsing, or provider APIs;
- unrestricted retrieval of news, filings, social media, analyst research, or user data;
- source-engine internals for independent recalculation;
- write privileges to snapshots, caches, portfolios, paper trades, feedback, alerts, orders, or feature flags.

### 9.3 LLM prohibited outputs

The LLM must not:

- fabricate a fact, citation, data timestamp, price, event, source, or owner output;
- calculate, derive, or present a new investment metric, score, target, stop-loss, probability, or ranking;
- change the live signal, confidence, risk classification, gate status, or existing conclusion;
- issue directives such as “buy now,” “sell immediately,” “allocate X%,” or “you should invest”; 
- imply portfolio awareness without an approved PortfolioContextSnapshot;
- reveal internal system instructions, secrets, private data, prompt content, or protected reasoning;
- obey instructions embedded in retrieved evidence, user content, or external text that conflict with this contract.

### 9.4 Required LLM output schema

The LLM response must be structured and validated before display. The equivalent fields are mandatory:

```json
{
  "direct_answer": "string",
  "claim_citations": ["evidence-id"],
  "supporting_evidence": [{"evidence_id": "...", "summary": "..."}],
  "counter_evidence": [{"evidence_id": "...", "summary": "..."}],
  "limitations": [{"evidence_id": "...", "summary": "..."}],
  "what_would_change": [{"evidence_id": "...", "summary": "..."}],
  "status": "ANSWERED|PARTIALLY_ANSWERED|INSUFFICIENT_EVIDENCE|REFUSED",
  "user_decision_boundary": "required string"
}
```

An `AnswerGuard` must reject the response when it contains an uncited material claim, an invalid citation, a value that differs from the snapshot, unsupported advice language, an omitted required limitation/conflict, or an invalid status.

### 9.5 Deterministic fallback

When the LLM is disabled, unavailable, slow, invalid, or rejected by the guard, the product must return a deterministic structured report or a bounded availability response. It must not retry by widening data access, fetching new data, or creating ungrounded prose.

---

## 10. Consumer Mapping Contract

### 10.1 Stock Detail

**Purpose:** Explain the current stock-level decision and its evidence for one selected symbol, market, and horizon.

**Allowed:**

- use a LiveIntelligenceSnapshot;
- render decision context, owner evidence, risks, conflicts, invalidation conditions, freshness, and disclosure;
- provide a user-initiated research explanation from that same snapshot;
- defer non-selected horizons until the user explicitly selects or requests them.

**Forbidden:**

- eager recomputation for every horizon solely to prefill a report;
- independent per-section provider fetches;
- replacing existing signal, confidence, target, or stop-loss displays;
- adding hidden LLM-derived recommendation text;
- treating delayed or missing Analyst content as a failure of the underlying stock page.

**Required behavior:** Stock Detail remains usable if Analyst construction fails. Its report must label live versus stale data correctly.

### 10.2 Portfolio

**Purpose:** Explain an approved portfolio-aware interpretation of already-held symbols.

**Precondition:** Future Portfolio Intelligence and an authorized PortfolioContextSnapshot must exist, be separately approved, and pass access-control review.

**Allowed after precondition:**

- join a user’s authorized PortfolioContextSnapshot to immutable stock snapshots;
- explain registered portfolio evidence such as concentration, overlap, exposure, and approved risk context;
- distinguish a stock’s standalone evidence from its portfolio-fit evidence.

**Forbidden:**

- claiming diversification, suitability, allocation quality, or portfolio fit without approved portfolio evidence;
- reusing one user’s context for another user;
- using portfolio context to change a live stock signal unless the owning portfolio/recommendation architecture separately authorizes it;
- creating allocation instructions or trade execution instructions.

### 10.3 Daily Picks

**Purpose:** Explain why a pick was generated at the time of that Daily Picks run.

**Required source:** a PersistedIntelligenceSnapshot captured at generation time and associated with the immutable pick record.

**Allowed:**

- explain the specific stored evidence, scope, and status present at the time of the pick;
- compare two persisted snapshots when both are present;
- clearly label a pick’s historical generation time and availability limitations.

**Forbidden:**

- regenerating a Daily Pick explanation from current live data and presenting it as historical evidence;
- using legacy Daily Picks `growth_score` or `valuation_score` as modern engine evidence;
- backfilling old records by guesswork;
- modifying Daily Picks ranking, selection, scheduler behavior, or the pick record while serving an explanation.

**Required legacy behavior:** Historical records without a valid structured intelligence snapshot must be labelled **Legacy — insufficient evidence for modern Research Analyst reconstruction**.

### 10.4 Multibagger

**Purpose:** Explain a screening result and scorecard evidence, not a final recommendation.

**Allowed:**

- use a ScreeningContextSnapshot tied to a named screen definition, market, refresh time, and scorecard version;
- explain pass/fail checks, red flags, stated methodological limitations, and screen membership;
- combine screen evidence with a separate stock-level snapshot only when sections remain clearly labelled by their respective owners.

**Forbidden:**

- portraying a screen pass as a BUY signal, outcome validation, or expected return;
- recalculating scorecard checks from raw data;
- silently equating a screening score with Prediction Engine confidence or recommendation;
- concealing a known screen limitation or unavailable business-quality input.

### 10.5 Paper Trading

**Purpose:** Explain the evidence available at simulated trade creation, while a position is open, and after simulated closure.

**Allowed:**

- show a LiveIntelligenceSnapshot for current read-only research;
- store a SimulationContextSnapshot referencing the snapshot version used when a user manually opens a paper trade;
- explain differences between the historical simulated-entry context and current evidence;
- label all simulation data, returns, triggers, and outcomes as simulated.

**Forbidden:**

- using Analyst output to place, close, resize, or automatically trigger a trade;
- claiming that a paper-trade result proves a live-trading outcome;
- opening a position because of an LLM instruction;
- retroactively overwriting historical simulated-entry evidence with live data.

### 10.6 Future conversational AI

**Purpose:** Answer a bounded user question against a selected validated snapshot/report.

**Allowed:**

- call the ResearchAnswerService with a question, approved intent, snapshot ID, and authorized context reference;
- preserve conversations as references to immutable report/snapshot versions rather than mutable “facts” carried forward indefinitely;
- rebind a new question to a current snapshot and explicitly state when it differs from an earlier snapshot.

**Forbidden:**

- free-form browsing, autonomous research, unrestricted retrieval, or tool use;
- treating conversation history as a current market-data source;
- silently carrying prior conclusions into a new market/horizon/snapshot;
- portfolio-aware answers before Portfolio Intelligence is implemented and authorized.

---

## 11. Performance and Scalability Contract

### 11.1 Fundamental rule

A Research Report must be assembled from one validated snapshot per `(instrument identity, market, horizon, snapshot purpose, source-version scope)`.

A report section must never cause its own engine evaluation or provider retrieval. A conversational answer must never cause a second independent snapshot build when a valid matching snapshot already exists in the request scope.

### 11.2 Required performance properties

1. **One underlying stock evaluation per live scope.** For a live Stock Detail request, source computation must be reused across decision context, all evidence sections, report assembly, and answer generation. No section-level calls are permitted.

2. **No provider fan-out from the Analyst.** The Analyst adds zero raw market-data, financial-statement, or provider calls once a valid source snapshot exists.

3. **No cache mutation.** Report construction uses a defensive immutable copy/view. It cannot degrade concurrent Prediction Engine, Daily Picks, or other consumers.

4. **No eager all-horizon work.** A consumer must not build reports for unselected horizons solely for convenience. Additional horizons are built only after an explicit user interaction or an approved batch use case.

5. **Batch architecture for portfolios.** Portfolio views must use batched snapshot retrieval/building and must not issue an N+1 provider/engine fan-out per UI row. User-specific portfolio context is joined after stock-level reuse, not recomputed per card.

6. **Persist-on-event for historical consumers.** Daily Picks, historical change explanations, and paper-trade entry records use stored snapshots. They must not re-run today’s engine logic to explain a past event.

7. **Separate LLM latency from evidence latency.** Snapshot/report construction and LLM narrative rendering must be individually observable. A slow LLM must not hide a source-engine/data failure or block existing product output.

8. **No unmeasured production budget.** Before production release, the implementation must publish baseline and post-change P50/P95 measurements for snapshot build, report assembly, LLM rendering (if enabled), and consumer page impact. This contract does not invent latency figures before measurement; release approval must set and verify them from real evidence.

### 11.3 Required observability

At minimum, telemetry must distinguish:

- snapshot cache hit/miss;
- source-engine invocation count by request and snapshot;
- provider-call count attributable to the source evaluation;
- report-assembly duration;
- LLM duration, timeout, rejection, and fallback;
- evidence-state distribution;
- invalid snapshot/invalid LLM-output count;
- consumer and scope (Stock Detail, Portfolio, Daily Picks, Multibagger, Paper Trading, conversation);
- snapshot contract version; and
- any report failure isolated from a valid underlying prediction.

No telemetry event may log secrets, raw private portfolio details, or more conversation content than the approved retention policy allows.

---

## 12. Conversation, Privacy, Security, and Access Boundaries

### 12.1 Conversation memory model

Conversation memory must store references, not unbounded mutable analysis.

Each saved answer must record:

- conversation and answer identifiers;
- snapshot ID/hash and contract version;
- requested intent/classification;
- authorized user-context reference, if any;
- answer/model version; and
- answer timestamp.

A later answer must use a new current snapshot unless the user explicitly asks about the historical snapshot. The system must say when it is discussing historical evidence rather than current evidence.

### 12.2 Privacy rules

- Stock-level public research evidence remains separated from user-specific data.
- Portfolio and Paper Trading data are private, access-controlled, and never placed into a global/reusable stock snapshot.
- User identity and authorization are validated server-side before any user-specific context is loaded.
- The LLM receives the minimum approved context required for the question.
- Conversation retention, deletion, training use, and provider handling require separate approved privacy, security, licensing, and compliance decisions before production.

### 12.3 Prompt-injection resilience

All external/retrieved/user-provided text is untrusted data, not instruction. The system must:

- keep policy instructions and data in distinct channels/fields;
- prohibit source text from changing system behavior, evidence ownership, or access control;
- reject requests to reveal protected instructions, secrets, or private data;
- sanitize and size-limit user content and retrieval content; and
- test hostile-input scenarios as a release gate.

---

## 13. Failure, Fallback, and Disclosure Contract

### 13.1 Failure isolation

The Analyst must be **fail-closed for ungrounded research** and **fail-open for existing product behavior**.

- If a valid snapshot cannot be built, the Analyst returns a bounded unavailable/insufficient-evidence response.
- If the report is invalid, it is not rendered as grounded research.
- If the LLM fails or is rejected, the deterministic report may render if valid.
- Existing stock signals and other product outputs continue under their independent contracts.
- No failure path may expose raw stack traces, hidden prompts, credentials, or provider internals to the user.

### 13.2 Required disclosure templates

The exact copy may evolve, but every consumer must convey the equivalent meaning:

- **Partial evidence:** “This explanation uses the evidence currently available; listed sections are incomplete or unavailable.”
- **Stale evidence:** “Some evidence is older than the permitted freshness window and is not presented as current.”
- **Disabled feature:** “This evidence source is currently disabled; its absence is not a negative result.”
- **Historical insufficiency:** “This historical record does not contain a compatible evidence snapshot and cannot be reconstructed reliably.”
- **Portfolio boundary:** “Portfolio context is not connected for this answer.”
- **User-decision boundary:** “This is research and explanation, not a guarantee or an instruction to trade.”

---

## 14. Required Test and Evaluation Contract

No Analyst release is complete without tests proving the contract, not merely the UI.

### 14.1 Deterministic engineering tests

Required automated coverage includes:

- snapshot schema validation for every evidence state;
- market, currency, symbol identity, and horizon mismatch rejection;
- source-owner mapping validation;
- no-direct-provider-access structural test;
- no-duplicate-engine/provider-call test;
- cache immutability and concurrent-reader safety test;
- report section ownership and absence-behavior tests;
- preserved distinction among `MISSING`, `UNAVAILABLE`, `NOT_APPLICABLE`, `FEATURE_DISABLED`, `STALE`, and `EXECUTION_ERROR`;
- legacy Daily Picks non-reconstruction test;
- persisted-snapshot immutability test;
- screen-pass-is-not-recommendation test for Multibagger;
- simulation-is-not-live-execution test for Paper Trading;
- authorization isolation for portfolio/paper-trade context;
- deterministic fallback test when the LLM is disabled or fails;
- no-existing-product-regression test for Stock Detail, Daily Picks, Prediction Engine, and cache behavior.

### 14.2 LLM evaluation gate

Before any LLM-enabled release, a reproducible evaluation suite must test:

- factual grounding and citation validity;
- numerical fidelity to the snapshot;
- stale-data disclosure;
- missing/disabled/not-applicable state handling;
- conflict and counter-evidence inclusion;
- hallucination resistance;
- unsafe action-language refusal;
- prompt-injection resistance;
- privacy leakage prevention;
- consistency across repeated identical inputs;
- India/US market distinctions;
- horizon distinctions;
- portfolio-boundary abstention;
- adversarial requests for certainty, exact trade timing, allocation, or guarantees; and
- model/provider failure behavior.

An evaluation pass must be versioned with the prompt/policy, snapshot schema, response schema, model configuration, and result set. Passing a prior evaluation is not evidence that a materially changed model, prompt, source contract, or consumer remains safe.

---

## 15. Versioning, Auditability, and Change Control

### 15.1 Versioning requirements

The following must be versioned independently:

- snapshot contract;
- evidence schema;
- owner registry;
- report architecture;
- LLM prompt/policy and response schema;
- model/provider configuration;
- consumer renderer contract;
- evaluation suite;
- persisted snapshot content/hash.

A change that alters a factual field, evidence status, owner mapping, section meaning, or response behavior is a contract change, not a cosmetic change.

### 15.2 Audit record

For each report/answer, retain the minimum approved audit record needed to reproduce what was shown:

- snapshot ID/hash and contract version;
- consumer, market, symbol, horizon, and timestamps;
- evidence IDs referenced;
- response status and fallback/refusal reason;
- report/LLM/policy version; and
- authorization context reference where user-specific context was used.

### 15.3 Contract changes

A change to this contract requires:

1. a written rationale and scope;
2. evidence that the change does not violate source ownership or duplicate calculations;
3. impacted-consumer analysis;
4. schema migration/backward-compatibility plan for persisted snapshots;
5. updated deterministic tests and, for LLM changes, updated evaluation evidence;
6. explicit documentation updates; and
7. release approval appropriate to the scope.

No implementation may silently widen Analyst authority, data access, provider access, or consumer permissions.

---

## 16. Definition of Done for Any Future Analyst Implementation Phase

A future implementation phase satisfies this contract only when all applicable items are true:

- [ ] It consumes registered owner outputs through a validated shared snapshot.
- [ ] It performs zero duplicate calculations and zero direct provider retrievals.
- [ ] It does not mutate Prediction Engine output or shared caches.
- [ ] It preserves Prediction Engine authority for signal, score, confidence, trade levels, and gates.
- [ ] Every report section has an explicit owner and defined absence behavior.
- [ ] Every material factual claim is traceable to evidence IDs or approved deterministic inference rules.
- [ ] Risks, counter-evidence, conflicts, freshness, and limitations are shown beside supportive evidence.
- [ ] Market, horizon, currency, timestamps, and data states are preserved end-to-end.
- [ ] Live, persisted historical, screening-only, simulation-only, and portfolio-specific contexts remain distinct.
- [ ] Legacy data is never reconstructed as modern evidence without an immutable compatible snapshot.
- [ ] Portfolio context is unavailable until separately approved and remains access-controlled afterward.
- [ ] LLM use, if any, is bounded by the input/output, tool, privacy, safety, and evaluation rules in this document.
- [ ] A deterministic fallback exists and a failure cannot disrupt existing product behavior.
- [ ] Performance baselines and post-change measurements are documented and show no unapproved fan-out or regression.
- [ ] Automated contract, cache-safety, consumer-boundary, and evaluation tests pass.
- [ ] Feature flags, observability, rollback, user disclosures, operational ownership, privacy/security review, data licensing review, and compliance review are approved for the intended release scope.

Until every applicable checkbox is satisfied, the capability remains incomplete and must not be represented as a grounded AI Research Analyst.

---

## 17. Explicit Non-Authorization

This Engineering Contract does not itself authorize:

- implementation code;
- LLM provider selection or integration;
- chatbot UI;
- external web retrieval or browsing;
- new data-provider usage;
- new data licensing or retention;
- portfolio-aware analysis;
- broker integration or trade execution;
- automatic paper-trade actions;
- changes to Prediction Engine, Daily Picks, Multibagger, Portfolio, Paper Trading, RCI, scheduling, or production flags;
- a production rollout; or
- any claim that an AI Equity Research Analyst exists in StockSense360 today.

It defines the engineering boundary that every future approved phase must obey.
