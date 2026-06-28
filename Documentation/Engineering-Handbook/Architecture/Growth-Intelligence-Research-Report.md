# Growth Intelligence — Research Report (companion to SSDS-007)

**Purpose of this document:** SSDS-007's Metric Catalogue states *what* each metric is and where its data would come from. This report states *why* each one matters from a financial-research standpoint — the reasoning a future engineer (or this engagement's own future sessions) would otherwise have to re-derive. Per this sprint's "evidence over opinion" rule: every claim below is either grounded in standard, widely-taught equity-analysis reasoning (cited where a specific named concept is being invoked) or explicitly flagged as this study's own reasoned inference, not asserted as settled fact.

---

## 1. Revenue growth quality

**Why it matters:** Revenue growth is the most basic growth signal, but its *quality* varies enormously — a company growing revenue 20%/year through genuine demand is different from one growing 20%/year through aggressive discounting that will eventually compress margins, or through one-time contract wins that won't repeat. "Quality" here specifically means: is the growth rate consistent across the available history, or lumpy/volatile? Volatility in the growth rate itself is informative — it's the same logic behind why earnings-quality analysis (e.g., the accruals-based reasoning Business Quality Engine already applies via its Beneish M-Score and Sloan Accruals inputs, confirmed in this engagement's own SSDS-003 work) treats *smooth* growth with some suspicion (it can indicate earnings management) while *volatile* growth gets a durability discount, not a quality bonus, in the opposite direction. Growth Intelligence's contribution is narrower than either extreme: it measures the rate and consistency of the top-line trend itself, leaving earnings-quality-style manipulation detection to Business Quality, where it already lives.

**Confidence implications:** A 3-year CAGR computed from exactly 4 data points is inherently less reliable than a 5-year CAGR from 6 points — more periods reduce the influence of any single anomalous year. This is why the catalogue proposes both 3Y and 5Y windows where data allows, rather than picking one.

**Missing data:** A company with under 4 years of history (recent IPO, recent spinoff) simply cannot produce a CAGR — this should reduce data-completeness confidence proportionally, not be backfilled with an assumed value.

## 2. EPS growth

**Why it matters:** EPS growth and revenue growth diverging in either direction is itself a signal, not noise. EPS growing faster than revenue can mean genuinely improving margins (good) or can mean share buybacks shrinking the denominator while the underlying business is flat (less good, though not necessarily bad — buybacks are a legitimate capital-allocation choice). EPS growing slower than revenue, or declining while revenue grows, is a classic margin-compression or dilution signal. This divergence check (Metric #7 in the catalogue) is arguably more informative than either growth rate in isolation — it's the reasoning behind why analysts conventionally look at both metrics side by side rather than EPS growth alone.

**Confidence implications:** Because India's data source provides only a categorical EPS trend (`eps_trend`: accelerating/decelerating/mixed), not a quantitative CAGR, any India-side divergence check is inherently a lower-precision comparison than the US side's numeric-vs-numeric comparison — this asymmetry should be reflected in confidence, not hidden.

## 3. Operating profit growth

**Why it matters:** Operating profit (EBIT) growth separate from revenue growth specifically isolates *operating leverage* — whether a company's cost structure allows profit to grow faster than revenue as it scales (a structural advantage software/platform businesses are conventionally understood to have over labor- or input-intensive businesses), or whether costs are growing in lockstep with or faster than revenue (eroding the operating margin even as the top line grows). This is a distinct question from EPS growth (#2), which is affected by financing decisions (debt, buybacks) below the operating-profit line; EBIT growth isolates the *business's* leverage, not the *capital structure's*.

## 4. Free cash flow growth

**Why it matters:** Reported earnings and operating profit can grow while a business still consumes cash — most commonly through working-capital growth (more inventory, more receivables, both of which scale with revenue but aren't "growth" in the value-creating sense) or through capitalized costs that don't show up as an expense yet. FCF growth is the standard cross-check equity analysts use precisely because it adjusts for both. A company whose revenue and EBIT both grow nicely while FCF stagnates or declines is showing a real, specific warning sign worth surfacing on its own.

## 5. Share count dilution

**Why it matters:** Per-share metrics (the only ones that actually matter to an existing shareholder, as opposed to absolute-dollar metrics) can be inflated by reducing the share count (buybacks, value-accretive to existing holders) or deflated by increasing it (secondary offerings, convertible-debt conversion, employee stock-comp issuance at scale — value-dilutive). A company that looks like it's growing EPS nicely while quietly diluting shareholders 5%+/year is a meaningfully different investment than one growing EPS through genuine per-share value creation. This is a standard, well-understood equity-analysis concern, not a novel idea this study is introducing — its value here is in making it an explicit, quantified, always-checked metric rather than something a user has to notice themselves.

## 6. Organic vs. acquisition-driven growth

**Why it matters, and why this study cannot resolve it with current data:** Growth achieved by acquiring other companies is fundamentally different from growth achieved by the existing business expanding — acquired growth carries integration risk, often overpays (acquisition premiums), and can mask a declining core business behind a growing consolidated top line. This is a real, important distinction in equity research. However, distinguishing the two reliably requires either (a) segment-level reporting detail most companies don't expose in a structured, scrapeable way, or (b) parsing cash-flow-statement acquisition spend against reported organic-growth disclosures companies sometimes (not reliably) provide in earnings calls/investor materials — neither of which any provider in this codebase's Data Fabric currently exposes in structured form. This study names the gap rather than approximating it with a weak proxy that could mislead more than it helps.

## 7. Margin expansion

**Why it matters:** Margin trend (gross and operating) is the clearest lens on whether growth is coming "for free" (pricing power, scale economies) or being bought (discounting, rising input costs absorbed without pass-through). This overlaps conceptually with Operating Profit Growth (#3) but is worth tracking as its own normalized metric (a percentage, not a CAGR) because margin level and margin *trend* are both informative independent of the absolute growth rate — a company can have low but expanding margins (an improving story) or high but contracting margins (a deteriorating one), and growth-rate-alone metrics wouldn't distinguish these.

## 8. Guidance consistency

**Why it matters, and why this is the one full data-source gap:** A management team that consistently meets or modestly beats its own guidance is, by reputation if nothing else, more credible than one that consistently misses or guides aggressively and walks it back — this is standard sell-side analyst practice (tracking "beat rate" against consensus or company guidance). This study could not find any provider already integrated in this codebase that exposes historical guidance-vs-actual data in structured form; yfinance's earnings-calendar/estimate fields exist as a concept but are not currently fetched, parsed, or validated anywhere in this codebase, and this study did not attempt to validate their reliability or coverage, since doing so would itself be implementation work, not design-study research. Recommend a small, dedicated feasibility spike rather than assuming this metric is achievable.

## 9. Capital allocation in service of growth (reinvestment efficiency)

**Why it matters:** A company reinvesting heavily (high CapEx, high R&D) that *isn't* growing operating profit proportionally is destroying value through that reinvestment, not creating it — this is the standard logic behind incremental-ROIC analysis (how much additional operating profit is generated per dollar of additional invested capital). It's a genuinely useful, well-grounded concept, but this study flags it as the catalogue's most "new" derived metric — incremental ROIC is more commonly computed by analysts with access to clean, audited invested-capital figures and is sensitive to how invested capital is defined (this study proposes total debt + equity, matching what's already available from Financial Strength's existing 16-field schema, but other reasonable definitions exist and could produce different conclusions). This should be validated against real companies' known reinvestment stories (e.g., a company publicly known to be in a heavy, currently-unprofitable growth-investment phase) before being trusted, not assumed correct from the formula alone.

## 10. Growth durability and cyclicality adjustment

**Why it matters:** The same headline growth number means different things in a structurally growing sector (software, healthcare services) versus a cyclical one (commodities, autos, industrials) at a favorable point in its cycle — cyclical growth is expected to (and conventionally understood to) mean-revert, while structural growth is not. Treating both identically would systematically overrate cyclical companies caught at a cycle peak and potentially underrate structurally growing companies in a temporary trough. This is why the catalogue proposes a sector-context modifier rather than a sector-agnostic growth score — directly reusing this codebase's existing sector taxonomy (`sector_quality_applicability.py`) rather than inventing a new cyclicality model, since that taxonomy's sector buckets already roughly track the cyclical/structural distinction analysts conventionally use.

## 11. Forecast confidence and historical persistence

**Why it matters:** Two companies with an identical 3-year revenue CAGR can have very different reliability if one grew steadily ~X% every year and the other grew 0%, then 3X%, then roughly X% (same geometric average, very different trend reliability). Standard statistical reasoning (coefficient of variation of year-over-year growth rates) captures this directly and is a well-established way to quantify trend consistency without requiring any new data beyond what's already needed for the CAGR calculations themselves. This is the mechanism proposed to drive the engine's trend-reliability confidence component (distinct from data-completeness confidence) — see SSDS-007's Confidence Strategy section for how the two combine.

---

*This report is research only — financial-reasoning grounding for SSDS-007's Metric Catalogue. No production code, tests, or providers were modified or evaluated empirically in producing it; "validated against real data" claims throughout SSDS-007 and this report refer to validation work this study recommends for the implementation sprint, not work already performed.*
