# SSDS-010 — Transaction-Cost and Estimated-Tax P&L

**Status:** Proposed — future work; documentation only; implementation not authorized.  
**Prepared:** 2026-08-19  
**Markets:** India and United States, modeled independently.  
**Governed by:** SES-001 through SES-006.  
**Related surfaces:** Paper Trading, Portfolio, Dashboard, Daily Picks outcome measurement.  
**Active-work boundary:** This proposal is separate from, and must not expand, the current Daily Picks conviction-accuracy evidence reconciliation.

---

## 1. Problem Statement

StockSense360 can show a price-based gain or loss without consistently accounting for the costs incurred when a position is bought, sold, or converted between currencies. That is not an exact representation of investor P&L. India and the United States have different broker charges, exchange and regulatory levies, tax rules, holding-period definitions, account types, and user-residency effects. A single hard-coded percentage would therefore be misleading.

The product needs an auditable calculation that distinguishes:

1. market-price performance;
2. performance after trading and FX costs; and
3. a separately labeled, user-specific estimate after tax.

The system must also preserve each deliberate Buy as an independent immutable trade lot. Repeated buys in the same symbol must never be averaged together in storage or treated as duplicates.

## 2. Goal and Non-Goals

### Goal

Provide a future implementation contract for exact, explainable P&L for India and U.S. holdings by:

- recording actual charges when known and estimating only missing charges;
- applying versioned, effective-dated cost rules independently by market, broker, venue, instrument, and trade date;
- maintaining immutable tax lots and explicit sell-to-lot allocations;
- showing Gross, After-Cost, and Estimated After-Tax P&L as distinct dashboard layers;
- retaining the rule version, data source, calculation timestamp, and actual-versus-estimated status for every component; and
- reserving after-cost outcome fields so model audits can measure realistic returns without mixing in personal tax circumstances.

### Non-goals

This document does **not** authorize:

- any code, schema, migration, scheduled job, model, ranking, gate, feature flag, deployment, or historical backfill;
- a change to Daily Picks BUY logic, conviction, learning, or the active accuracy audit;
- tax filing, tax advice, guaranteed tax liability, or support for every possible jurisdiction at launch;
- merging repeated purchases into one lot;
- fabricating unknown costs as zero;
- using a personalized tax estimate to evaluate or advertise model performance; or
- silently recalculating previously displayed history under new rules.

## 3. Affected Surfaces

| Surface | Future impact |
|---|---|
| Backend | A pure cost-calculation service, a separate tax-estimation service, lot-allocation logic, FX-rate provenance, and read APIs. These services must not import or alter Selection Engine ranking logic. |
| Frontend | Portfolio, Paper Trade, trade detail, and performance dashboard views with a P&L-layer selector and itemized breakdown. |
| Data | Private, owner-scoped records for rule versions, broker profiles, immutable lots, cost breakdowns, lot allocations, tax profiles, tax estimates, and FX provenance. Names in this document are logical contracts, not approved schemas. |
| India | Indian brokerage, exchange/regulatory levies, securities transaction tax, GST, stamp duty, depository charges, FX where applicable, and India-specific tax estimation. |
| United States | Commissions, exchange/regulatory fees, FX, and U.S.-specific federal/state/non-resident tax estimation. |
| Daily Picks audit | Additive future outcome fields only. After-cost, pre-personal-tax returns become the realistic comparison layer; existing raw outcomes remain reproducible. |

## 4. Design

### 4.1 P&L layers

The dashboard must never collapse unlike concepts into one number.

| Layer | Definition | Use |
|---|---|---|
| Gross P&L | Sale proceeds minus acquisition notional, using actual fills where available | Pure price movement |
| After-Cost P&L | Gross P&L minus buy-side costs, sell-side costs, and applicable FX costs | Investor trading result and model-performance audit |
| Estimated After-Tax P&L | After-Cost P&L minus a versioned, profile-specific estimated tax liability | Personal planning aid only |

For an open position, every layer is unrealized and must be labeled as such. For a partial close, realized and unrealized portions must be presented separately.

Taxable gain is jurisdiction-specific and must be calculated by the tax estimator from its own rules. It must not be assumed to equal After-Cost P&L.

### 4.2 Cost-component contract

Each component requires:

- market and account/broker profile;
- trade or settlement date, as required by the applicable rule;
- side, instrument type, quantity, price, and currency;
- rule identifier and immutable rule version;
- calculation basis, rate or fixed amount, rounding rule, and resulting amount;
- source provenance and effective dates;
- status: actual, broker-reconciled, estimated, unavailable, or not applicable;
- calculated-at timestamp and calculator version; and
- supersession linkage when an estimate is later replaced by an actual broker charge.

