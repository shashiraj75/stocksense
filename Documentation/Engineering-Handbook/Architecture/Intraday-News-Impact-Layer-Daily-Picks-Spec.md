# Intraday News Impact Layer for Daily Picks

**Status:** Planned / documentation-only specification  
**Scope:** Future enhancement for Daily Picks live validity monitoring  
**Implementation state:** Not implemented in production code  
**Decision principle:** Preserve the original Daily Pick; add a live status overlay when market-moving news appears.

---

## 1. Purpose

Daily Picks are generated before market open using the data available at generation time. During trading hours, material news can invalidate, weaken, or strengthen a pick after it has already been published.

This specification documents a future **Intraday News Impact Layer** that monitors material news and market reaction during trading hours, then updates the **live validity status** of an existing Daily Pick without silently rewriting the original recommendation.

The goal is not to make Daily Picks behave like uncontrolled real-time trading tips. The goal is to protect user trust by making the platform transparent about what changed after a pick was generated.

---

## 2. Non-Goals

This document does **not** approve production implementation.

This enhancement must not:

- Change existing Daily Picks generation logic.
- Change scoring thresholds or Recommendation Engine logic.
- Change scheduler behavior.
- Silently convert `BUY` to `SELL` based only on a headline.
- Rewrite or delete the original Daily Pick after publication.
- Claim intraday monitoring is live before a separate implementation, validation, and release decision.
- Treat generic media sentiment as equal to official exchange disclosures.

---

## 3. Product Rule

The original Daily Pick remains the audit source of truth.

The intraday layer may add a separate live status:

| Status | Meaning |
|---|---|
| `ACTIVE` | No material post-generation news or abnormal reaction detected. |
| `WATCH` | News or price/volume action may affect the pick, but evidence is not strong enough to pause fresh entry. |
| `PAUSED` | Material event detected; fresh entry should be avoided until revalidation. |
| `INVALIDATED` | Severe negative news or confirmed thesis break; the original pick remains visible but is no longer live-valid. |
| `UPGRADED` | Positive material news strengthens the pick, subject to price/volume confirmation and overextension checks. |

Example display principle:

```text
Original Signal: BUY
Live Status: PAUSED
Reason: Material negative announcement detected after pick generation.
Last checked: 11:20 IST
```

---

## 4. Source Priority

The intraday layer should prioritize official and high-signal sources before broad media feeds.

| Priority | Source Type | Treatment |
|---|---|---|
| 1 | NSE / BSE corporate announcements, filings, board-meeting outcomes, exchange notices | Highest confidence; can trigger `PAUSED` or `INVALIDATED` depending on severity. |
| 2 | Credit rating agency updates, regulator notices, insolvency / legal disclosures | High confidence; usually risk-sensitive. |
| 3 | Recognized financial news sources | Useful but should normally require confirmation before severe status changes. |
| 4 | Generic RSS headlines, blogs, social media-derived mentions | Low confidence; should not trigger severe overrides alone. |
| 5 | Price/volume anomaly with no confirmed news | Can trigger `WATCH`, not automatic invalidation. |

Official exchange disclosures should have higher weight than generic media sentiment.

---

## 5. News Categories

The layer should classify events by category and impact, not only positive/negative sentiment.

Suggested categories:

- `earnings_result`
- `guidance_change`
- `order_win`
- `contract_loss`
- `management_resignation`
- `promoter_pledge_change`
- `insider_or_bulk_deal`
- `regulatory_or_legal_issue`
- `credit_rating_change`
- `corporate_action`
- `sector_news`
- `unusual_price_volume_movement`
- `provider_or_data_warning`

Each event should store a short user-facing reason and a machine-readable category.

---

## 6. Impact Scoring

A simple first version can use a bounded impact score:

| Impact Score | Meaning | Typical Status Effect |
|---|---|---|
| `+3` | Strong positive material event | Consider `UPGRADED`, only with confirmation. |
| `+2` | Positive event | Keep `ACTIVE` or move to `UPGRADED` if confirmed. |
| `+1` | Mild positive | Usually stay `ACTIVE`. |
| `0` | Neutral / unclear | No change. |
| `-1` | Mild negative | `WATCH`. |
| `-2` | Negative material event | `WATCH` or `PAUSED`. |
| `-3` | Severe negative / thesis break | `PAUSED` or `INVALIDATED`. |

A headline should not be enough. Final status should combine:

```text
final_intraday_status = f(news_impact, source_priority, confidence, price_move, volume_ratio, existing_pick_horizon)
```

---

## 7. Conservative Decision Rules

The first production version should be intentionally conservative.

Recommended rules:

