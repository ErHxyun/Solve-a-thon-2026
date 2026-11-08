# One-year workflow simulation: full method

Code: `twoperson2.py` · Output: `outputs/two_person_simulation.xlsx` · Data: 11/7/2026 Grants.gov export

## 1. Summary

In one simulated year, a grants officer using our tool expects about **$1.59M** in awards versus **$0.59M**
working the current way (**2.7x**), mainly because she spends **~39 hours** finding and screening grants
instead of **~296**, and writes **~6.5** applications instead of **~3.6**. The tool comes out ahead in
**87%** of 300 simulated years; the most conservative variant still gives **2.1x**.

| Per year (base case, mean of 300 years) | With tool (A) | Current way (B) | Difference |
| --- | --- | --- | --- |
| Hours finding and screening grants | 39 | 296 | 257 hours saved |
| Hours left for writing | 481 | 224 | 2.1x more |
| Applications submitted | 6.5 | 3.6 | +2.9 |
| Expected grants won | 1.71 | 0.97 | +0.74 |
| Expected funding | $1.59M | $0.59M | 2.7x |
| Truly fitting grants never seen | 0 | 7.3 of 39.1 | 7 fewer missed |
| Years where A wins more money | 87% | | |

These are expected values from a model built on our own assumptions and on the 11/7 grants only; the
dollars lean optimistic (section 11). The ratio and the hours are the robust part.

## 2. How this differs from the ranking test

| | Ranking test (slide 7) | One-year workflow test (slide 11) |
| --- | --- | --- |
| Question | Which of 2,556 weightings orders grants best? | What does the whole tool change over a year? |
| What differs between the two sides | Only the order she applies in | The whole way of working |
| Grants | The 15 fitting grants open on 11/7 | 11/7 grants resampled into a year, ~52 fitting |
| Time | No posting dates; deadlines drawn 8-52 weeks out | Posting dates over the year, real window lengths |
| Cost of finding grants | Weekly browsing hours only | Browsing + triage + reading each notice |
| Tool features | Ranking only | Ranking, alerts, short cards, starter pack |
| Approval buffer | None | 2 weeks before each deadline |
| Tool / current way | $839K / $340K (2.4x) | $1.59M / $0.59M (2.7x) |

The ranking test is a fair race to pick weights: same grants and rules for all 2,556 options, so only the
order changes. The one-year test offers about three times as many opportunities, charges for reading time
and adds the tool's features, so its dollars are higher. Compare within a column, or compare ratios.
($853K is the ranking-test score from the weight search itself, on its own random draws; quote $839K.)

## 3. Input data: the grant pool

| Step | Grants left | Why |
| --- | --- | --- |
| All listings in the 11/7 file | 1,459 | Raw export |
| Pass the gates (state eligible, open to NC, still open), deduplicated | ~1,030 | Ineligible grants cannot be pursued |
| Exclude the 20 past DNCR wins | | Kept for the past-wins test only |
| Keep grants DNCR applies for itself (drop Route items) | **146** | Routed grants belong to other agencies |
| Of these, Fit A >= 35 | **15** | The only ones the tool would put in front of her |

Fields per grant (from `dncr_grant_scoring.py`): A (Fit), Final score, P_case (1 formula, 2 state-only,
3 open competition), expected_number_of_awards, award_typ (sqrt(floor x ceiling) or ceiling / 3, cap $3M,
blank if unknown), H (estimated writing hours), post_date, close_date, agency.

All 15 fitting grants are open competitions; 14 of 15 come from funders she already knows (NPS, IMLS,
NEH, NEA, NHPRC). Median writing estimate 79 hours; median window 10.4 weeks (8.7 to 52). Median
typical award across the pool: $333,333.

