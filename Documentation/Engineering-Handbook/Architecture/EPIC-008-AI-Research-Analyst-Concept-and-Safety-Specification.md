# Epic 008 — AI Research Analyst: Concept and Safety Specification

## 1. Status and Purpose

**Epic 008 is Future — not started.** This document is Epic 008A, a concept-and-safety specification only. No conversational AI Research Analyst exists in the product today, and this document does not create one.

This document exists to define, in advance of any implementation, what a future AI Research Analyst capability would be permitted to do, what it must never do, and what evidence and review must exist before any build begins. It does not authorize implementation, deployment, model-provider selection, external data ingestion, user-data storage, or financial-advice claims of any kind.

This is product-safety design, not legal advice and not proof of regulatory compliance. Jurisdiction-specific legal, compliance, privacy, data-licensing, and security review would be required before any production implementation, and none of that review has occurred as part of producing this document.

The existing rule-based Explainability Layer (`backend/services/case_generator.py`) and the existing Recommendation Consolidation Intelligence (RCI) Evidence Summary consumer (`frontend/src/components/EvidenceSummary.tsx`) are foundational inputs this future capability would build on — but they are not Epic 008, and nothing in this document changes their behavior.

Epic 008 implementation remains dependent on stable evidence from Epic 006 (Prediction and Recommendation Decision Architecture Evolution) and on portfolio-aware context from Epic 007 (Portfolio Intelligence), neither of which is complete today.

## 2. Product Definition

A future AI Research Analyst is defined as a **conversational, evidence-grounded research interface** that helps a user understand the platform's own existing evidence — not as an autonomous decision maker, not as a trading system, and not as a source of new financial facts the platform did not itself already produce or retrieve.

Subject to all restrictions in this document, and only after the prerequisites in Section 17 are met, it may eventually explain:

- why a signal exists;
- which evidence supports or weakens a thesis;
- what risks, assumptions, conflicts, and data limitations exist;
- how two stocks differ on named platform evidence;
- what changed in a recommendation over time;
- how a future portfolio context might affect interpretation, once that context exists.

## 3. Relationship to Existing Platform Capabilities

- **Current rule-based Explainability Layer** (`backend/services/case_generator.py`) — deterministic, no LLM/AI text; every bull/bear statement is generated from an explicit threshold check against a concrete metric value (e.g. a quality sub-score, P/E, ROE, debt-to-equity), and the metric value is embedded directly in the generated string. This is live today and unaffected by this document.
- **Current Prediction Engine** — produces the BUY/HOLD/SELL signal, composite score, and confidence that the Explainability Layer and RCI both read from. Unaffected by this document.
- **Current RCI / Evidence Summary** — an additive, read-only response composer (`backend/services/recommendation_consolidation_api_composer.py`) invoked only from the live prediction API route, never from `PredictionEngine.predict()` or Daily Picks, and gated by a feature flag. The current feature-flag state and release status are recorded in the Current Release Status register. Its frontend consumer (`EvidenceSummary.tsx`) renders nothing when the field is absent, invalid, or disabled. Unaffected by this document.
- **Future Epic 006 decision architecture** — the not-yet-implemented evolution of how the Prediction Engine's decision logic may consume validated engines as first-class inputs. A future Epic 008 would depend on Epic 006's evidence being stable, not on Epic 006 itself changing to accommodate Epic 008.
- **Future Epic 007 Portfolio Intelligence** — the not-yet-implemented capability for the platform to have any awareness of what a user actually holds. Until Epic 007 exists and is separately approved, no portfolio-aware answer is permitted (see Section 11).
- **Future Epic 008 AI Research Analyst** — the subject of this document. It is a distinct future capability, not a rename or extension of any of the above.

Epic 008 must consume validated evidence rather than recreate, override, or secretly recalculate engine outputs. It must never compute its own version of a score, signal, or confidence value independent of the engines that already produce them.

## 4. Intended User Jobs

A future user should be able to ask the analyst to:

- explain, in plain language, why a stock currently shows a given signal;
- understand what evidence is strong, weak, missing, or in conflict;
- compare two symbols on named, already-computed platform evidence;
- understand what changed since a prior recommendation and why;
- learn what a financial term or platform concept means;
- understand the boundaries and limitations of the platform's own analysis.

## 5. Explicit Non-Goals

