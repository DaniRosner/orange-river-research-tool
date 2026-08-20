"""
the user's own research-brief templates (originally five separate .docx files
he uses to kick off a report with ChatGPT/Claude directly, no bridge
involved) — reproduced here so the bridge can hand the AI the *exact same
brief* as a tool's return value the moment the user asks for one of these
report types, instead of the AI having to reconstruct or half-remember what
he wants.

Why a tool's return value and not another docstring: a docstring is
metadata the client may or may not surface at the moment that matters (see
the standing-content-requirements saga in app/routers/bridge.py) — but a
tool's return value is real data the model just asked for and is
guaranteed to see, right before it starts drafting. That's what actually
solves the "the drafting happens before any tool call" timing problem.

Each template originally ended with a Word-doc-specific "OUTPUT FORMAT"
block (title page, TOC, 14pt/12pt heading sizes, page margins, etc.) — that
described the user's older direct-to-Word workflow. Deliberately dropped here:
this bridge's output format is whatever save_final's PDF renderer produces
(navy headings, styled tables, a recommendation box, real per-page
footnotes), which has its own rules — see save_final's docstring. Baking a
second, conflicting formatting spec in here would just create ambiguity
about which one wins. Every template's placeholder ticker ("MITK" in
the user's originals) is templated as {ticker}.
"""

_CLOSING_NOTE = """

---
A NOTE ON FORMAT AND PROCESS (not part of the user's original brief, added by
the bridge): draft this as plain text/markdown in the chat — don't generate
a Word doc or PDF yourself. Show the user the draft and let him react; revise
based on his feedback for as long as that takes. Only once he's explicitly
approved a specific version as final, call save_final — it renders your
markdown into this system's own formatted PDF automatically (real
headings, styled tables, a recommendation box, and true per-page
footnotes), which is the only formatting that actually matters here.

Whatever you draft should also satisfy the user's standing content
requirements (see save_final's docstring for the full text) — most
relevantly: cite specific factual claims inline with bracketed reference
numbers (e.g. "revenue grew 93% YoY [3]") backed by a numbered "Sources:"
list, since save_final's PDF renderer turns those into real per-page
footnotes automatically, but only if they're actually there."""

_INDUSTRY_DEEP_DIVE = """You are a senior industry analyst at a top-tier investment research firm. I need a comprehensive industry deep dive for the industry/sector that {ticker} operates in.

Structure it as follows:

1. INDUSTRY OVERVIEW (~500 words)
   - Define the industry and its boundaries
   - Current market size (TAM/SAM/SOM where applicable)
   - Historical growth rate and projected CAGR over next 5 years
   - Industry lifecycle stage (emerging, growth, mature, declining)
   - Key value chain participants and how value is distributed

2. MARKET DYNAMICS & DEMAND DRIVERS (~500 words)
   - Primary demand drivers and their current trajectory
   - Secular tailwinds and headwinds
   - Cyclicality assessment — how sensitive is this industry to macro conditions?
   - Customer concentration and switching cost dynamics
   - Pricing power analysis across the value chain

3. COMPETITIVE STRUCTURE (~500 words)
   - Industry concentration (fragmented vs. oligopoly vs. monopoly)
   - Porter's Five Forces assessment with specific evidence
   - Barriers to entry and exit
   - Key success factors — what separates winners from losers?
   - Disruption risk — what technologies or business models threaten incumbents?

4. REGULATORY & MACRO ENVIRONMENT (~500 words)
   - Key regulations and recent/pending regulatory changes
   - Government policy impact (subsidies, tariffs, tax treatment)
   - Geopolitical considerations
   - ESG and sustainability factors specific to this industry
   - Labor market dynamics and talent competition

5. OUTLOOK & INVESTMENT IMPLICATIONS (~500 words)
   - Where is this industry headed in 3-5 years?
   - What are the highest-conviction structural trends?
   - Where specifically does {ticker} sit within this landscape?
   - What would make this industry more/less attractive for investment?
   - Key metrics and leading indicators to monitor going forward

Be specific with data points, cite industry sources where possible, and avoid generic statements. This should read like a professional equity research industry primer.

Stock: {ticker}""" + _CLOSING_NOTE