| Grant (fitting, 11/7) | Funder | Fit A | Final | Typical award | Window (wk) |
| --- | --- | --- | --- | --- | --- |
| ORLP Recurring Notice 5 Year | NPS | 68.8 | 51.6 | $2,121,320 | 52.0 |
| Public Humanities Projects | NEH | 68.4 | 51.2 | $666,667 | 8.7 |
| Collections Stewardship | NEH | 89.2 | 51.1 | $158,114 | 39.9 |
| National Digital Newspaper Program | NEH | 71.0 | 39.2 | $108,333 | 14.1 |
| Landmarks of History and Culture | NEH | 56.7 | 32.4 | $153,333 | 11.9 |
| NSF STEM K-12 | NSF | 52.2 | 31.9 | $333,333 | 52.0 |
| National Leadership Grants for Museums | IMLS | 53.7 | 31.3 | $193,649 | 10.4 |
| Museums Empowered | IMLS | 60.5 | 30.4 | $41,833 | 10.4 |
| Inspire Grants for Small Museums | IMLS | 60.7 | 28.1 | $19,365 | 10.4 |
| National Leadership Grants for Libraries | IMLS | 48.1 | 27.5 | $158,114 | 10.4 |
| Laura Bush 21st Century Librarian | IMLS | 40.5 | 24.4 | $273,861 | 10.4 |
| NEA Domestic Indemnity | NEA | 39.4 | 21.8 | $31,623 | 49.4 |
| NEA International Indemnity | NEA | 39.3 | 21.7 | $31,623 | 36.4 |
| Museum Grants for American Latino History | IMLS | 41.6 | 21.3 | $50,000 | 10.4 |
| 21st Century Museum Professionals | IMLS | 35.9 | 21.2 | $223,607 | 10.4 |

(The live website may show slightly different scores, e.g. Collections Stewardship Fit 91, score 52.)

## 4. Building one year of opportunities

1. **How many postings.** The Notifier spec counts ~26 state-eligible listings a week, ~1 fitting DNCR,
   so ~52 fitting a year. 15 of 146 fit (10.3%): N_year = round(52 / (15/146)) = round(506.1) = **506**.
2. **Which grants.** 506 draws with replacement from the 146. Each fitting grant appears ~3.5 times a
   year, each copy with its own posting date and truth. Resampling adds volume, not new kinds of grants.
3. **Posting week** t0 ~ Uniform(0, 52) (most programs recur yearly).
4. **Window** w = (close_date - post_date) / 7, clipped to 2-52 weeks; missing dates -> 8. Deadline = t0 + w.

Example: a Public Humanities copy (w = 8.7) posting at week 20.0 closes at 28.7 and must be finished by
26.7 (two-week approval buffer).

## 5. The simulated truth (same draws for both people)

- **True fit** f = clip(A/100 + e, 0, 1), e ~ Normal(0, 0.25). "Truly fits" when f >= 0.35.
- **Base win rate** b: formula -> Beta(19, 1) (mean 0.95); state-only with 0 < awards < 9,999 ->
  clip(Normal(min(1, awards/56), 0.1), 0.02, 1); otherwise Uniform(0.05, 0.45) (mean 0.25; all 15 fitting grants).
- **True win chance** p_win = 0 if f < 0.35, else clip(b x (0.5 + f), 0, 1). Example: b 0.25, f 0.90 -> 0.35.
- **True award**: known -> award_typ x LogNormal(0, 0.7), cap $3M; unknown -> LogNormal(ln 333,333, 0.9), cap $3M.
  (Multiplier median 1; about two thirds of draws between 0.5x and 2x.)
- **True writing hours** = H x LogNormal(0, 0.45) (a 79-hour estimate mostly lands at 50-124 hours).
- **Value of an application** = p_win x award (expected dollars, not a random win).

Example: Collections Stewardship copy, e = +0.03 -> f = 0.92; b = 0.30 -> p_win = 0.30 x 1.42 = 0.43;
award multiplier 1.2 -> $189,737; hours 0.9 x 79 = 71. Expected value = 0.43 x $189,737 ~ $81,600.

## 6. Person A: uses the tool

| Feature (repo) | How we model it | Parameter |
| --- | --- | --- |
| Collector + Gatekeeper + Fit Scorer | Considers only A >= 35, ordered by Final score | Skip line 35; weights 0.30/0.20/0.30/0.15/0.05 |
| Notifier weekly digest | Learns of a grant 0-1 week after posting | Uniform(0, 1) week |
| Notifier urgent email | Same day if the window <= 45 days (6.43 weeks) | 45 days |
| Today page review | Fixed review time | 0.5 h/week = 26 h/year |
| Card Agent + Verifier | Card replaces the first read of the notice | 0.25 h per fitting grant |
| Apply Assist starter pack | Cuts writing time | -10% (assumption; 0% tested) |
| Tracker + closing-soon alerts | Never forgets a started application | none |

