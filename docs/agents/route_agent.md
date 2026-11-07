# Route Agent — Spec

| | |
|---|---|
| Owner | P3 |
| Branch | `agent/route` |
| Code | `agents/route_agent.py`, prompt `prompts/route_agent.md` |
| Type | Code lookup + LLM wording (deployment `chat`) |
| Runs | On click (user presses **Route**) |
| Status | Lookup + LLM draft + offline draft work; lookup table is a starter |

## 1. Description
When an opportunity fits but someone else should apply (a DNCR division, a local government DNCR passes money to, or another NC agency such as Emergency Management), drafts a short forwarding note with the reason and deadline. "Not knowing who should respond" is one of OSBM's three reasons money is missed. It never sends anything. Serves **R6** (human decides) and the Route decision.

## 2. Input
| Source | Field |
|---|---|
| UI call | `Opportunity`, `Card` |
| `reference/program_owners.json` | rules: match on `top_agency_code` or keyword → owner + reason |

## 3. Process
1. Look up the owner with code rules; if none match, use the card's `who_applies`.
2. Ask the LLM to word a polite note (< 120 words) with the reason and link.
3. Code overwrites `route_to`, `reason`, `opp_id`, and **`deadline` from the listing**.
4. Offline → template note.

## 4. Procedure
- ✅ lookup_owner, LLM draft, offline draft, deadline from listing
- ☐ Grow `program_owners.json` with P1 (Emergency Management, DPI, DEQ, DHHS, Commerce, local pass-through)
- ☐ Rule priority + "no match" path tested
- ☐ UI wiring with P5: button → draft shown in an editable text box → copy button (no send)

## 5. Validation
- Output validates as `RouteDraft`; deadline equals `opp.close_date`.
- Guard runs inside the gateway.
- Nothing is emailed; the UI only displays and copies.

## 6. Output
`RouteDraft`: opp_id, route_to, reason, deadline, message.

## 7. Testing module
`tests/test_route_agent.py` (create it)

| Test | Input | Expected |
|---|---|---|
| DHS → Emergency Management | top_agency_code DHS | route_to NC Emergency Management |
| keyword rule | "school bus" in title | DPI |
| no match | other | uses card.who_applies |
| deadline from listing | close_date 2026-12-01 | draft.deadline == 2026-12-01 |
| offline | LLM disabled | message starts with "[offline draft]" |

## 8. Quality assurance
- **Done when:** all tests pass; 3 live drafts read naturally and contain no invented facts.
- **Metrics:** % of routed items with a lookup match (vs fallback).
- **GenAI log:** any wrong owner the AI suggested.
