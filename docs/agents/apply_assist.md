# Apply Assist — Spec

| | |
|---|---|
| Owner | P1 |
| Branch | `agent/apply-assist` |
| Code | `agents/apply_assist.py`, prompt `prompts/apply_assist.md`, `reference/process_map.json` |
| Type | LLM (deployment `chat`, reasoning_effort medium) |
| Runs | On click (user presses **Chase**) |
| Status | Draft + offline pack work; process map needs research |

## 1. Description
After the user chooses Chase, produces a starter pack so the application starts the same day: requirements quoted from the listing, SF-424 information to gather, a narrative outline (headings only), a backward timeline from the deadline, and an email to the division lead. It drafts **scaffolding only**: no narrative, no budget, never asks for or stores EIN/UEI/bank/credentials, never submits. Serves "spend time writing, not searching" and **R7**.

## 2. Input
| Source | Field |
|---|---|
| UI call | `Opportunity`, `Card` (money_type, division, prerequisites) |
| `reference/process_map.json` | steps (who / AI can help), prep-time estimates by money type, SF-424 fields |

## 3. Process
1. Pick the prep-time estimate for the card's money type.
2. Prompt the LLM with listing text, close date, steps, SF-424 list.
3. Code overwrites `opp_id`; offline → template pack.
4. UI shows the pack and offers a download (.md).

## 4. Procedure
- ✅ Prompt v1, schema, offline pack
- ☐ Research the real application path (workstream 1) → update `process_map.json` with sources; replace estimates
- ☐ Validate the timeline: every "Day -N" step fits before the close date; if days_to_close < prep time → flag "too late for this round → Waitlist"
- ☐ Download as Markdown in the UI (with P5)
- ☐ Try 3 live packs; record what was useful and what was wrong

## 5. Validation
- Output validates as `StarterPack`.
- No sensitive values requested or produced (guard + prompt + test).
- Timeline uses only the listing close date (code passes it in).

## 6. Output
`StarterPack`: requirements[], sf424_fields_to_gather[], outline[], timeline[], email_to_division_lead, open_questions[].

## 7. Testing module
`tests/test_apply_assist.py` (create it)

| Test | Input | Expected |
|---|---|---|
| offline pack | LLM disabled | valid pack, SF-424 list from process map |
| no secrets | live/mocked output | no EIN/UEI-like strings (guard finds none) |
| too late | days_to_close 5, competitive (30-day prep) | flagged too late |
| opp_id overwrite | mocked wrong id | corrected |

## 8. Quality assurance
- **Done when:** 3 live packs reviewed by P1; requirements traceable to the listing; no narrative or budget text drafted.
- **Metrics:** reviewer rating (useful / partly / not useful) per pack section.
- **GenAI log:** where the AI invented requirements not in the listing.
