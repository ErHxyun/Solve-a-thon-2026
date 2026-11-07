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
| 2026-11-07 | Lead | `prompts/card_agent.md` v1 | Decision cards with evidence quotes | |

## Where AI was wrong
| Date | Who | What the AI said / did | How we caught it | Fix |
|---|---|---|---|---|
| 2026-11-07 | Lead | Assumed `temperature: 0` works on every chat model | Smoke test: GPT-6 deployments return HTTP 400 ("only default (1) supported") | Gateway sends `reasoning_effort` instead for GPT-6 |
| 2026-11-07 | Lead | Baseline tagged ORLP and RARI as "already yours" via CFDA 15.916 | Reviewing the first pipeline output by eye | Match recurring programs on CFDA **and** program name (P3 TODO) |
| | | | | |
