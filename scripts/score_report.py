"""Excel report of the Fit Scorer ranking (the team's "DNCR_Top50_Grants" workbook), built from the pipeline.
Owner: Criteria.  Spec: docs/agents/fit_scorer.md

Usage:
    python scripts/score_report.py                      # committed fixture (offline)
    python scripts/score_report.py --live --top 50      # live Grants.gov feed (needs network)
Writes data/reports/DNCR_Top<N>_Grants_<date>.xlsx with sheets: Top N, How scored, Funnel, DNCR objectives.
Collector / Gatekeeper come from this branch if present, otherwise the graph's stand-ins.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from agents import fit_scorer as fs  # noqa: E402
from core import config, storage  # noqa: E402
from core.reference import load_profile  # noqa: E402
from core.schemas import Opportunity  # noqa: E402
from graphs.batch_graph import collector, gatekeeper  # noqa: E402

COLS = [("rank", "Rank", 6), ("number", "Opportunity #", 18), ("title", "Title", 48), ("agency", "Agency", 26),
        ("deadline", "Deadline", 12), ("final", "Final score", 9), ("A", "Plan match A", 10), ("S", "Meaning S", 9),
        ("K", "Keywords K", 9), ("objective_id", "Objective", 9), ("second_objective_id", "2nd objective", 9),
        ("U", "Urgency U (0-3)", 9), ("W", "Workload score W", 10), ("H", "Hours H", 8), ("route_to", "Who applies", 30),
        ("suggested_decision", "Suggested button", 12), ("flags", "Flags", 30), ("rationale", "Rationale", 110),
        ("cfda", "CFDA", 18), ("url", "Link", 40)]
BTN_FILL = {"Chase": "C6EFCE", "You decide": "FFF2CC", "Waitlist": "FCE4D6", "Route": "DDEBF7", "Skip": "EDEDED"}


def build(top_n: int):
    state: dict = {}
    for node in (collector, gatekeeper):
        state.update(node.run(state))
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    passed = {g["opp_id"] for g in state["gates"] if g["passed"]}
    items, cal = fs.score_all(list(opps.values()), passed)
    return opps, state["gates"], items, cal


def row_values(rank: int, s, o: Opportunity) -> dict:
    c = s.components
    return {"rank": rank, "number": o.number, "title": o.title, "agency": o.agency_name or o.agency_code,
            "deadline": o.close_date or "forecast/rolling", "final": s.final, "A": c.get("A"), "S": c.get("S"),
            "K": c.get("K"), "objective_id": s.objective_id, "second_objective_id": s.second_objective_id,
            "U": int(c.get("U", 0)), "W": c.get("W"), "H": round(c.get("H", 0)), "route_to": s.route_to,
            "suggested_decision": s.suggested_decision, "flags": "; ".join(s.flags), "rationale": s.rationale,
            "cfda": ", ".join(o.cfda), "url": o.url}


def write(path: Path, opps, gates, items, cal, top_n: int) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = f"Top {top_n}"
    ws["A1"] = f"DNCR - Top {top_n} grants by Final score (as of {date.today()})"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Only grants that pass all three gates are ranked. Final score = plan match x (0.70 + 0.15 urgency + "
                "0.15 workload). The button is a suggestion; a person decides.")
    hdr = 4
    for j, (_, h, w) in enumerate(COLS, 1):
        cell = ws.cell(hdr, j, h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = w
    for i, s in enumerate(items[:top_n], 1):
        vals = row_values(i, s, opps[s.opp_id])
        for j, (k, _, _) in enumerate(COLS, 1):
            cell = ws.cell(hdr + i, j, vals[k])
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if k == "suggested_decision":
                cell.fill = PatternFill("solid", fgColor=BTN_FILL.get(vals[k], "FFFFFF"))
    ws.freeze_panes = ws.cell(hdr + 1, 4)

    how = wb.create_sheet("How scored")
    lines = [("Step", "Rule used"),
             ("1 Can we apply?", "G1 state eligible (00 / 99, or 'Others' with state text, flagged); G2 not limited to another "
                                 "region or country; G3 open, deadline not passed, forecast not stale. Pass/fail only."),
             ("2a Plan match A", f"A = (2S + K)/3 x research penalty. S = TF-IDF similarity of title + first {fs.SYNOPSIS_CHARS} chars "
                                 f"to the 25 objectives, scaled with L = {cal['L']}, H = {cal['H']}. K = IDF-weighted phrase score, "
                                 f"K = 100 x (1 - exp(-K_raw / {cal['tau']})); each negative word halves K. NIH x0.4; NSF/NASA/BAA x0.75."),
             ("2b Urgency U", "Urgency tier (0-3) of the matched objective; one-time words lift U to 2."),
             ("2c Workload W", f"H = median of a {fs.N_SIMS:,}-run simulation of 11 stages for the title's profile; "
                               f"W = 100 x ({fs.H_HARD} - H) / ({fs.H_HARD} - {fs.H_EASY})."),
             ("3 Final score", f"Final = A x ({fs.WEIGHTS['base']} + {fs.WEIGHTS['U']} x U/3 + {fs.WEIGHTS['W']} x W/100)."),
             ("4 Which button?", f"Skip: A < {fs.A_SKIP}. Route: another office applies. Waitlist: forecast/rolling. "
                                 f"Chase: Final >= {fs.FINAL_CHASE}. Otherwise You decide.")]
    for i, (a, b) in enumerate(lines, 1):
        how.cell(i, 1, a).font = Font(bold=True)
        how.cell(i, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
    how.column_dimensions["A"].width, how.column_dimensions["B"].width = 20, 140

    fun = wb.create_sheet("Funnel")
    n_pass = sum(1 for g in gates if g["passed"])
    a_ok = [s for s in items if s.components.get("A", 0) >= fs.A_SKIP]
    funnel = [("All listings", len(opps)), ("Pass all gates", n_pass), (f"... and A >= {fs.A_SKIP}", len(a_ok)),
              (f"... and Final >= {fs.FINAL_CHASE}", sum(1 for s in a_ok if (s.final or 0) >= fs.FINAL_CHASE))]
    funnel += [(f"Button: {k}", v) for k, v in Counter(s.suggested_decision for s in items).most_common()]
    for i, (a, b) in enumerate([("Stage", "Count"), *funnel], 1):
        fun.cell(i, 1, a)
        fun.cell(i, 2, b)
    fun.column_dimensions["A"].width = 32

    obj = wb.create_sheet("DNCR objectives")
    for j, h in enumerate(["Objective", "Plan page", "Urgency U", "Objective (summary)", "Phrases used"], 1):
        obj.cell(1, j, h).font = Font(bold=True)
    for i, o in enumerate(load_profile().objectives, 2):
        for j, v in enumerate([o.objective_id, o.page, o.urgency_tier, o.text, ", ".join(o.keywords)], 1):
            obj.cell(i, j, v).alignment = Alignment(wrap_text=True, vertical="top")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="pull the live Grants.gov feed")
    ap.add_argument("--top", type=int, default=50)
    a = ap.parse_args()
    if a.live:
        config.COLLECTOR_MODE = "live"
    opps, gates, items, cal = build(a.top)
    out = config.LOCAL_DATA_DIR / "reports" / f"DNCR_Top{a.top}_Grants_{date.today()}.xlsx"
    write(out, opps, gates, items, cal, a.top)
    storage.write_json("scored/calibration.json", cal)
    print(f"calibration: {cal}")
    print(f"wrote {out.relative_to(config.ROOT)} ({min(a.top, len(items))} rows)")


if __name__ == "__main__":
    main()
