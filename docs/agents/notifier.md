# Notifier — Spec (weekly digest + same-day urgent email)

| | |
|---|---|
| Owner | LLM-A |
| Branch | `agent/email-digest` |
| Code | `agents/notifier.py`; graph node `notifier` (after `alert`, before `publish`); API `GET /api/email/preview`, `GET /api/email/status` |
| Type | Code (no LLM) |
| Runs | Daily batch; sends at most one email per run |
| Status | Done: rules, email design, ACS + SMTP delivery, previews, 13 tests. Needs the email settings below on the Azure job |

## 1. Description
Turns the Alert node's output into **one weekly email plus a same-day email only when something can't wait**. Why weekly: about 26 state-eligible listings are posted per week and only ~1 fits DNCR (Grants.gov export 2026-08-18), so a daily email would be empty most days and get ignored, the way the Grants.gov digest is today. Why also same-day: some windows are ~30 days and a week's delay can sink an application (Discovery: the school-bus case). The pipeline still runs every morning; running daily ≠ emailing daily. Serves **R1** (no missed windows) and goal 2 ("hear about it early enough to act").

## 2. Input
| Source | Field |
|---|---|
| state | `opportunities`, `top` (short list), `checked` (verified cards: why it fits, money type, confidence), `alerts` |
| storage | `decisions/dncr.json` (latest decision wins), `opportunities/seen_history.json` (first-seen dates), `notify/sent.json` |
| env | settings below |

## 3. Process
1. **Urgent triggers** (each item emailed once per trigger, Skip silences):
   - Waitlisted forecast is now open (`waitlist_opening`)
   - New short-list item whose window is ≤ `EMAIL_URGENT_NEW_DAYS` (45)
   - Item the user is **chasing** closes within `EMAIL_URGENT_CLOSING_DAYS` (7)
2. **Which email**: weekly day (`EMAIL_WEEKLY_DAY`, Monday ET) and not yet sent this ISO week → **weekly** (with an "Act now" section, so never two emails on Monday); else fresh urgent triggers → **urgent**; else nothing.
3. **Weekly sections**: Act now · New this week (first seen in the last 7 days; nothing on day one) · Deadlines in the next 30 days (short list + chased; not Skip/Waitlist) · On your waitlist · Still worth a look (top 5). Every section has an empty-state sentence, so the weekly email also proves the system is alive.
4. **Render** HTML (table layout, inline styles; the web app's colors, fonts, pill buttons and card layout) + a plain-text part. Every card links to `APP_URL/#item-<id>`; the header button to `#today`.
5. **Send** with `EMAIL_MODE`; record the result in `notify/latest.json`; mark sent keys only after a successful send (a failed send retries next run).
6. Every run also writes `notify/preview/weekly.json` (and `urgent.json` when there are triggers) for the demo: open `/api/email/preview` or `/api/email/preview?kind=urgent`.

## 4. Procedure
- ✅ Rules, de-duplication, Monday merge, previews, ACS + SMTP, API preview/status, CLI
- ☐ Set the email settings on `job-ffm-refresh` (below) and run `python -m agents.notifier --test`
- ☐ Ask the client: weekly day/time, recipients, is "weekly + urgent" right?
- ☐ Pilot: custom sender domain instead of the free azurecomm.net subdomain (deliverability)

## 5. Validation
- Never breaks the pipeline: any error is written to `notify/latest.json` (`status: error`) and the run continues.
- No secrets in outputs: status shows the recipient **count**, never addresses, keys or passwords (tested).
- Reruns the same day send nothing new (tested). Titles and text are HTML-escaped (tested).

## 6. Output
`state["notification"]` and `notify/latest.json`: `{date, kind: weekly|urgent|null, mode, recipients, urgent_items, subject, status, provider|error}`.

## 7. Settings and setup
| Variable | Default | Notes |
|---|---|---|
| `EMAIL_MODE` | `preview` | `off` · `preview` (render only) · `acs` · `smtp` |
| `EMAIL_TO` | — | comma-separated |
| `EMAIL_FROM` | — | ACS: `DoNotReply@<id>.azurecomm.net`; SMTP: the account address |
| `APP_URL` | `http://localhost:8000` | set to the web app URL in Azure |
| `EMAIL_ACS_CONNECTION_STRING` | — | **secret** |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_STARTTLS` | — / 587 / — / — / true | password is a **secret** |
| `EMAIL_WEEKLY_DAY`, `EMAIL_URGENT_NEW_DAYS`, `EMAIL_URGENT_CLOSING_DAYS`, `EMAIL_DEADLINE_WINDOW_DAYS` | mon, 45, 7, 30 | |

**Option A — Azure Communication Services (recommended, stays in our Azure):**
1. (Once) `az provider register --namespace Microsoft.Communication`. Portal → create **Email Communication Services** in `rg-ffm-team` (data location: United States).
2. In it: **Provision domains → Add a free Azure subdomain**. Note the sender `DoNotReply@<id>.azurecomm.net`.
3. Create **Communication Services** (same resource group and data location).
4. In Communication Services: **Email → Domains → Connect domain** → pick the subdomain from step 2.
5. Communication Services → **Keys** → copy the **Connection string**.
6. Only the daily job sends (the web app does not need the key):
```bash
az containerapp job secret set -n job-ffm-refresh -g rg-ffm-team --secrets acs-email="<connection string>"
az containerapp job update -n job-ffm-refresh -g rg-ffm-team --set-env-vars \
  EMAIL_MODE=acs EMAIL_ACS_CONNECTION_STRING=secretref:acs-email \
  EMAIL_FROM="DoNotReply@<id>.azurecomm.net" EMAIL_TO="<recipient>" APP_URL="https://<web app url>"
```
7. Locally: put the same values in `.env`, then `python -m agents.notifier --test`, and `python -m agents.notifier --preview weekly --send` to send the real digest once.

The free subdomain has low sending limits and may land in spam at first: fine for the demo; use a verified custom domain for a pilot.

**Option B — SMTP (fallback if ACS can't be created on the subscription):** a Gmail account with 2-step verification and an **app password**: `EMAIL_MODE=smtp SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=<gmail> SMTP_PASSWORD=<app password> EMAIL_FROM=<gmail>`. University Microsoft 365 accounts usually block SMTP sign-in.

## 8. Testing module
`tests/test_notifier.py`: urgent triggers; Skip silences; weekly once per week and merged with urgent on Monday; quiet weekday sends nothing; "new this week" needs a baseline; rendering uses the site's tokens, escapes HTML, links to the app, shows far dates instead of counting; weekly email never empty; preview mode never sends; SMTP sends once then stays quiet and leaks no password; ACS message shape; failures recorded not raised; mode off; API preview/status.
