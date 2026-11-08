# Web API — contract for the front end (`index.html`)

Backend: `app/api.py` (FastAPI). It serves `index.html` at `/` and JSON under `/api`. The daily job writes the data; the API reads it, records decisions, and runs the two on-click agents. **Nothing is ever submitted or sent.**

Interactive docs (try every endpoint in the browser): **`/docs`**

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
python pipeline.py                         # writes today's results into ./data
uvicorn app.api:app --reload --port 8000   # open http://localhost:8000 and http://localhost:8000/docs
```

Opening `index.html` with VS Code Live Server (port 5500) also works: CORS allows `localhost:5500`. Call the API with the full URL `http://localhost:8000/api/...`. When the page is served by the API itself, use relative URLs (`/api/...`).

## Endpoints

| Method | Path | What it returns / does |
|---|---|---|
| GET | `/api/shortlist` | The morning short list + last-refreshed time + alert digest. Query: `limit` (15), `include_skipped`, `include_low`, `hide_decided_skip` (true) |
| GET | `/api/opportunities/{opp_id}` | Everything about one item: listing, score components, rationale, card, verification, decision history |
| POST | `/api/decisions` | Record a decision. Body: `{"opp_id": "...", "decision": "Chase" \| "Waitlist" \| "Skip" \| "Route", "note": ""}` → 201 |
| DELETE | `/api/decisions/{opp_id}` | Undo the latest decision for that item (e.g. accidental Skip) |
| GET | `/api/decisions` | Full decision log |
| POST | `/api/opportunities/{opp_id}/route` | Route Agent draft: `{route_to, reason, deadline, message}` — show it in an editable box with a Copy button |
| POST | `/api/opportunities/{opp_id}/apply-assist` | Starter pack after Chase: `{requirements[], sf424_fields_to_gather[], outline[], timeline[], email_to_division_lead, open_questions[]}` |
| POST | `/api/opportunities/{opp_id}/card` | Write + verify a decision card for an item outside the morning top 15 (one LLM call, retried once with Verifier feedback, cached in `cards/on_demand/`). Returns the checked card |
| GET | `/api/tracker` | The user's application records |
| PUT | `/api/tracker/{opp_id}` | Partial update: `stage` (saved · reviewing · preparing · internal_approval · submitted · awarded · declined · closed), `owner`, `next_action`, `next_action_due` (YYYY-MM-DD), `notes`, `my_decision` (pursue · monitor · decline · undecided), `eligibility` (verified · ineligible · pending). Every change is appended to `history`; stage saved → decision Waitlist, active stages → Chase |
| GET | `/api/email/preview?kind=weekly\|urgent` · `/api/email/status` | Notifier email preview (HTML) and last status |
| GET | `/api/alerts` | `{alerts: [...], digest: {...}}` |
| GET | `/api/run` | Last pipeline run: `finished_at`, `status`, `counts` (funnel) |
| GET | `/api/health` | `{ok, storage, llm_enabled}` |

Errors: 404 unknown item · 409 no card for this item (not on the short list) · 422 bad body.

## Real sample responses

Generated from the offline pipeline (40-record fixture): [`api-samples/`](api-samples/)

- `shortlist.json` — `GET /api/shortlist`
- `opportunity.json` — `GET /api/opportunities/361480`
- `route.json`, `apply-assist.json` — the two POST drafts

Build the page against these files first; switch to the live API when it runs.

## Short-list item (what each card shows)

| Field | Show it as |
|---|---|
| `title`, `funder`, `url` | heading, subtitle, "Open listing" link |
| `days_left`, `close_date` | "Closes in 24 days (2026-12-01)"; red when ≤ 14; "rolling / not stated" when null |
| `suggested_decision`, `decision_reason` | a hint chip next to the buttons — the person decides |
| `confidence` | High / Medium / Low badge (after verification) |
| `objective.id`, `objective.text`, `objective.page`, `division` | "Objective 1.5, plan p. 7 · DNCR museums / education" |
| `route_to` | "Who applies" |
| `card.can_dncr_apply`, `card.who_applies`, `card.money_type`, `card.prerequisites`, `card.worth_it` | the five questions |
| `card.summary`, `card.why_fit`, `card.why_not_fit` | plain-English text; always show `why_not_fit` |
| `card.evidence[]` | expandable quotes with source "listing" / "plan" |
| `verification.warnings[]` | yellow warnings |
| `already_yours`, `watch`, `flags[]` | small tags |
| `my_decision` | the user's action: Chase = Tracking, Waitlist = Saved, Skip = Dismissed, Route = Forwarded. Never overwrites `suggested_decision` |
| `number`, `plan_match` | search key; Plan match tile (Fit Scorer A, 0–100 — not the Final score) |
| `money_category`, `tags` | one of Formula / State-only competition / Open competition / Unknown (from the scorer's chance case); `One-time` tag |
| `funding_mechanism`, `competition_scope` | the same facts split in two: Formula / Competitive / Unknown, and State-only / Open to multiple applicant types / Unknown |
| `eligibility` | verified (a person marked it) · pending · ineligible · unknown (no card) |
| `factors` | A, S, K, P, V, U, W, H, H10, H90, award, P_case — for the score breakdown |
| `tracker`, `card_on_demand` | the user's application record (or null); card was written on click |

Plain English on the card; numbers (score components) only in the detail view.

## Minimal JavaScript

```js
const api = (path, opts) => fetch(path, opts).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });

const data = await api('/api/shortlist');
render(data.items);  // your card renderer
document.querySelector('#refreshed').textContent = `Last refreshed: ${data.last_refreshed}`;

async function decide(oppId, decision, note = '') {
  await api('/api/decisions', {method: 'POST', headers: {'Content-Type': 'application/json'},
                               body: JSON.stringify({opp_id: oppId, decision, note})});
  if (decision === 'Route') showDraft(await api(`/api/opportunities/${oppId}/route`, {method: 'POST'}));
  if (decision === 'Chase') showStarterPack(await api(`/api/opportunities/${oppId}/apply-assist`, {method: 'POST'}));
}
const undo = oppId => api(`/api/decisions/${oppId}`, {method: 'DELETE'});
```

## Deploy

Same image as the job. `MODE=app` (default) starts `uvicorn app.api:app` on port 8000; `MODE=job` runs the pipeline; `MODE=streamlit` is the old fallback page. No changes to the Azure commands in `docs/azure-setup.md`.