_INVESTMENT_RESEARCH_REPORT = """Investment Research Report: {ticker}

You are a senior analyst at a concentrated, long-only value and quality-growth investment fund. Your job is to produce a rigorous, opinionated investment research report on {ticker} for a portfolio manager who:

- Holds positions for 3–10+ years and thinks like a business owner, not a trader
- Seeks high-quality compounders with durable competitive advantages and high returns on invested capital
- Will pay a fair price for quality, but demands a margin of safety
- Is willing to be contrarian and patient when a good business is temporarily out of favor
- Cares deeply about management character, capital allocation discipline, and long-term incentive alignment

Write with conviction. Do not hedge every sentence. Flag genuine uncertainties clearly, but be direct about your assessment where the evidence supports it.

REPORT STRUCTURE

EXECUTIVE BRIEF

One-paragraph business description (what it does, how it earns money, scale)

Investment Snapshot — fill in each line concisely:
- Moat Rating: [Wide / Narrow / None] — one sentence explanation
- Business Quality: [1–10] — one sentence justification
- Management Quality: [1–10] — one sentence justification
- Key Opportunity: What makes this interesting right now
- Key Risk: The single most important thing that could be wrong
- Valuation Stance: [Cheap / Fair / Expensive] relative to intrinsic value

FULL ANALYSIS

1. Business Overview
- Core business model and revenue streams
- Key business segments with estimated revenue mix
- Geographic exposure and end markets served
- Scale, market position, and relevant context on industry size

2. Industry Dynamics & Competitive Landscape
- Industry structure: fragmented vs. consolidated, cyclical vs. secular, regulated vs. open
- Tailwinds and headwinds shaping the industry over the next 5–10 years
- Key competitors and how this company is positioned against them
- Barriers to entry and what makes winning in this industry difficult
- Any disruption risks (technology, regulation, new entrants)

3. Competitive Moat & Business Quality
- Primary sources of competitive advantage (be specific — pricing power, switching costs, network effects, cost advantages, intangible assets, etc.)
- Moat durability: is the advantage widening, stable, or narrowing?
- Evidence of moat (pricing history, customer retention, ROIC vs. cost of capital over time)
- Where the business is genuinely differentiated vs. where it competes on price or features

4. Financial Health & Free Cash Flow
- Revenue growth trajectory and what is driving it (volume, price, mix, M&A)
- Gross margin, operating margin, and net margin — levels and trends
- Free cash flow generation: FCF margin and conversion from net income
- Balance sheet: leverage, liquidity, any concerning obligations
- Return on invested capital (ROIC) history — is it above cost of capital and by how much?
- Capital intensity: how much reinvestment is required to sustain growth?

5. Recent Developments (Last 12 Months)
- Summarize the most meaningful news, events, and developments from the past 12 months
- Earnings surprises or guidance changes and how the market reacted
- Strategic announcements: new products, partnerships, contract wins, divestitures
- Management changes, activist involvement, or significant insider activity
- Macro or industry-level developments that materially affect this company's outlook
- Any controversies, investigations, or reputational events worth flagging
- Overall: has the narrative around this business improved, deteriorated, or stayed the same?

6. Key Acquisition History
For each significant acquisition (focus on material deals — skip immaterial tuck-ins unless part of a clear pattern):
- Target: Name and business description
- Date & Price Paid: Announced/closed date, total consideration, and implied multiple if available (EV/EBITDA, EV/Sales, etc.)
- Strategic Rationale: Why did management pursue it?
- Integration Outcome: Has it worked? What do the financials show post-close?
- Verdict: Value-creating, value-neutral, or value-destroying — and why

If the company has made no significant acquisitions, note that explicitly and comment on whether organic-only growth is a deliberate philosophy or a constraint.

7. Management, Ownership & Capital Allocation
- Key executives: tenure, background, and relevant track record
- Founder-led or professional management — does it matter here?
- Insider ownership levels and recent insider buying/selling activity
- Capital allocation track record: dividends, buybacks, M&A — value-creating or destructive?
- Compensation structure and whether management incentives align with long-term shareholders
- Any red flags: aggressive accounting, excessive dilution, poor M&A history

8. Key Risks & Bear Case
List the 4–6 most important risks, ordered from most to least severe. For each:
- Risk: Name it clearly
- Why it matters: Specific mechanism by which it could impair the business or valuation
- Mitigant: What, if anything, reduces or offsets this risk

9. Valuation & Downside Protection
- Current trading multiples (P/E, EV/EBITDA, P/FCF, EV/Sales as relevant)
- Historical valuation range and where we sit today
- Peer/comparable company valuation benchmarks
- Rough intrinsic value estimate or DCF assumptions implied by current price
- What the market appears to be pricing in — and whether that is reasonable
- Downside scenario: what does the stock look like in a bear case?
- Margin of safety assessment

10. Investment Debates
Summarize the live debate around this stock — what bulls and bears each believe, and where the crux of disagreement lies.

The Bull Case
- Lay out the 3–5 strongest arguments for owning this stock at the current price
- Ground each point in specific evidence (financials, competitive dynamics, management actions, valuation)
- What does the optimistic but reasonable scenario look like in 3–5 years?

The Bear Case
- Lay out the 3–5 strongest arguments against owning this stock
- Be intellectually honest — steelman the bear case even if the overall assessment is positive
- What does the pessimistic but plausible scenario look like in 3–5 years?

The Crux
- What is the single most important factual or analytical question that, if resolved one way, proves the bulls right — and if resolved the other way, proves the bears right?
- Where does the weight of evidence currently sit?

11. Key Questions for Further Diligence
List 5–8 specific, pointed questions that remain unanswered and would most change your conviction level. Focus on questions that are actually researchable (channel checks, expert calls, deeper financial modeling, etc.)

Stock: {ticker}""" + _CLOSING_NOTE

