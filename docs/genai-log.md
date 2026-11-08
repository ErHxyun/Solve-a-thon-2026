# GenAI Log (feeds the GenAI Field Notes deliverable)

Everyone adds rows as they work. The deliverable needs: tools used, 3–5 key prompts, and at least one place where AI was wrong and how we caught it.

## Tools used
| Tool | Used for | Who |
|---|---|---|
| Claude (Claude Code) | Planning, architecture, scaffolding code, Azure setup steps | Lead |
| Azure OpenAI gpt-6.1-terra / gpt-4.1 | Card Agent, Route Agent, Apply Assist | System |
| Azure OpenAI text-embedding-3-large | Fit Scorer semantic matching | System |
| | | |

## Key prompts
| Date | Who | Prompt (or file + version) | What it produced | Kept? |
|---|---|---|---|---|
| 2026-11-07 | Lead | `prompts/card_agent.md` v1 | Decision cards with evidence quotes | Replaced by v2 |
| 2026-11-07 | LLM-A | `prompts/card_agent.md` v2: split LISTING / PLAN (quotable) from FACTS (computed, never quoted); 6–30-word quotes, one per key field | Prompt for the live run | Pending live test |

## Where AI was wrong
| Date | Who | What the AI said / did | How we caught it | Fix |
|---|---|---|---|---|
| 2026-11-07 | Lead | First live GPT-6 card (NEH Public Humanities) said eligibility was "unclear" and quoted "Eligibility text: not stated" | Verifier rejected the quote (it was our prompt label, not listing text); the record listed State governments as an applicant type | Prompt now shows the Grants.gov applicant types as quotable listing text; 203 of 1,364 live listings have no free-text eligibility |
| 2026-11-07 | Lead | Rule-based scorer suggested **Chase** for NOAA DARRP; the GPT-6 card said "No. Applications from state governments will not be considered." | Read all 15 live cards | Verifier now flags "Card says DNCR cannot apply" and drops the card to Low — the model caught what applicant-type codes missed |
| 2026-11-07 | Lead | Assumed `temperature: 0` works on every chat model | Smoke test: GPT-6 deployments return HTTP 400 ("only default (1) supported") | Gateway sends `reasoning_effort` instead for GPT-6 |
| 2026-11-07 | LLM-A (Claude Code) | Card cache key hashed the whole user prompt, which contained days_to_close and the ranking score, so the key changed every day and the cache never hit | Lead's code review of `agent/card` | Key = prompt version + deployment + listing text/facts + objective; days-left and score removed from the prompt; tests for next-day hit and for misses on real changes |
| 2026-11-07 | LLM-A (Claude Code) | First draft of the Alert history fell back to `previous_ids.json` whenever no earlier-date run existed, so a second run on the same day compared today with today and dropped every "new" flag | Reading the rerun case before testing; then a test (`test_new_since_previous_day_and_rerun_same_day`) | Legacy file used only when no history exists at all; day-one reruns return "nothing new" |
| 2026-11-07 | LLM-A (Claude Code) | While debugging the Alert agent it ran `rm -rf data` inside the repo to clear local output, which also deleted the committed `data/.gitkeep` | `git status` before committing showed `D data/.gitkeep` | Restored with `git checkout -- data/.gitkeep`; clear local output with `rm -rf data/*` (keeps `.gitkeep`) or a temp `LOCAL_DATA_DIR` instead |
| 2026-11-07 | Lead | Baseline tagged ORLP and RARI as "already yours" via CFDA 15.916 | Reviewing the first pipeline output by eye | Match recurring programs on CFDA **and** program name (P3 TODO) |
| 2026-11-07 | LLM-A (Claude Code) | First money-type rule (any of disaster / supplemental / emergency / recovery in title+summary) labelled RARI, NIH CCRP and NIH COBRE "one-time disaster" | Ran the card agent behind the real Gatekeeper / Fit Scorer / Verifier and read the 14 cards | Title words, or ≥ 3 body mentions; CFDA hint also needs a name match; regression tests added |
| 2026-11-07 | LLM-A (Claude Code) | `fix/card-eligibility` changed the card user-prompt layout (added "Eligible applicant types") but `PROMPT_VERSION` stayed v2.1. The cache key covers the listing, objective, system prompt and version, not the user-prompt layout, so cards cached before the fix would keep the old "eligibility unclear" answer | Diffing `agent/card` against `main` after the merge | `PROMPT_VERSION` → v2.2; comment now says to bump it for user-prompt layout changes too |
| 2026-11-07 | LLM-A (Claude Code) | Checking the deployed app, the AI first reported the front end as wired and working; the scores on the live site were still from the **old** rubric (A × (0.70 + 0.15U + 0.15W), lines 50/60), while the team's final `dncr_grant_scoring.py` uses A × (0.30 + 0.20P + 0.30V + 0.15U/3 + 0.05W/100), lines 35/38 | The user shared the final script; diffing its parameters against `agents/fit_scorer.py` | Ported P, V, the agency-type floor, new lines and route rules; added `expected_awards` (Grants.gov `numberOfAwards`); `scripts/rubric_parity.py` runs both on the same CSV: A/P/V identical, Final within 0.075, same objective for 1,012/1,012 grants |
| | | | | |
