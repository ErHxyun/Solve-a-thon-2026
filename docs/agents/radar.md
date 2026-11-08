# Grant Topic Radar — Spec

| | |
|---|---|
| Owner | Front end (page, area rules) + Lead (pipeline wiring) |
| Code | `agents/radar.py`; graph node `radar` (after `publish`); page template `radar/radar_template.html`; `GET /radar`, `GET /api/radar` |
| Type | Code (no LLM) |
| Origin | `radar/build_radar.py` (one-off snapshot from the team workbook) — now rebuilt daily from Grants.gov |

## 1. Description
Shows where federal grant money is **growing or fading**, by policy area, and how well each area fits DNCR's plan. Answers "what is coming next year in our areas?" — the forward-looking view the daily short list does not give.

## 2. Input
| Source | What |
|---|---|
| Grants.gov `search2` | every posted + forecasted listing (all applicants), and listings **closed in the last 365 days** (`sortBy=closeDate|desc`, paging stops at the cutoff); the same two pulls with applicant types 00/25/99 to mark "open to states" |
| Fit Scorer | `plan_match`, `research_penalty`, agency-type floor — on **titles only**, so closed and coming listings are scored the same way |
| Gatekeeper | `OTHER_REGION` / `NC_OK` (title-based region check for "open to states") |

About 16 search calls, ~18 s. Offline (fixture) mode uses the Collector's 40 listings and labels growth as indicative.

## 3. Process
1. **Area** = first matching title rule (DNCR areas first), else agency fallback, else Other. 18 areas + Other; 6 are DNCR plan areas.
2. **Bucket** = Closed (deadline passed) · 2026 · 2027 · 2028+ · Forecast (no deadline in the search hit).
3. **Growth (momentum)** = area's share of coming listings ÷ overall coming share. 1.0× = average; shown only for areas with ≥ 3 listings and when closed history exists.
4. **Fit** = mean plan match A of the area's listings; **strong** = count with A ≥ 35.
5. **Keyword map** = title words/bigrams (stop words removed) with their own growth and main area.

## 4. Output
`radar/latest.json`: `{as_of, source, window_days, generated_at, all: {...}, eligible: {...}}`; each scope has `n, closed, coming, overall_coming, buckets, areas[], words, words_dncr, words_other` — the same shape as `build_radar.py`, so the page template plugs in unchanged.

## 5. Validation
- Never breaks the pipeline: any error is printed and the previous radar stays (`{"radar": {"status": "error"}}`).
- Titles are HTML-unescaped from the API and HTML-escaped when injected into the page; `</` is escaped in the data block.
- Tests: `tests/test_radar.py` (area rules, buckets, growth arithmetic, no growth claim without history, region check, page served after a pipeline run, failure isolation).

## 6. Limits
- "Recently closed" is a 12-month window (`RADAR_CLOSED_DAYS`); growth compares that window with everything open or forecast now, so long-running recurring notices (e.g. NIH parent announcements) inflate "coming" for research areas.
- Areas come from title phrases, not the full synopsis.
