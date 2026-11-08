# Solution Design — Federal Funds Matchmaker for NC DNCR

*UNC Solve-a-thon 2026 · OSBM "Federal Funds Matchmaker" challenge · Draft for team review, 2026-11-07*

> Items marked **[TEAM]** need a number or a sentence from the team before submission.

## 1. Problem

The NC Department of Natural and Cultural Resources (DNCR) runs state parks, historic sites, museums, the zoo and aquariums, archives, the State Library and the Arts Council. Federal money for this work is spread across dozens of agencies (NPS, NEH, NEA, IMLS, USFWS, USFS, NOAA, FEMA …) and posted on Grants.gov alongside thousands of listings DNCR cannot use.

DNCR has **one grants developer, who is not technical**. Today that person relies on keyword searches and the Grants.gov email digest. The three failure modes we heard about:

1. **Missed windows.** Some programs open for about 30 days; finding them a week late can sink an application.
2. **Noise.** Keyword search returns hundreds of hits. In our run, 313 listings match eight plan keywords, and 131 of those that pass the gates are still not worth DNCR's time (research grants, other regions, other agencies' programs).
3. **No memory.** A spreadsheet doesn't track what was considered, what was handed to another office, or where an application stands.

**Goal:** every morning, a short list of the opportunities that fit DNCR's 2025–2029 Strategic Plan, each explained in plain English with evidence the reader can check, and one decision per item: **Chase, Waitlist, Skip or Route** (another NC office should apply).

## 2. Users and decisions

| User | Needs |
|---|---|
| DNCR grants developer (primary) | A list short enough to read with coffee; why each item fits; what could block DNCR; one click to act |
| Division leads (secondary) | A forwarding note or starter pack they can act on |
| OSBM / leadership (reader) | Evidence that nothing obvious is missed and that a person makes every call |

Design rule from the UX review: **show the decision first, the explanation second, and the workflow only when the user chooses to act.** The model *suggests*; the person *decides*. Priority, plan match, confidence, eligibility and the person's own decision are five different things and are shown separately.

## 3. Architecture

### 3.1 Why an agentic workflow, not autonomous agents

We use a **fixed LangGraph state graph** in which each step has one job, a typed input and output, and tests. Code does everything that can be computed (fetching, gates, scoring, dates, routing rules, quote checks). The LLM is used only where language is the work: reading a listing and writing a card, a forwarding note, or a starter pack. This keeps the system predictable, cheap (about 15 LLM calls per morning), auditable, and safe to run unattended. An autonomous agent that decides what to read and when to stop would be harder to test and to explain to a state agency.

```
START → Collector → Gatekeeper → Fit Scorer → Card Agent → Verifier ─┬→ Alert → Notifier → publish → END
                                                  ▲                  │ quotes not found (once)
                                                  └── card_retry ◄───┘
On click (web app): Route Agent · Apply Assist · card on demand (same Verifier check)
```

### 3.2 Steps

| Step | Type | What it does | Key safeguards |
|---|---|---|---|
| **Collector** | code | Pages through every posted and forecasted Grants.gov listing that state governments can apply for (applicant types 00 State, 25 Others, 99 Unrestricted); fetches details with 8 workers, 4 retries and a 20 h cache; records a keyword-search baseline | One bad record never stops the run; zero records = hard failure |
| **Gatekeeper** | rules | G1 applicant type; G2 region (other states, territories, Gulf-only RESTORE Act, foreign posts); G3 open and not stale (forecasts or rolling notices whose newest year is 2+ years old) | Pass/fail only, with a reason per gate |
| **Fit Scorer** | code | Five factors (§4); suggested decision; who should apply (route rules by agency and title) | Same rubric as the team's spreadsheet; parity script; every item carries a written rationale |
| **Card Agent** | LLM | Writes a card for the top 15 from LISTING and PLAN text only. FACTS (dates, amounts, hints) are computed by code and never quoted | Structured output (Pydantic); dollar/date guard with one self-repair; cache keyed by prompt version |
| **Verifier** | code | Every quote must be found in its source (fuzzy ≥ 90). If quotes are missing, the card goes back once with feedback (evaluator–optimizer loop). Flags cards that say DNCR cannot apply | Sets confidence High / Medium / Low |
| **Alert / Notifier** | code | Closing soon, waitlist opening, new since yesterday; weekly email plus same-day email only for urgent triggers | Preview mode by default; sent keys recorded only after a successful send |
| **Route Agent / Apply Assist** | LLM, on click | Forwarding note to the right NC office; starter pack (requirements, SF-424 fields to gather, outline, timeline, email to the division lead) | Drafts only; never sent; no IDs or credentials ever go to the model |