Actual broker-reported charges are authoritative for user P&L. Rule-engine estimates remain available for audit and variance analysis; they are not overwritten.

### 4.3 India cost engine

The India ruleset must model applicable components independently, including:

- brokerage and broker minimum/maximum rules;
- securities transaction tax;
- exchange transaction charges;
- SEBI turnover charges;
- GST on applicable services;
- stamp duty;
- depository participant charges;
- instrument/product distinctions such as delivery versus intraday; and
- FX conversion when the user's reporting currency differs.

No current rate belongs in application code without an effective-dated rule record and an authoritative-source reference. Initial implementation research must re-verify the rules then in force using official sources such as the [NSE levy guide](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies) and [NSE stamp-duty guide](https://www.nseindia.com/static/invest/first-time-investor-stamp-duty-charges-taxes).

### 4.4 United States cost engine

The U.S. ruleset must model applicable components independently, including:

- broker commissions and contract/account-specific fees;
- SEC Section 31 fees when applicable;
- FINRA Trading Activity Fee when applicable;
- exchange, clearing, and other regulatory charges;
- FX conversion and explicit currency-conversion fees; and
- spread/slippage estimates for simulated trades.

An actual fill price already contains market slippage. Estimated slippage must therefore be used only for simulations or pre-trade scenarios and must never be charged again against an actual fill. Effective-dated regulatory sources must be retained, including the [SEC fee-rate advisories](https://www.sec.gov/rules-regulations/fee-rate-advisories).

### 4.5 Tax estimation

Tax is a separate, opt-in estimation layer because it depends on facts that are not properties of the stock signal.

A future tax profile may include:

- tax residency and applicable jurisdiction;
- tax year;
- account type;
- filing-status or tax-bracket inputs where relevant;
- state or local jurisdiction where relevant;
- India surcharge/cess inputs where relevant;
- currency and FX convention;
- lot-disposal method or broker-confirmed lot election; and
- user override with effective date and provenance.

The estimator must support at least:

- India short-term versus long-term classification, annual aggregation and thresholds, and current statutory additions;
- U.S. short-term versus long-term classification, account treatment, federal/state distinctions, loss carryovers, and wash-sale implications;
- separate treatment for dividends and withholding rather than mixing them into trade P&L; and
- an Unavailable result when the supplied profile is insufficient.

Authoritative starting references include India's Income Tax Department guidance for [short-term capital gains](https://www.incometaxindia.gov.in/w/tax-on-short-term-capital-gains%E2%80%8B) and [sale of shares](https://www.incometaxindia.gov.in/sale-of-shares), and [IRS Topic 409](https://www.irs.gov/taxtopics/tc409). All rates, thresholds, holding-period definitions, and effective dates must be revalidated before implementation and before each annual rule release.

User-facing copy must say **Estimated tax — not tax advice** and identify the profile and rule version used.

### 4.6 Immutable trade-lot invariant

Each deliberate Buy creates one immutable tax lot, including repeated purchases of the same symbol on the same day.

- Storage must not average, merge, deduplicate, or replace lots.
- Aggregated average cost may be derived for display only.
- A Sell creates explicit allocation records from sell quantity to one or more lots.
- Partial sells preserve the remaining quantity on each original lot.
- Broker-confirmed lot elections override default allocation rules.
- If no election is known, the configured method is applied and recorded; the system must not silently assume a universal FIFO rule.
- Corrections are append-only supersessions with an audit trail, not in-place rewrites.

### 4.7 Logical data contracts

The following names describe responsibilities only; a future approved sprint must inspect the existing schema and choose final names.

| Logical contract | Responsibility |
|---|---|
| cost_rule_versions | Immutable effective-dated rules by market, venue, instrument, side, and source |
| broker_fee_profiles | User/broker/account-specific commissions, minimums, and overrides |
| tax_lots | Independent immutable Buy lots and remaining quantities |
| lot_allocations | Explicit Sell-to-Buy lot consumption, including partial closes |
| trade_cost_breakdowns | Component-level actual/estimated costs with calculation provenance |
| tax_profiles | Private user inputs required by an estimator |
| tax_estimates | Versioned results, assumptions, confidence/status, and supersession |
| fx_rate_observations | Rate, currency pair, source, timestamp, and purpose |

All user-specific records must be private, owner-scoped, and protected by row-level access controls. Tax-profile details are sensitive financial data and must not be exposed through public APIs, logs, analytics events, or client-managed authorization metadata.

### 4.8 Daily Picks outcome boundary

Future outcome records may add these separate measures:

- gross_return;
- transaction_cost_return or transaction_cost_amount;
- fx_cost_return or fx_cost_amount;
- net_return;
- benchmark_net_return; and
- cost_rule_version.

Exact field names require a separately approved schema review. Existing historical outcomes remain immutable. A recalculation under a newer rule version is a new scenario, linked to the original, never a silent replacement.

Model-quality reports must:

- use after-cost, pre-personal-tax returns as the realistic economic measure;
- show gross results alongside after-cost results so cost sensitivity is visible;
- apply comparable cost assumptions to the benchmark/rest-of-population comparison;
- exclude personalized tax estimates from ranking, win-rate, calibration, and edge claims; and
- keep India and U.S. populations separate unless a report explicitly justifies aggregation.

### 4.9 Dashboard behavior

The primary control is a three-state selector:

1. Gross;
2. After Costs; and
3. Estimated After Tax.

Every view must show:

- realized and unrealized amounts separately;
- local-market currency and optional reporting currency;
- the FX rate and timestamp when conversion is used;
- an expandable cost/tax component breakdown;
- actual, reconciled, estimated, unavailable, and not-applicable badges;
- the rule/profile version and as-of date; and
- a warning when missing information prevents an exact value.

The dashboard must never label an estimate as exact. Exactness is available only to the extent that actual fills, actual broker charges, FX records, lot elections, and required tax-profile data are present.

## 5. Explainability and Transparency Impact

This feature resolves a potentially misleading pairing: a profitable stock move can still produce a smaller or negative investor result after costs and tax.

Required user-facing explanations:

- **Gross** — price movement before trading costs and tax.
- **After Costs** — includes recorded or estimated trading and FX costs; component statuses are visible.
- **Estimated After Tax** — personal estimate based on the selected tax profile; not tax advice and not used to score StockSense360's predictions.
- **Why changed?** — each revision identifies whether an estimate was reconciled to an actual charge, an FX rate changed, a lot election changed, or a new rule version was selected.

## 6. Data and Edge Cases

The future design and test plan must cover:

- missing broker profile, missing charge, or unsupported instrument;
- charge known only at settlement;
- fee minimums/caps, rounding, and multiple fills;
- cancelled, rejected, corrected, split, merged, or partially filled orders;
- stock splits, bonuses, dividends, return of capital, spin-offs, and symbol/currency changes;
- partial closes across multiple lots;
- same-symbol repeated buys and same-day buys;
- sell quantity exceeding known lots;
- short sales, options, and other unsupported products returning Unavailable rather than a fabricated result;
- tax-year boundary and changing residency/account profile;
- FX at trade, settlement, and reporting times;
- stale or missing FX;
- estimated cost later replaced by actual broker data;
- rule changes with overlapping or missing effective dates; and
- historical views under original rules versus explicitly requested what-if scenarios.

## 7. Testing Plan

A future implementation must include:

### Unit tests

- every fee component and effective-date boundary by market;
- rate basis, caps/minimums, rounding, and currency precision;
- immutable repeated-buy lots and partial allocations;
- tax holding-period boundaries and insufficient-profile outcomes;
- FX conversion and no-slippage-double-counting; and
- actual-versus-estimated supersession.

### Integration tests

- Paper Trade creation through dashboard P&L;
- multi-fill Buy and partial Sell;
- broker-reconciled charges replacing estimates without rewriting history;
- owner isolation and unauthorized-access rejection;
- independent India and U.S. calculation paths; and
- outcome export with gross and after-cost fields.

### Golden/regression cases

Maintain fixed, human-reviewed examples for:

- one India delivery round trip;
- one India multi-lot partial close;
- one U.S. round trip with a sell-side regulatory fee;
- one U.S. position with FX conversion;
- repeated same-symbol buys proving lots remain independent;
- a rule-rate change across an effective-date boundary; and
- a profile-incomplete tax estimate returning Unavailable.

Golden expected values must cite the rule version and authoritative source used. A legal/tax reviewer must approve tax examples before production exposure.

### Non-interference tests

- cost/tax modules are not imported by prediction ranking or BUY-selection code;
- personalized tax data cannot enter model features;
- existing predictions and historical outcomes are byte-for-byte unchanged unless an explicitly versioned after-cost scenario is requested; and
- the active Daily Picks evidence-reconciliation workflow is unaffected.

## 8. Rollout and Risk

### Phased implementation

| Phase | Scope | Exit gate |
|---|---|---|
| TC-0 | Evidence audit, broker/product inventory, legal/tax review, exact schema/API proposal | Stakeholder approval; no code before evidence closes |
| TC-1 | Versioned India/U.S. transaction-cost engine for Paper Trades | Golden calculations and independent review pass |
| TC-2 | Gross/After-Cost dashboard and component breakdown | Browser, API, RLS, and reconciliation tests pass |
| TC-3 | Immutable tax lots and sell allocations | Repeated-buy and partial-close invariants pass |
| TC-4 | Opt-in, versioned tax estimator | Legal/tax review; insufficient profiles fail closed |
| TC-5 | Estimated After-Tax dashboard | Copy, privacy, accessibility, and audit trail approved |
| TC-6 | Optional broker-import reconciliation | Provider-specific evidence and rollback plan approved |

Every phase requires its own SES-006-compliant execution prompt with requirement IDs, evidence checkpoint, exact files, test commands, stop conditions, rollback, and a draft PR. Approval of this document is not approval of any phase.

### Risks and mitigations

| Risk | Required mitigation |
|---|---|
| Regulatory or tax rates become stale | Effective-dated immutable versions, official-source provenance, scheduled review owned by a named role, and fail-closed behavior for uncovered dates |
| False precision | Status badges, assumptions, profile/rule version, and Unavailable instead of zero |
| Privacy exposure | Owner-scoped private tables, RLS tests, redacted logs, minimal tax-profile collection |
| Double counting | Component taxonomy, fill-aware slippage rule, and reconciliation variance tests |
| Historical drift | Preserve original results and create linked versioned scenarios |
| Cross-market contamination | Separate India/U.S. rules, fixtures, reports, and release gates |
| Model contamination | Hard architectural and test firewall between tax profiles and Selection Engine |
| Large migration blast radius | Additive phases, feature flags default off, shadow calculations, no destructive rewrite |

### Rollback

Each phase must be additive and reversible:

- UI flags default off;
- legacy gross P&L remains available;
- new reads fall back to legacy display with an explicit unavailable notice;
- no phase deletes or rewrites existing trades;
- tax and cost records are append-only/versioned; and
- disabling the feature must not alter ranking, picks, or stored historical predictions.

## 9. Open Questions

Product/stakeholder decisions required before TC-0 closes:

1. Which brokers/account types are launch targets in India and the U.S.?
2. Should broker-import reconciliation be in the first release or remain TC-6?
3. Which tax residencies and account types are supported at launch?
4. Which reporting currency and FX source/convention should each user be able to select?
5. How should users choose lot allocation when the broker election is unavailable?
6. Is the first release limited to cash equities and delivery trades?
7. Who owns annual regulatory/tax rule review and legal sign-off?
8. Should benchmark-net-return use a standardized execution-cost profile or the same user broker profile?
9. What historical date range, if any, receives an explicitly versioned after-cost scenario calculation?

## 10. Roadmap Metadata

| Field | Decision |
|---|---|
| Priority | High, after the current Daily Picks evidence and outcome-foundation work is closed |
| Business value | High — investor-visible P&L becomes realistic and auditable |
| Engineering effort | Large, delivered incrementally across TC-0 through TC-6 |
| Risk | High for tax correctness/privacy; medium for transaction-cost calculation |
| Dependencies | Clean trade/fill history, immutable lots, FX provenance, current Daily Picks work closed, tax/legal review, broker/product decisions |
| Likely files | Future Paper Trading/Portfolio services and routes, private database migrations, dashboard components, tests, and this SSDS; exact paths require TC-0 repository evidence |
| Estimated time | Multi-sprint; TC-0 approximately 1–2 weeks, later phases estimated only after evidence |
| Sprint | Unscheduled future workstream |

## 11. SES-006 Coverage Declaration for Future Prompts

A later execution prompt is valid only if it:

- identifies one TC phase, not the entire workstream;
- maps every requirement to evidence, implementation files, and tests;
- states India and U.S. behavior independently;
- preserves immutable repeated-buy lots;
- separates actual, estimated, and unavailable values;
- separates after-cost model evaluation from personalized tax estimates;
- names migrations, RLS, feature flag, observability, rollout, and rollback when applicable;
- stops when authoritative rates, product decisions, or user-profile requirements are unresolved; and
- ends at a draft PR unless explicit merge/deployment approval is separately provided.

Until such a prompt is approved, SSDS-010 remains documentation only.
