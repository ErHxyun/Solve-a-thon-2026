# <Agent name> — Spec

| | |
|---|---|
| Owner | |
| Branch | `agent/<name>` |
| Code | `agents/<name>.py` |
| Type | Code / LLM / Code + embeddings |
| Runs | Daily batch / On click |
| Status | Baseline / In progress / Done |

## 1. Description
What job does this agent do, in one paragraph? Which step of a grants specialist's work does it replace?
Which requirement(s) (R1–R8) does it serve?

## 2. Input
| Source | Field / file | Notes |
|---|---|---|

## 3. Process
High-level steps, in order (what happens, not how).

## 4. Procedure
Implementation checklist for the owner (what to build, in order). Mark done items ✅.

## 5. Validation
Checks the agent does **at runtime** on its own input and output (schema validation, rules, guards).
What does it do when a check fails?

## 6. Output
| State key / file | Shape | Example |
|---|---|---|

## 7. Testing module
`tests/test_<name>.py`

| Test | Input | Expected |
|---|---|---|

## 8. Quality assurance
- Definition of done (checklist)
- Metrics and target values
- Known failure modes and how we handle them
- What to record in the GenAI log