### 3.3 LLM gateway and guardrails

All model calls go through `core/llm.py`:

- **Azure OpenAI only** (our own resource). Chat: `gpt-6.1-terra`; fallback `gpt-4.1`.
- **Guard** redacts SSNs, EINs, UEIs, bank routing/account numbers, passwords and API keys before any call and logs what was redacted.
- **Per-model parameters**: GPT-6 deployments reject `temperature`, so they get `reasoning_effort`; gpt-4.1 gets `temperature 0`.
- **Structured outputs** validated with Pydantic; retries with backoff; automatic fallback deployment.
- **Audit log**: one JSONL line per call (agent, deployment, prompt hash, latency, tokens, what was redacted). Today's run: 44 calls, 0 failures, median 4.1 s.
- **Tools** for agents are read-only and allow-listed (owner lookup, objective text, days until, prep-time, backward timeline), with at most 3 tool steps.
- **Nothing is submitted or sent** by the system. Email is off (preview) until a person configures it.

### 3.4 Deployment (Azure)

One Docker image, three roles. Container Apps **Job** runs the pipeline daily at 10:00 UTC (`MODE=job`). Container **App** serves the FastAPI backend and web app (`MODE=app`). Results live in **Azure Blob** as a JSON data contract, so the web app only reads and never reruns the pipeline. Secrets are Container Apps secrets, never in the image (`.dockerignore` excludes `.env`, `data/`, `logs/`). Runbook: `docs/deploy.md`.

## 4. Scoring rubric ("DNCR Grant Scoring Rules, Final")

| Factor | Meaning | How it is computed |
|---|---|---|
| **A** plan match (0–100) | Is it ours? | max( (S + K)/2 × research penalty, 70 if the listing names an agency type DNCR is — state arts agency, SHPO, state library, state archives/museum/parks, natural heritage program ). S = TF-IDF similarity of title + first 1,500 characters to each of the 25 objectives, calibrated on the full feed (median NIH listing = 0, 90th percentile of DNCR-type funders = 100). K = IDF-weighted phrase hits (title counts double; each negative word such as "clinical" halves K). NIH × 0.4; NSF/NASA/BAA × 0.75 |
| **P** chance (0–1) | Can we win? | 1 for formula / every-state programs; expected awards ÷ 56 if only states may apply; published success rate when known; otherwise 0.25 (estimate) |
| **V** value (0–1) | Is it worth it? | Log of the typical award: $10K = 0, $3M = 1; not stated = 0.5 |
| **U** urgency (0–3) | Does the plan say now? | Urgency tier of the matched objective (Helene recovery and A250 are 3); one-time words lift to 2 |
| **W** workload (0–100) | Can one person do it? | Median of a 10,000-run simulation of 11 application stages (triangular hours) for the application's profile; 20 h = 100, 150 h = 0 |

**Priority (Final) = A × (0.30 + 0.20 P + 0.30 V + 0.15 U/3 + 0.05 W/100).** A is a multiplier: the other factors adjust a good fit but cannot rescue a bad one.