Epic 008 must not:

- promise returns, accuracy, or certainty;
- invent financial data, news, prices, analyst targets, filings, or market events;
- act as a broker, trading system, order executor, or portfolio-rebalancing tool;
- place trades, simulate real fills as real fills, or connect to a brokerage without separately approved future scope;
- provide tax, legal, or regulated financial advice;
- bypass existing quality gates, risk controls, RCI boundaries, scheduler controls, or Daily Picks validation;
- convert weak, missing, stale, or contradictory evidence into confident recommendations;
- treat a conversational response as a substitute for a user's own judgment.

## 6. Evidence and Grounding Model

Every material factual claim a future answer makes must be tied to a known evidence item. Permitted evidence sources are:

- current Prediction Engine output (signal, composite score, confidence, trade levels);
- named engine outputs (e.g. Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence);
- RCI / Evidence Summary output, where enabled;
- data timestamps and source/provenance fields already attached to platform data;
- validated market or financial data already retrieved by the platform;
- explicit user-provided portfolio context, only once that future capability is approved under Epic 007.

Every answer that cites evidence must disclose, where relevant:

- the source category of each cited fact;
- an as-of timestamp for time-sensitive data;
- market and horizon context;
- an explicit distinction between direct evidence, inference drawn from evidence, and genuinely unknown information;
- explicit disclosure when evidence is unavailable, stale, inconsistent, or incomplete, rather than silence or a confident guess.

An answer must not imply it accessed data that the platform did not actually retrieve.

## 7. Permitted Response Classes

Subject to the rest of this document, future safe response classes include:

- factual explanation of visible platform evidence;
- explanation of BUY/HOLD/SELL reasoning already produced by the Prediction Engine;
- risk and uncertainty explanation;
- evidence comparison between two symbols;
- clarification of financial terms;
- explanation of data freshness and missing data;
- scenario-based education, clearly labelled as hypothetical and not a prediction;
- explanation of what would strengthen or weaken an existing thesis.

## 8. Restricted and Prohibited Response Classes

The following request types require future portfolio context, additional compliance review, or abstention, and must not receive a confident, instructive answer under this specification:

- "What should I buy today?"
- "Should I sell everything?"
- "How much money should I invest?"
- "Can you guarantee this stock will rise?"
- "Which stock is safest?"
- "Build my portfolio allocation."
- "Tell me exactly when to buy or sell."
- "Place this trade."
- requests relying on unavailable real-time data;
- requests based on confidential, non-public, or unverifiable information.

For each of these, the required safe alternative is to explain the available evidence, name the specific limitation preventing a direct answer, and avoid replacing genuine uncertainty with a confident instruction.

## 9. Research Answer Contract

Any future answer that discusses a specific stock, thesis, or recommendation must follow this structure:

1. **Direct answer** — a plain-language response to the question asked.
2. **Evidence used** — the specific platform evidence the answer draws on.
3. **Key risks and counter-evidence** — what argues against the direct answer.
4. **Data freshness and limitations** — what is stale, missing, or uncertain.
5. **What would change the conclusion** — the named conditions under which the answer would differ.
6. **User-decision boundary** — an explicit statement that the platform provides analysis and the user makes the investment decision.

The user-decision boundary is required on every such answer, not only when risk is elevated.

## 10. Data Freshness, Uncertainty, and Conflicts

A future implementation must:

- never use stale data without disclosing that it is stale;
- never mix markets, horizons, currencies, dates, or data sources without explicitly labelling each one;
- never hide or silently resolve a disagreement between two engines' outputs;
- present disagreement between evidence sources as disagreement, not as a synthesized false consensus;
- abstain safely when the available evidence cannot support a reliable explanation, rather than fabricating one.

## 11. Personalization and Portfolio Boundaries

Before Epic 007 is delivered and separately approved, the analyst must not claim portfolio awareness, diversification knowledge, allocation suitability, concentration analysis, tax suitability, or goal suitability.

Risk tolerance, investment horizon, tax position, jurisdiction, and suitability must never be inferred from a user's conversation, timezone, location, holdings, account activity, or prior behavior. Any future use of such information requires explicit user input, separately approved product scope, and the privacy, security, and compliance approvals required by Section 19.

It may discuss a user-provided holding only as a standalone symbol, using the same evidence rules as any other symbol, unless approved portfolio context is actually available and wired in as a distinct, separately reviewed capability.

