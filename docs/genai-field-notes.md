# GenAI Field Notes — Federal Funds Matchmaker (NC DNCR)

*UNC Solve-a-thon 2026 · Draft for team review, 2026-11-07. The running log behind these notes is [genai-log.md](genai-log.md).*

> Items marked **[TEAM]** need each member's own entry before submission.

## 1. Tools we used

| Tool | Where | Used for |
|---|---|---|
| **Claude Code** (Anthropic) | Build | Architecture options, scaffolding the agents and tests, Azure CLI runbooks, porting the scoring spreadsheet logic to the pipeline, code review, the web app redesign |
| **Azure OpenAI `gpt-6.1-terra`** | Product | Card Agent (top 15 each morning), Route Agent and Apply Assist (on click), cards on demand |
| **Azure OpenAI `gpt-4.1`** | Product | Automatic fallback when the main deployment fails (used once today) |
| Azure OpenAI `text-embedding-3-large` | Deployed, not used yet | Candidate replacement for TF-IDF in plan matching, pending a precision comparison |
| ChatGPT / other assistants | Build | **[TEAM]** each member lists what they used, e.g. for the rubric research, pitch text or front-end layout |

What the LLM does **not** do in the product: fetch data, apply eligibility gates, compute scores, dates or amounts, choose the suggested decision, or check its own quotes. All of that is code.

## 2. Key prompts

**1. Card Agent system prompt (`prompts/card_agent.md`, v2.2)** — the most important prompt in the product. The design choice that made it work: split the input into three blocks and give each a different rule.

> You get three blocks: LISTING TEXT (you may quote it), PLAN TEXT (you may quote it), FACTS — computed by our code; use them to reason, but NEVER quote them …
> evidence: 2–4 items … quote = 6–30 words copied CHARACTER FOR CHARACTER from that block … A reviewer program will reject any quote it cannot find.

Why: in v1 the model mixed computed facts ("Days left: 38") into quotes and restated amounts differently from the listing. After the split, 52 of 52 quotes on the deployed run were found word-for-word.

**2. Verifier feedback (the evaluator–optimizer loop)** — when quotes are not found, the card is rewritten once with:

> These quotes were not found verbatim in the source; copy exact text or remove them: …

This repaired missing quotes on 2–3 cards per run in testing, at the cost of one extra call each.

**3. Figure guard (self-repair)** — if a card contains a date or dollar amount that is not in the listing, it gets one more try:

> These dates or dollar amounts are not in the listing; remove them or use 'not stated': …

Anything still unsupported is masked by code, so a hallucinated deadline cannot reach the reader.

**4. Route Agent (`prompts/route_agent.md`)** — kept deliberately small:

> Write `message` as a plain, polite email body (under 120 words) … Do not invent deadlines, dollar amounts, or eligibility facts. Leave `deadline` null; code fills it.

**5. Build prompt that shaped the architecture** (to Claude Code, lead): *"Is this structure a strict AI agent workflow?"* The answer led us to call it what it is — a fixed LangGraph workflow with LLM steps and one evaluator–optimizer loop, not autonomous agents — and to move every computable step out of the LLM.

## 3. Where AI was wrong, and how we caught it

| # | What the AI said or did | How we caught it | What we changed |
|---|---|---|---|
| 1 | The rule-based scorer suggested **Chase** for NOAA DARRP. The GPT-6 card read the eligibility text and said "No. Applications from state governments will not be considered." (Here the model was right and our rules were wrong.) | Reading all 15 live cards | The Verifier now flags any card that says DNCR cannot apply and drops confidence to Low; the UI puts the blocker on the card face |
| 2 | First live card for NEH Public Humanities said eligibility was "unclear" and quoted "Eligibility text: not stated" — a label from our own prompt, not the listing | The Verifier rejected the quote; the record listed State governments as an applicant type | The prompt now shows Grants.gov applicant types as quotable listing text (203 of 1,364 listings have no free-text eligibility) |
| 3 | AI-generated code wrote `\b` inside a shell heredoc, which became a backspace character, so two regexes (the region gate and the "card says no" check) silently never matched | Tests on known cases failed; inspecting the bytes showed `chr(8)` | Replaced with real `\b`; regression tests on the exact phrases |
| 4 | The ported scoring script fell back to calibration constants on a different scale for small batches, so every meaning score S was 0 on the 40-record test set | A test asserting S > 50 for a known-good grant | Full-feed calibration (L = 0.012, H = 0.097, τ = 23.65) used whenever a batch is too small to calibrate itself |
| 5 | The AI assumed `temperature: 0` works on every chat model | Smoke test: GPT-6 deployments return HTTP 400 | The gateway sends `reasoning_effort` to GPT-6 and `temperature` only to gpt-4.1; cards are cached so wording is stable within a prompt version |
| 6 | The card cache key hashed the whole prompt, which included days-left and the score, so the cache never hit | Lead's code review | Key = prompt version + deployment + listing + objective; days-left and score removed from the prompt |
| 7 | After the team finalized the rubric, the AI reported the deployed site as working, but its scores still used the old formula (lines 50/60) | A teammate compared the site with the final spreadsheet script | Ported the final rubric; `scripts/rubric_parity.py` now runs both on the same data (A/P/V identical, Final within 0.075 on 1,012 grants) |
| 8 | Two independent AI-assisted ports of the new rubric differed: one wrote the chance case into the output and one did not, so the money-type filter showed "Unknown" for every grant | Checking the filter counts on real data in the browser (200 of 200 Unknown) | The API derives the case with the scorer's own function, so the label can never disagree with the score |
| 9 | Claude Code added itself as a co-author on commits without being asked | The lead noticed it in the commit list | History rewritten, rule saved: no AI attribution lines in commits or PRs |
| 10 | The AI ran `rm -rf data` to clear local output, which also deleted a tracked placeholder file | `git status` before committing | Restored it; clear local output with a temp data folder instead |

Pattern: most errors were **confident and plausible** — code that runs, a status report that sounds right. What caught them was always something outside the AI: a test on a known case, the Verifier, reading the real output, or a teammate comparing against the source of truth.

## 4. Practices that worked

1. **Code computes, the model writes.** Every number on a card comes from code; the model only quotes and explains.
2. **Verify, don't trust.** Every quote is checked against its source; failures get one retry with feedback, then a lower confidence.
3. **Version prompts and cache by version.** Any prompt change bumps `PROMPT_VERSION`, which invalidates cached cards.
4. **One gateway.** Redaction, per-model parameters, structured output, retries, fallback and an audit log in one place (`core/llm.py`).
5. **Test with real data early.** The 40-record fixture is real Grants.gov data; the full 1,364-listing run found problems the fixture could not.
6. **Keep a log of AI mistakes as you go** ([genai-log.md](genai-log.md)); it became this document.

## 5. What we would do differently

- Label an evaluation set **before** tuning the rubric, by people who do not write scoring code. **[TEAM: add precision@15 when the labels are done]**
- Compare GPT-6.1 models and embeddings on the same labeled set (`scripts/eval_card_agent.py` is ready).
- Pin the UX contract (what each number means) before building the first UI; we redesigned once after user testing.

## 6. Team reflections **[TEAM]**

One or two sentences each: what AI helped you do faster, and one time you had to correct it.

- Lead:
- Criteria:
- LLM-A:
- LLM-B:
- Front end:
