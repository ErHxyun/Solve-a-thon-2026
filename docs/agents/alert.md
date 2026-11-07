# Alert — Spec

| | |
|---|---|
| Owner | lead |
| Branch | `agent/alert` |
| Code | `agents/alert.py` |
| Type | Code (date math) |
| Runs | Daily batch, after Verifier |
| Status | closing_soon, new_since_yesterday, waitlist_21_days work; waitlist_opening TODO |

## 1. Description
Makes sure short windows are not missed (the ESHPF grant had only 31 days). Flags items closing soon, items new since yesterday, and reminds about Waitlisted items when they open or are 21 days from closing. Serves **R5** (warns early) and the "Race the Clock" bonus.

## 2. Input
| Source | Field |
|---|---|
| `state["opportunities"]`, `state["top"]` | status, days_to_close |
| `decisions/dncr.json` | Waitlist decisions |
| `opportunities/previous_ids.json` | ids seen in the previous run |

## 3. Process
1. Top-N items closing in ≤ 10 days → `closing_soon`.
2. Top-N items not seen in the previous run → `new_since_yesterday`.
3. Waitlisted items: posted and ≤ 21 days left → `waitlist_21_days`; forecast that became posted → `waitlist_opening`.
4. Save today's ids for tomorrow's comparison.

## 4. Procedure
- ✅ closing_soon, new_since_yesterday, waitlist_21_days
- ☐ `waitlist_opening` (store status per id; detect forecasted → posted)
- ☐ Thresholds from config (10 / 21 days)
- ☐ Optional: daily digest text (what a morning email would say), for the roadmap demo

## 5. Validation
- Output validates as `Alert`; dates only from `Opportunity` (never from cards).
- First run (no previous ids) must not flag everything as new.

## 6. Output
| State key / file | Shape |
|---|---|
| `state["alerts"]`, `alerts/latest.json` | `list[Alert]`: opp_id, kind, message, days_to_close |

## 7. Testing module
`tests/test_alert.py` (create it)

| Test | Input | Expected |
|---|---|---|
| closing soon | days_to_close = 7 | one `closing_soon` |
| not soon | days_to_close = 30 | none |
| first run | no previous ids | no `new_since_yesterday` |
| new item | previous ids missing one | one `new_since_yesterday` |
| waitlist 21 | Waitlist decision + 15 days left | `waitlist_21_days` |

## 8. Quality assurance
- **Done when:** all five tests pass and the UI shows alerts at the top.
- **Metrics:** alerts per day (keep it small; too many alerts = noise).
- **GenAI log:** any off-by-one or timezone issue the AI introduced.
