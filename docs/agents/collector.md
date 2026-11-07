# Collector — Spec

| | |
|---|---|
| Owner | lead |
| Branch | `agent/collector` |
| Code | `agents/collector.py` |
| Type | Code (no LLM) |
| Runs | Daily batch (first node) |
| Status | Full live pull done (state-eligible filter, paging, parallel fetch, cache, keyword baseline) |

## 1. Description
Reads the federal feed so the grants developer doesn't have to. Pulls posted and forecasted opportunities from Grants.gov, fetches each record's details (eligibility, description, award size, dates), normalizes them into `Opportunity` records, and de-duplicates. Replaces "open the Grants.gov email digest every morning." Serves **R1** (runs daily) and **R4** (a broad pull, not only keyword hits).

## 2. Input
| Source | Field / file | Notes |
|---|---|---|
| Grants.gov `POST /v1/api/search2` | `oppHits[]`: id, number, title, agencyCode, openDate, closeDate, oppStatus | No key. Search returns **no** eligibility or description |
| Grants.gov `POST /v1/api/fetchOpportunity` | `data.synopsis` (posted) or `data.forecast` (forecasted) | applicantTypes, applicantEligibilityDesc, synopsisDesc / forecastDesc, award floor/ceiling, costSharing, responseDateStr / estApplicationResponseDateStr, cfdas |
| Config | `COLLECTOR_MODE` (`fixture` / `live`) | Fixture = `tests/fixtures/grants_sample_raw.json` (40 real records, 2026-11-07) |

## 3. Process
1. Search with several keyword sets **plus** a broad no-keyword pull (paged).
2. Collect unique ids; fetch details for each (skip and log failures).
3. Save the untouched raw details to `raw/{date}/grants.json` (audit and replay).
4. Normalize to `Opportunity`: strip HTML, unescape titles, ISO dates, compute `days_to_close`.
5. De-duplicate by opportunity number; flag records missing title or description as `low_quality`.
6. Write `opportunities/latest.json`.

## 4. Procedure
- ✅ `search()`, `fetch_detail()`, `normalize()`, fixture mode
- ✅ Full pull: `search_all()` pages 250 at a time through every posted/forecasted listing with `eligibilities=00|25|99` (state governments, others, unrestricted), up to `COLLECTOR_CAP` (5,000); de-dups hits across pages
- ✅ Parallel detail fetch (`COLLECTOR_WORKERS`, default 8) with retry + backoff on timeouts / 429 / 5xx; one failed record never stops the run; zero records fails the run
- ✅ Detail cache in storage `cache/grants/{id}.json`, reused for `COLLECTOR_CACHE_HOURS` (20) — a same-day rerun is fast
- ✅ Keyword baseline: `baseline/{date}/keyword_hits.json` (what typing plan keywords into Grants.gov returns) for the "missed by keyword search" metric
- ✅ Counts in `runs/collector_latest.json`: searched, listed, cached, fetched, failed, normalized, low_quality, seconds
- ☐ Use `lastUpdatedDate` to refetch only changed records instead of a time window

## 5. Validation
- Every normalized record must validate as `Opportunity` (Pydantic). Invalid → log id + error, skip, count as failed.
- Dates come only from the API string fields; unparseable → `None` (= "not stated"), never guessed.
- If a fetch fails mid-run, keep going. Fail the whole run only if **zero** records are collected.

## 6. Output
| State key / file | Shape |
|---|---|
| `state["opportunities"]` | `list[Opportunity]` (as dicts) |
| `raw/{yyyy-mm-dd}/grants.json` | raw `fetchOpportunity.data` list (live mode only) |
| `opportunities/latest.json` | same as state |

## 7. Testing module
`tests/test_collector.py` (create it)

| Test | Input | Expected |
|---|---|---|
| normalize posted | fixture record 362715 (ORLP) | status posted, `00` in applicant types, close date 2030-11-01 |
| normalize forecast | fixture record 322314 | status forecasted, description from `forecastDesc` |
| HTML stripped | any record | no `<p>` or `&amp;` in title/description |
| de-dup | two records, same number | one output |
| bad record | record missing `synopsis` and `forecast` | skipped, no crash |

## 8. Quality assurance
- **Done when:** a live run stores ≥ 1,000 records (posted + forecasted) with a run-log entry, in under 10 minutes.
- **Metrics:** records fetched / failed; % low_quality; run time.
- **Known issues:** forecasts with past estimated dates (negative `days_to_close`) are stale; Gatekeeper drops them. Some records have no close date (rolling).
- **GenAI log:** any API field the AI guessed wrong (we found that search2 has no eligibility; fetchOpportunity does).