- Finding hours = 52 x 0.5 + 0.25 x N_fit = 26 + 0.25 x 51.9 = **39.0**
- Writing budget = 520 - 39.0 = **481 h** = 9.25 h/week; average submitted application 75.5 h after the cut
- A trusts our Fit score, so ~0.95 of her 6.5 applications a year go to grants that do not truly fit (win chance 0)
- No fitting grant has a window under 6.43 weeks, so the same-day email never fires here (and the repo now
  sends the weekly digest only by default)

## 7. Person B: the current way

| Behavior | How we model it | Parameter |
| --- | --- | --- |
| Browses email lists and funder sites | Fixed weekly time | 2 h/week = 104 h/year |
| Finds grants at random | Weekly chance of noticing each open grant | 12% familiar funders, 4% others |
| Triages every noticed grant | Skims title and synopsis | 0.2 h each |
| Reads relevant notices | Full first read | 1.5 h per noticed grant that truly fits |
| Judges fit | Perfect: keeps exactly f >= 0.35 (generous to B) | |
| Chooses what to write | Earliest deadline first | |
| Writing | No starter pack | hours as drawn |

Weeks to notice ~ Geometric(h) (at least 1); never noticed if that week is at or past the deadline.
P(noticed within w weeks) = 1 - (1 - h)^w.

| Funder | h | Average wait | Noticed in a 10.4-week window | In 52 weeks |
| --- | --- | --- | --- | --- |
| Familiar | 12% | 8.3 weeks | 73% | ~100% |
| Other | 4% | 25 weeks | 35% | 88% |

- Finding hours = 52 x 2 + 0.2 x 329.2 + 1.5 x 84.3 = 104 + 65.8 + 126.5 = **296.3**
  (329.2 noticed of 506 posted; 84.3 noticed and truly fitting)
- Writing budget = 520 - 296.3 = **223.7 h** = 4.30 h/week; average submitted application 62.2 h
- She never sees **7.3** of the 39.1 truly fitting grants a year

## 8. The scheduling algorithm (each person)

1. rate = (520 - finding hours) / 52 per week.
2. At clock t, a grant is a candidate if: on her list (A: A >= 35; B: noticed and truly fits), known by t,
   not done, t + hours / rate <= deadline - 2, and hours used + its hours <= budget.
3. No candidate: t += 0.5 and repeat.
4. Pick: A = highest Final score; B = earliest deadline.
5. Write it: t = finish time; add hours; add p_win x award to dollars and p_win to wins; count one application.
6. Repeat until t reaches 52 (an application started before 52 counts even if it finishes slightly after).

Worked example at week 20, same two grants known to both: ORLP copy (104 h, deadline 72) and Public
Humanities copy (79 h, must finish by 26.7).

| | A (9.25 h/wk) | B (4.30 h/wk) |
| --- | --- | --- |
| Order | ORLP (51.6) before Public Humanities (51.2) | Public Humanities first (earlier deadline) |
| Public Humanities finish | 20 + 79 x 0.9 / 9.25 = 27.7 -> too late | 20 + 79 / 4.30 = 38.4 -> too late |
| ORLP finish | 20 + 104 x 0.9 / 9.25 = 30.1 -> fine | 20 + 104 / 4.30 = 44.2 -> fine |
| Writes | ORLP, done week 30.1 (~22 weeks left) | ORLP, done week 44.2 (~8 weeks left) |

## 9. Metrics

Computed per year, averaged over 300 years (seeds 0-299), same seed for both people.

| Metric | Formula |
| --- | --- |
| Hours finding and screening | A: 26 + 0.25 x N_fit; B: 104 + 0.2 x N_noticed + 1.5 x N_noticed&fit |
| Applications | count from the schedule |
| Expected grants won | sum of p_win |
| Expected funding | sum of p_win x award |
| Truly fitting grants never seen | A >= 35 and f >= 0.35, never noticed before closing |
| A beats B | share of years with A's funding > B's |