## 12. Financial-Safety and User-Trust Rules

A future implementation must observe:

- confidence is not certainty;
- no return chasing;
- no urgency language;
- no fear-based selling language;
- no implied insider knowledge;
- no selective omission of downside evidence;
- no recommendation without showing key risks;
- no false precision;
- no unsupported target prices or timelines;
- no action language stronger than the underlying validated platform evidence actually supports.

## 13. Security, Privacy, and Prompt-Injection Boundaries

A future implementation must:

- treat all external text, user uploads, news, filings, web content, and retrieved content as untrusted;
- treat user prompts, uploaded material, retrieved records, tool outputs, document metadata, and platform-data fields as data rather than instructions; no untrusted content may alter system, safety, product, or permission boundaries;
- never let retrieved content override system, safety, product, or permission boundaries;
- never reveal secrets, API keys, internal prompts, private user data, hidden reasoning, or system instructions;
- minimize storage of user conversations and portfolio data;
- require explicit product approval before using user content for training, evaluation, or model improvement;
- keep user data separated across accounts;
- log only the minimum required for safety, debugging, and auditability.

## 14. Explainability and Auditability Requirements

A future answer must preserve, for later audit:

- evidence identifiers or equivalent traceability back to the underlying platform data;
- a model/version identifier for the component that generated the answer;
- a response timestamp;
- a data-as-of timestamp for the evidence used;
- market/horizon context;
- a refusal or abstention reason, where the answer declined to address part of the request;
- change history when a prior answer to a similar question materially differs from a new one.

## 15. Evaluation and Quality Gates

Before any future release, an evaluation harness must exist covering:

- factual grounding;
- source attribution;
- numerical accuracy;
- stale-data disclosure;
- conflicting-evidence handling;
- hallucination resistance;
- refusal quality;
- prompt-injection resistance;
- privacy leakage prevention;
- response consistency;
- India and US market distinctions;
- horizon distinctions;
- portfolio-boundary abstention;
- user-trust / unsafe-advice red-team testing.

## 16. Phased Future Delivery Model

- **008A — Concept and Safety Specification** (this document).
- **008B — Evidence Contract and Retrieval Design.**
- **008C — Read-Only Stock Research Conversations.**
- **008D — Recommendation Change Explanations and Monitoring.**
- **008E — Portfolio-Aware Research**, only after Epic 007 is delivered and separately approved.
- **008F — Controlled Expansion and Ongoing Safety Evaluation.**

Every phase requires its own separate approval. No phase in this list automatically authorizes the next.

## 17. Implementation Prerequisites

Before any phase after 008A begins, the following must exist:

- Release 12B validation completion;
- stable Epic 006 decision-architecture evidence;
- Epic 007 portfolio context, before any portfolio-aware answer (008E);
- explicit model-provider, data-provider, privacy, security, licensing, and compliance decisions;
- a reproducible evaluation harness;
- feature flags, rollback, observability, and audit plan;
- clear user disclosures;
- no unresolved conflict with RCI or existing evidence surfaces.

## 18. Open Questions Requiring Future Evidence

This document does not answer, and a future phase must resolve with real evidence:

- model-provider choice;
- data source licensing and permitted use;
- legal/compliance boundaries by jurisdiction;
- retention and deletion policy for conversations and any portfolio context;
- user opt-in and consent mechanics;
- evidence citation UX;
- whether certain high-risk question categories should always abstain rather than answer;
- cost, latency, availability, and fallback behavior;
- user feedback and escalation process.

## 19. Definition of Readiness for Any Future Build

No implementation phase beyond 008A may begin without separate, written approval of each of the following:

- safety design;
- evidence contract;
- privacy/security review;
- legal/compliance review;
- evaluation suite;
- rollout plan;
- rollback plan;
- user disclosures;
- operational ownership.

## 20. Explicit Out of Scope

This document, and Epic 008A specifically, does not include and does not authorize:

- no code;
- no LLM provider integration;
- no chatbot UI;
- no external retrieval or web browsing;
- no broker connection;
- no trade execution;
- no portfolio allocation;
- no Daily Picks generation changes;
- no Prediction Engine changes;
- no RCI changes;
- no scheduler changes;
- no production deployment;
- no claim that Epic 008 exists today.