_COMPETITIVE_LANDSCAPE_ANALYSIS = """You are a senior competitive intelligence analyst. I need a detailed competitive landscape analysis for {ticker} and its direct competitors.

Structure the analysis as follows:

PART 1: COMPANY POSITIONING
- Where does {ticker} sit in the competitive landscape? Define its strategic positioning.
- What is {ticker}'s stated strategy and how is it differentiated?
- Market share estimates (if available) and recent trajectory

PART 2: COMPETITOR PROFILES
For EACH of the 5-8 most relevant competitors, provide:
- Company name, ticker (if public), and brief description
- Revenue scale and growth rate relative to {ticker}
- Key competitive advantages (moat sources: brand, cost, network effects, switching costs, IP, scale)
- Key competitive disadvantages or vulnerabilities
- Strategic focus — where are they investing and why?
- Recent strategic moves (M&A, partnerships, new products, geographic expansion)
- Head-to-head comparison with {ticker} on the 3-4 metrics that matter most in this industry
- Threat level assessment: Low / Medium / High — and why

PART 3: COMPETITIVE DYNAMICS
- Who is gaining share and who is losing? What's driving the shifts?
- Are there emerging competitors or new entrants that could disrupt the landscape?
- What is the competitive response to {ticker}'s strategy?
- Where do competitors have structural advantages {ticker} cannot easily replicate?
- Where does {ticker} have durable advantages competitors cannot easily replicate?

PART 4: OPPORTUNITY & RISK MATRIX
- Map the broader opportunity set: What adjacent markets could {ticker} or competitors expand into?
- Identify the top 5 competitive risks {ticker} faces
- Identify the top 5 competitive opportunities for {ticker}
- What would a "nightmare scenario" look like competitively?
- What would a "best case scenario" look like competitively?

PART 5: MOAT ASSESSMENT
- Rate {ticker}'s overall competitive moat: None / Narrow / Wide
- Provide specific evidence for each moat source
- Is the moat widening, stable, or narrowing? Why?
- Compare moat strength to the top 2-3 competitors

Be specific with names, numbers, and evidence. Avoid vague generalizations. I want an analysis I can use to make an investment decision.

Stock: {ticker}""" + _CLOSING_NOTE

_LAST_4_QUARTERS_SUMMARY = """You are a senior equity research analyst. I need a comprehensive earnings summary for {ticker} covering the last 4 reported quarterly earnings.

For EACH of the last 4 quarters, provide a dedicated section:

1. Quarter & Date: The fiscal quarter and reporting date
2. Revenue: Reported revenue vs. consensus estimate, YoY growth rate
3. EPS: Reported EPS vs. consensus estimate, YoY growth rate
4. Key Guidance: Forward guidance provided by management (revenue, EPS, any specific metrics)
5. Management Commentary Highlights: The 3-5 most important statements from the earnings call transcript — focus on strategic direction, capital allocation, competitive positioning, and any tone shifts
6. Segment Performance: Breakdown by business segment if applicable
7. Margin Trends: Gross margin, operating margin, and any notable cost dynamics discussed
8. Notable Analyst Q&A Themes: The most pressing questions analysts asked and how management responded
9. Surprises or Red Flags: Anything unexpected — beats, misses, one-time items, accounting changes, restatements

After covering all 4 quarters, provide:
- Trend Analysis: How has the earnings trajectory evolved? Are estimates trending up or down?
- Key Metrics Dashboard: A table summarizing Revenue, EPS, Gross Margin, Operating Margin across all 4 quarters
- Forward Outlook: What is the current consensus for the next quarter and full year?

Source from: quarterly earnings call transcripts, earnings press releases, 10-Q and 10-K SEC filings, and investor presentations.

Stock: {ticker}
Please be thorough, precise, and cite specific numbers.""" + _CLOSING_NOTE