Money gap decomposition: 1.59M / 0.59M = (6.49 / 3.56) x ($245K / $166K) ~ 1.82 x 1.48 ~ 2.7.
A writes 1.8x as many applications, each worth 1.5x more because she starts with large grants (applied
awards average $954K vs $664K). Win chance per application is similar: 0.26 (A) vs 0.27 (B).

## 10. Results across scenarios

| Scenario | Change | A | B | Ratio | A wins | A apps | B apps | B hours | B never sees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Base | sections 6-7 | $1.59M | $0.59M | 2.7x | 87% | 6.5 | 3.6 | 296 | 7.3 |
| B reads faster | first read 0.75 h | $1.59M | $0.71M | 2.2x | 82% | 6.5 | 4.3 | 233 | 7.3 |
| **Most conservative** | faster read + starter saves 0% | $1.46M | $0.71M | **2.1x** | 79% | 5.9 | 4.3 | 233 | 7.3 |
| No starter saving | starter saves 0% | $1.46M | $0.59M | 2.5x | 85% | 5.9 | 3.6 | 296 | 7.3 |
| B notices more | 24% / 8% weekly, 1 h/week browsing | $1.59M | $0.58M | 2.7x | 86% | 6.5 | 3.8 | 289 | 1.6 |
| B notices less | 6% / 2% weekly, 3 h/week browsing | $1.59M | $0.60M | 2.7x | 88% | 6.5 | 3.4 | 292 | 15.4 |

- Reading time is the biggest lever for B ($0.59M -> $0.71M when halved).
- Noticing more does not help B: misses fall from 7.3 to 1.6 but extra reading keeps funding flat.
- The starter pack is worth ~0.6 applications and ~$0.13M a year.

## 11. Limitations and leakage

| Issue | Effect | How to answer |
| --- | --- | --- |
| Same 11/7 grants used to tune weights and to build the year (leakage) | Favors A | Say it; fix = rerun on the unseen 8/18 snapshot (not yet run) |
| Only 15 fitting grants, repeated ~3.5x | Leans on a few large grants (ORLP $2.1M) | Fewer big grants would shrink the ratio |
| Truth built from our own scores (circularity) | Favors A | Partly offset: B judges true fit perfectly |
| B's parameters are assumptions | Either way | Varied in section 10; minimum still 2.1x |
| Starter-pack saving assumed | Favors A | 0% scenario: 2.5x (2.1x with faster B) |
| Same-day alert never fires | Understates A slightly | Untested benefit |
| Expected values; competitive win rates guessed (5-45%) | Either way | Pilot replaces guesses with real outcomes |
| Routing not counted | Understates A | Extra benefit |

Quote the ratio range (2.1-2.7x) and the hours saved; label dollars as simulation estimates from the 11/7 pool.

## 12. Talk track and Q&A

> "Same officer, same 10 hours a week, one simulated year. With the tool she spends about 40 hours finding
> grants instead of about 300, so she writes about six applications instead of under four, and starts with
> the large ones. That is roughly 2 to 2.7 times the expected funding, and in the most conservative version
> still about 2 times."

| Question | Answer |
| --- | --- |
| Why $1.59M here and $839K on slide 7? | Different tests: slide 7 picks weights on one day's grants; this runs a full year with the whole tool, so more grants to choose from. Both land around 2-3x. |
| Only three more applications, 2.7x the money? | 1.8x as many applications times 1.5x more per application (she starts with the large grants); win rate per application is about the same. |
| Isn't this leakage? | Partly, yes: the year is built from the grants the weights were tuned on, and the truth uses our scores. So we quote the ratio, and the pilot replaces assumptions with real outcomes. |
| Where do 2 h/week and 1.5 h per notice come from? | Assumptions from the Discovery session; halving reading time still leaves the tool 2.2x ahead. |
| What if she just looked harder? | Noticing twice as many grants cuts misses but adds reading time; her funding stays flat. The win comes from screening for her. |
| Does the tool ever do worse? | In about 13% of simulated years; with one or two expected wins a year, chance sometimes favors B's picks. |

## 13. Prompts

**Short prompt**