**Suggested decision:** Skip if A < 35 → Route if another NC office should apply → Waitlist if forecast / no deadline → **Chase if Final ≥ 38** (the Chase list is sized to about one year of the developer's hours) → otherwise "You decide".

W_S = 0.5 and the agency-type floor were chosen by nested cross-validation on past wins; the five weights by a budget simulation, with a past-wins blind test as a guardrail and the one-standard-error rule toward equal weights. Our pipeline reproduces the team's spreadsheet exactly on A, P and V and within 0.075 on Final for 1,012 grants (`scripts/rubric_parity.py`).

## 5. Human in the loop (web app)

- **Today**: only grants suggested Chase, ranked by priority; search by name, number or agency.
- **Compact card**: priority badge; award ceiling, deadline and plan match tiles; one-line purpose; one-line blocker (eligibility to confirm, a Verifier warning, the suggested owner for Route, or the first prerequisite); read-only AI suggestion; Save / Start tracking / Dismiss.
- **Details**: six groups — strategic fit; eligibility and blockers (Verified only when a person marks it); funding and competition (mechanism and who competes as separate fields); effort and timing (weeks at 10 h/week vs days left); scoring breakdown with the formula filled in; evidence completeness and quotes.
- **Find Funding**: filters (division; money type — Formula / State-only competition / Open competition / Unknown; deadline window; minimum confidence; AI suggestion), four sorts, live count, zero-results state. Nothing reruns.
- **My Tracker**: stage, owner, next action + due date, notes, my decision and a change log per grant. A pipeline rerun never resets it.

## 6. Evaluation

| Question | Result |
|---|---|
| Does the funnel cut the noise? | 1,364 listings → 891 pass gates → 863 scored → 29 worth a look → 5 Chase |
| Are the cards grounded? | 15/15 cards written; 52/52 quotes found word-for-word; the retry loop repaired missing quotes on 2–3 cards in earlier runs (0 needed on the deployed run) |
| Does the model catch what rules miss? | Yes: NOAA DARRP passed the applicant-type rules, but the card quoted "Applications from state governments will not be considered"; the Verifier flagged it and dropped confidence to Low |
| Does it match the team's rubric? | A/P/V identical, Final within 0.075 on 1,012 grants |
| vs. keyword search | The 8-keyword baseline finds all 5 Chase items, but it also returns 313 listings, and 131 of the 149 that pass our gates are Skip. 11 of the 29 items worth a look are **not** found by keyword search. The value is filtering and explaining, not only discovery |
| Precision@15 (human labels) | **[TEAM]** two teammates who do not write scoring code label the top 15 + 15 random items "DNCR should look at this: yes / no"; report precision@15 vs keyword search |
| Reliability | 155 offline tests; 44 LLM calls today, 0 failures, median 4.1 s; daily job on Azure |

## 7. Risks and limitations

- **Eligibility from the synopsis, not the NOFO.** We show "Eligibility pending" until a person confirms; hard blocks surface on the card.
- **Model wording varies** (GPT-6 has no temperature). Cards are cached by prompt version; the Verifier checks every quote.
- **Estimates are labeled as estimates** (chance, hours). Confidence describes evidence quality, not the chance of winning.
- **Single source.** Grants.gov only.
- **Pilot scope.** One shared tracker, no user accounts; the email sender uses a free Azure subdomain.

## 8. Roadmap

1. Read the full NOFO PDF for eligibility, match and deadlines; mark them Verified with a citation.
2. Add SAM.gov assistance listings and the NC foundations list.
3. Learn from decisions: use Chase/Skip history to recalibrate weights quarterly.
4. Accounts and roles (division leads see items routed to them); Teams/Outlook integration.
5. Expand to other agencies: the agency profile (objectives, divisions, route rules) is a data file.

## 9. Responsible use

No credentials, IDs or personal data are sent to the model. Every AI statement on a card is either a checked quote or labeled as the model's reasoning. Every number on a card comes from code, not the model. A person makes every decision, and nothing is submitted or sent automatically. AI use during the build is documented in the GenAI field notes.
