# Gatekeeper — Spec

| | |
|---|---|
| Owner | P3 |
| Branch | `agent/gatekeeper` |
| Code | `agents/gatekeeper.py` |
| Type | Code (rules) |
| Runs | Daily batch, after Collector |
| Status | Baseline rules work; region locks and research detection need work |

## 1. Description
Applies the three hard gates from the Field Notes before anything is ranked, so the short list never shows something DNCR cannot or should not apply for. Items that fail stay visible **with the reason** (never silently hidden). Replaces "scan the eligibility section of each listing." Serves **R2** (short list) and trust (no NIH lab grant at #1).

## 2. Input
| Source | Field | Notes |
|---|---|---|
| `state["opportunities"]` | `applicant_types[].id`, `eligibility_text`, `description`, `status`, `days_to_close`, `agency_code` | Grants.gov code `00` = State governments, `99` = Unrestricted, `25` = Others (see text) |

## 3. Process
1. **Eligible:** state governments can apply (`00` or `99`; `25` only if the text mentions states → flag "verify"), and the opportunity is not locked to another region or land type (BLM land, Gulf states, territories).
2. **Open or coming:** posted or forecasted, and not past the deadline.
3. **Not research for researchers:** NIH biomedical ×0.4, other research ×0.75 (penalty, not removal).
4. Produce a `GateResult` with human-readable reasons.

## 4. Procedure
- ✅ Baseline: codes 00/99/25, closed check, NIH prefix, research terms, small region list
- ☐ Region / federal-land list from the Field Notes false positives (BLM Youth Conservation Corps; Gulf-only; specific states)
- ☐ Better research detection: funding instrument + "principal investigator", "R01/R21/U24", NSF/DOD research agencies
- ☐ `25` handling: look for "state" / "state agencies" / "units of state government" in the eligibility text
- ☐ Return counts per failure reason (for the evaluation funnel slide: 1,454 → after gates → 15)

## 5. Validation
- Every input must validate as `Opportunity`; every output as `GateResult`.
- A failed item must have ≥ 1 reason (test this).
- Gates never use the LLM (deterministic, explainable).

## 6. Output
| State key / file | Shape |
|---|---|
| `state["gates"]` | `list[GateResult]`: `opp_id, passed, eligible, open_or_coming, research_penalty, reasons[]` |
| `gates/latest.json` (written by publish) | same |

## 7. Testing module
`tests/test_gatekeeper.py` (exists, 6 tests)

| Test | Input | Expected |
|---|---|---|
| T1 NIH | agency `HHS-NIH11` | passes, penalty 0.4 |
| T2 BLM | text "Bureau of Land Management lands" | fails with reason |
| not state-eligible | only `12` nonprofits | fails, reason mentions State governments |
| closed | `days_to_close = -1` | fails |
| fixture mix | 40 real records | some pass, some fail |
| ☐ add | BLM Youth Conservation Corps record | fails |
| ☐ add | `25` + "state agencies" in text | passes with "verify" reason |

## 8. Quality assurance
- **Done when:** on the labeled set, no item labeled Chase/Route is gated out (gate recall 100%), and every known false positive in the Field Notes is gated or down-weighted.
- **Metrics:** % of feed passing; reasons histogram; gate recall on labeled set.
- **Known failure modes:** over-strict region terms that accidentally match NC (e.g., "Appalachian" includes western NC — do not add it).
- **GenAI log:** any eligibility code or rule the AI suggested that turned out wrong.