```text
Now simulate a full year of work for two grants officers with the same 10 hours a week.

Build a year of opportunities by spreading grants like today's across the year, each posting on a random date with its real application window. Use the same random truth for both people (win chance, award, hours).

Person A uses our tool: it reads every new listing daily, sends a weekly email (same day if the window is short), shows a short card instead of the full notice, ranks by our score, and gives a starter pack when she decides to apply.

Person B works as today: notices grants at random while browsing, spends time reading each one to judge whether it fits, and works on whatever is due soonest.

Both need to finish two weeks before the deadline for internal approval. Tell me, for each person, hours spent finding and reading grants, applications submitted, expected grants won, expected dollars, and good grants never seen. Also show how much the result changes if reading is faster or the starter pack saves no time.
```

**Full specification** (reproduces the numbers above)

```text
INPUT: the scored 11/7/2026 Grants.gov export. Keep grants that pass the eligibility gates, are not one of the 20 past DNCR wins, and that DNCR applies for itself (146). For each: Fit A (0-100), Final score, P_case (1 formula / 2 state-only / 3 open), expected_number_of_awards, typical award (sqrt(floor x ceiling) or ceiling/3, cap $3M, blank if unknown), estimated hours H, post_date, close_date, agency.

YEAR: N = round(52 / share of the 146 with A >= 35) = 506 grants drawn with replacement. Posting week t0 ~ Uniform(0, 52). Window w = (close - post)/7 weeks, clipped 2-52, missing -> 8. Deadline = t0 + w.

TRUTH (same draws for both people):
- f = clip(A/100 + Normal(0, 0.25), 0, 1); truly fits if f >= 0.35
- base: P_case 1 -> Beta(19,1); P_case 2 with 0 < awards < 9999 -> clip(Normal(min(1, awards/56), 0.1), 0.02, 1); else Uniform(0.05, 0.45)
- p_win = 0 if f < 0.35 else clip(base x (0.5 + f), 0, 1)
- award = known ? min(3M, typical x LogNormal(0, 0.7)) : min(3M, LogNormal(ln 333,333, 0.9))
- hours = H x LogNormal(0, 0.45)

PERSON A (tool): considers grants with A >= 35. Knows a grant at t0 if w <= 45/7 weeks, else t0 + Uniform(0, 1). Finding hours = 52 x 0.5 + 0.25 x (number with A >= 35). Writing hours = hours x 0.9. Priority = highest Final score.

PERSON B (current way): weekly chance of noticing an open grant = 0.12 for NPS, IMLS, NEH, NEA, NHPRC, else 0.04; notice week = t0 + Geometric(chance); never noticed if that is at or after the deadline. Finding hours = 52 x 2 + 0.2 x (noticed) + 1.5 x (noticed and f >= 0.35). Keeps only noticed grants with f >= 0.35. Writing hours = hours. Priority = earliest deadline.

SCHEDULE (each person): budget = 520 - finding hours; rate = budget / 52 per week. Clock t = 0. While t < 52: candidates = on her list, known by t, not done, t + hours/rate <= deadline - 2, used + hours <= budget. None -> t += 0.5. Else take the top priority, t = finish, used += hours, add p_win x award and p_win, count one application.

OUTPUT: average over 300 seeds (0-299) of finding hours, applications, expected wins, expected dollars, truly fitting grants (A >= 35 and f >= 0.35) never noticed by B, and share of seeds where A's dollars > B's.

SCENARIOS: base; B reading 0.75 h; B reading 0.75 h + starter saving 0; starter saving 0; B noticing 0.24/0.08 with 1 h/week; B noticing 0.06/0.02 with 3 h/week.
```

## 14. Reproduce

```bash
cd analysis            # with data/grants-search-202611071427.csv in place
python twoperson2.py   # prints the scenario table, writes outputs/two_person_simulation.xlsx (~5-10 min)
```

Change assumptions in `P0` / `SCN` near the bottom of `twoperson2.py`: `haz_fam`, `haz_oth` (B's weekly
noticing chance), `search_h` (B's browsing h/week), `deep_h` (B's first-read hours), `starter_cut`
(A's writing-time saving), `approval_wk` (buffer before the deadline).
