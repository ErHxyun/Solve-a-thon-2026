# Alert — Spec

| | |
|---|---|
| Owner | lead |
| Branch | `agent/alert` |
| Code | `agents/alert.py` |
| Type | Code (date math) |
| Runs | Daily batch, after Verifier |
| Status | All four alert kinds + morning digest; 19 alert tests (24 in suite) pass offline |

## 1. Description
Makes sure short windows are not missed (the ESHPF grant had only 31 days). Flags items closing soon, items new since yesterday, and reminds about Waitlisted items when they open or are 21 days from closing. Serves **R5** (warns early) and the "Race the Clock" bonus.

## 2. Input
| Source | Field |
|---|---|
| `state["opportunities"]`, `state["top"]` | status, **close_date** (days left are recomputed from the run date) |
| `decisions/dncr.json` | the user's **latest** decision per item (Chase / Waitlist / Skip / Route) |
| `opportunities/seen_history.json` | `{run_date: {opp_id: status}}` for the last 14 run dates |
| `opportunities/previous_ids.json` | legacy baseline, used only when no history exists yet |

## 3. Process
1. **Today = run date in Eastern time.** Days left = `close_date − today`, never the `days_to_close` stored at ingest (it goes stale the next morning). Rolling / no date → no deadline alert.
2. **Previous run = the last run on an earlier date.** Running twice in one day still compares with yesterday; day one (even rerun) flags nothing as new.
3. Waitlisted items (on the short list or not):
   - was `forecasted` in the previous run, now `posted` → `waitlist_opening`
   - otherwise posted and ≤ 21 days left → `waitlist_21_days`
4. Short-list items **and Chased items**, posted, 0–10 days left → `closing_soon` ("You're chasing this; it closes …" for Chased). Skipped items are silent; Waitlisted items get their waitlist alert instead of a duplicate.
5. Short-list items not in the previous run → `new_since_yesterday` (not for Skipped items).
6. Sort: opening → closing soon (fewest days first) → waitlist 21 days → new.
7. Save today's snapshot (rerun overwrites; keep 14 dates), `alerts/latest.json`, and `alerts/digest.json`.

## 4. Procedure
- ✅ closing_soon, new_since_yesterday, waitlist_21_days
- ✅ `waitlist_opening` (status per id per run date; forecasted → posted)
- ✅ Thresholds from env: `ALERT_CLOSING_SOON_DAYS` (10), `ALERT_WAITLIST_WARN_DAYS` (21); not in `core/config.py` to keep `core/` untouched
- ✅ Daily digest (`alerts/digest.json`): headline + counts + one line per alert, for the morning email
- ✅ Chase reminders, Skip silences, latest decision wins (undo works)
- ☐ Email sending (roadmap: Azure Communication Services); the digest is the content
- ☐ UI shows `alerts/latest.json` at the top (frontend)

## 5. Validation
- Output validates as `Alert`; dates only from `Opportunity` (never from cards); days left from the run date.
- First run, and reruns on day one, flag nothing as new.
- Closed items (days < 0) and forecasts never get `closing_soon`.

## 6. Output
| State key / file | Shape |
|---|---|
| `state["alerts"]`, `alerts/latest.json` | `list[Alert]`: opp_id, kind, message, days_to_close |
| `alerts/digest.json` | `{date, headline, counts{kind: n}, lines[]}` e.g. "DNCR today: 12 items on the short list, 3 close within 7 days, 2 new" |
| `opportunities/seen_history.json` | `{run_date: {opp_id: status}}` |

## 7. Testing module
`tests/test_alert.py` — 19 tests, all with a fixed run date.

| Test | Expected |
|---|---|
| the 5 baseline spec tests | ✅ |
| stored 30 days but close date is 5 days after the run date | 5 days |
| closed (−1) and rolling items; forecast closing in 3 days | no closing_soon |
| new since previous day; rerun the same day | still flags new vs. yesterday |
| rerun on day one | nothing new |
| 20 daily runs | history keeps 14 |
| Waitlisted forecast → posted | `waitlist_opening` only |
| Waitlisted, off the short list, 12 days left | `waitlist_21_days` |
| Waitlisted, 5 days left | one alert, not two |
| Skipped | silent; Skip then Chase → alerts again |
| Chased, off the short list, 4 days left | `closing_soon` "You're chasing this" |
| sort order + digest headline + latest.json written | ✅ |
| Integration (fixture, real Gatekeeper / Fit Scorer): nearest short-list deadline is 16 days out → 0 alerts; IMLS items closing in 6 days are gated (no state eligibility) | ✅ correct |

## 8. Quality assurance
- **Done when:** all five tests pass and the UI shows alerts at the top.
- **Metrics:** alerts per day (keep it small; too many alerts = noise).
- **GenAI log:** any off-by-one or timezone issue the AI introduced.