1. Severe negative official announcement -> `PAUSED` or `INVALIDATED`.
2. Moderate negative official announcement -> `WATCH` or `PAUSED`.
3. Positive announcement with no price/volume confirmation -> keep `ACTIVE`, add informational badge only.
4. Positive announcement plus strong confirmation and no overextension -> consider `UPGRADED`.
5. Positive announcement after a large intraday gap-up -> `WATCH`, avoid chasing.
6. Unconfirmed media headline -> do not move beyond `WATCH` unless supported by official source or abnormal market reaction.
7. Price crash with no confirmed news -> `WATCH` with reason `Unusual price/volume movement; no confirmed material news found yet`.
8. Missing provider data -> do not infer impact; show a data-warning badge only.

---

## 8. Suggested Data Contract

A future implementation may add an append-only event table such as:

```text
pick_news_events
- id
- pick_id
- symbol
- market
- horizon
- source
- source_priority
- headline
- url
- published_at
- detected_at
- category
- impact_score
- confidence
- price_move_pct
- volume_ratio
- status_before
- status_after
- reason
- created_at
```

A separate live-status projection may be derived from the latest material event per pick:

```text
pick_live_status
- pick_id
- symbol
- market
- horizon
- original_signal
- live_status
- status_reason
- last_checked_at
- latest_event_id
```

Append-only event history is preferred so the product can explain *why* a pick changed from `ACTIVE` to `WATCH`, `PAUSED`, or `INVALIDATED`.

---

## 9. UI Requirements

The Daily Picks page should eventually show:

- Original Signal
- Live Status
- News Impact Badge
- Short reason
- Last checked time
- Link to source where available

Suggested badge language:

| Badge | User-Facing Copy |
|---|---|
| `ACTIVE` | No material post-generation news detected. |
| `WATCH` | New information may affect this pick. Review before fresh entry. |
| `PAUSED` | Material event detected. Fresh entry is not recommended until revalidated. |
| `INVALIDATED` | This pick is no longer live-valid due to material post-generation information. |
| `UPGRADED` | Positive material information supports the original pick, subject to price discipline. |

Required disclosure text:

```text
Daily Picks are generated before market open using information available at generation time. During trading hours, StockSense360 may monitor material news and market reaction. If new information affects a pick, the original signal remains visible for audit, while the live status may change.
```

---

## 10. Suggested Implementation Phases

### Phase 1 - Documentation and UI Placeholder

- Add this specification.
- Add no production behavior.
- Optional future UI placeholder may show `Live News Status: Not monitored yet` only if explicitly approved.

### Phase 2 - Passive Event Capture

- Capture official exchange/news events related to published Daily Picks.
- Store events without changing pick status.
- Validate source quality, latency, duplicate handling, and false positives.

### Phase 3 - Conservative Status Overlay

- Enable `ACTIVE`, `WATCH`, and `PAUSED` only.
- Preserve original Daily Pick signal.
- Add audit event history.
- Do not enable `INVALIDATED` or `UPGRADED` until enough live evidence exists.

### Phase 4 - Full Status Set

- Add `INVALIDATED` and `UPGRADED` after validation.
- Require official source and price/volume confirmation for severe transitions.
- Add monitoring and regression tests.

### Phase 5 - Intraday Revalidation

- Re-run scoring intraday only after the status overlay is proven stable.
- Show original score and live-adjusted score separately.
- Do not silently replace the 9 AM recommendation.

---

## 11. Testing Requirements

Future implementation should include tests for:

- Official negative announcement moves `ACTIVE` -> `PAUSED`.
- Generic unconfirmed headline cannot move `ACTIVE` -> `INVALIDATED`.
- Positive news without price/volume confirmation cannot trigger `UPGRADED`.
- Large gap-up after positive news results in `WATCH`, not automatic upgrade.
- Event history is append-only.
- Original Daily Pick signal remains unchanged.
- Missing provider data fails soft and does not produce false status changes.
- UI renders original signal and live status distinctly.

---

## 12. Compliance and Trust Notes

This feature should reduce risk, not create a new advisory black box.

Trust rules:

- Always preserve the original pick.
- Always explain the live status change.
- Always show source and timestamp when available.
- Never overstate certainty.
- Never present intraday status as a guaranteed trading instruction.
- Prefer `WATCH` / `PAUSED` when evidence is incomplete.

---

## 13. Open Questions Before Implementation

Before any production work, confirm:

1. Which official announcement sources are reliable enough for automated polling.
2. Whether NSE/BSE source access is stable within free-cost constraints.
3. Whether source URLs can be stored and displayed without licensing issues.
4. Expected polling cadence during trading hours.
5. Whether the feature is market-specific at first, likely India Daily Picks only.
6. Whether the first implementation should be shadow-only.
7. What user-facing disclaimer language is required.
8. Whether intraday alerts/notifications are in scope or deferred.

---

## 14. Current Decision

This is a **future/planned enhancement**. The approved current action is documentation only.

No production code, scoring logic, Daily Picks generation behavior, scheduler behavior, or provider integration is changed by this specification.