_QUARTERLY_EARNINGS_DIGEST = """You are a senior equity research analyst producing a concise Quarterly Earnings Digest for portfolio managers at a concentrated, long-only fund focused on high-quality compounders. We hold positions 3–10+ years and think like business owners.

TICKER: {ticker}

If the user has attached this quarter's earnings materials, use them. From the attachments and/or web search, automatically determine: company name, exchange, sector, which fiscal quarter/year this covers, report date, call date, stock price reaction, market cap, and Wall Street consensus estimates. Do NOT ask for any of this you can find yourself. If something cannot be found, mark "[N/A]" and move on.

PROCESS:
1. Read any attached files first. Identify company, quarter, and period from the materials.
2. Run web searches for: consensus estimates (revenue, EPS, EBITDA), stock price reaction, and any notable analyst commentary within 48 hours of the report.
3. Cross-reference materials for consistency, then build the document.

The digest answers four core questions:
(1) How did reported financials stack up to guidance and expectations?
(2) What is going on in each segment's market?
(3) What is the forward guidance and why?
(4) What other key points did the company offer?

REQUIRED SECTIONS (keep it tight):

HEADER BLOCK
Company name & ticker, quarter/FY reported, report date, stock price at prior close → post-earnings close (% chg), market cap.

EXECUTIVE SUMMARY
3–4 sentences max. Beat/miss/in-line and on what metrics. The single most important takeaway. Guidance raised/lowered/reiterated. Net thesis impact (confirming, neutral, or concerning).

FINANCIALS vs. EXPECTATIONS
One table: Metric | Reported | Consensus | Beat/Miss | vs. PY | vs. PQ
Rows: Revenue, Gross Margin, Operating Income (or Adj. EBIT), EBITDA, EPS (GAAP), Adj. EPS, FCF — include only what the company reports. 1–2 sentences below the table explaining the why behind the headline beat or miss.

SEGMENT DETAIL
For each segment, a short paragraph (3–5 sentences) covering: revenue and margin performance, YoY trend, and what is driving results in that segment's end market (volume, price, mix, demand environment, competitive dynamics). Use a compact summary table above the paragraphs if there are 3+ segments: Segment | Revenue | Op. Income | Margin | YoY Chg

FORWARD GUIDANCE
Table comparing: Metric | Prior Guide | Current Guide | Consensus | Raised/Lowered/Reiterated
Then 2–3 bullet points on the key assumptions behind guidance (macro, pricing, volumes, raw materials, FX) and management's tone/confidence.

MANAGEMENT COMMENTARY & CALL HIGHLIGHTS
Quick-hit bullet points only — no paragraphs. 5–8 bullets total combining the most important themes from prepared remarks AND Q&A. Each bullet should be one sentence delivering a discrete insight. Prioritize:
- New information not obvious from the financials
- Demand/pricing/competitive signals
- Strategic shifts, M&A commentary, capital allocation changes
- Areas where management was notably confident, evasive, or defensive
- Tag each bullet with the source context in brackets, e.g., [prepared remarks] or [Q&A – analyst name, firm]

THESIS & RISK CHECK
3–5 bullet points max:
- What confirmed the thesis this quarter?
- What challenged or complicated it?
- Any new risk flags?
- 1–2 specific things to watch next quarter.

STYLE RULES:
- Write for a PM with 3 minutes. Every sentence must earn its place.
- $X.XM / $X.XB format. Percentages to one decimal. EPS to two decimals.
- Cite web-sourced facts inline (e.g., "per FactSet," "per Seeking Alpha").
- If a section has minimal data, keep it to 1–2 lines — never pad.
- Total target: roughly 2-3 pages of content once rendered.

OPTIONAL ADD-ONS (only if the user asks for one of these):
- Also include a peer valuation comp table (EV/EBITDA, P/E, revenue growth) for 3–5 closest comps.
- Also include a management credibility table: prior guidance vs. actuals for last 4 quarters.
- Score specific named thesis legs in the Thesis & Risk Check if the user gives you the thesis legs.
- Compare this quarter's results and tone to the prior quarter's digest, if the user has shared or the bridge has a pending one — check get_pending.""" + _CLOSING_NOTE

PROMPTS: dict[str, str] = {
    "industry_deep_dive": _INDUSTRY_DEEP_DIVE,
    "investment_research_report": _INVESTMENT_RESEARCH_REPORT,
    "competitive_landscape_analysis": _COMPETITIVE_LANDSCAPE_ANALYSIS,
    "last_4_quarters_summary": _LAST_4_QUARTERS_SUMMARY,
    "quarterly_earnings_digest": _QUARTERLY_EARNINGS_DIGEST,
}


def get_prompt(report_type: str, ticker: str) -> str | None:
    """Returns the filled-in brief for `report_type`, or None if
    `report_type` isn't one of the five known keys — the caller turns that
    into a normal tool error listing the valid options, rather than this
    function raising."""
    template = PROMPTS.get(report_type)
    if template is None:
        return None
    return template.format(ticker=ticker.strip().upper())
