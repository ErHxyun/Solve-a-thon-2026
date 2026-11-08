"""Check that agents/fit_scorer.py reproduces the team's rubric script on the same Grants.gov CSV export.

Usage:  python scripts/rubric_parity.py --orig path/to/dncr_grant_scoring.py --csv path/to/grants-search-export.csv
Prints per-factor differences (A, P, V, Final) and the objective agreement for every grant the rubric's gates pass.
Run it whenever either side changes; the port is correct when A/P/V differ by 0 and Final only by rounding (< 0.1).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import fit_scorer as fs  # noqa: E402
from core.schemas import ApplicantType, Opportunity  # noqa: E402

CODE = {"state_governments": "00", "unrestricted": "99", "other": "25"}


def _num(x):
    x = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(x) else float(x)


def rubric_scores(orig, csv: str) -> pd.DataFrame:
    df = orig.load_csv(csv)
    g = df.apply(orig.gates, axis=1, result_type="expand")
    df["gate_pass"] = g[0]
    pm, _ = orig.plan_match(df)
    df = pd.concat([df.reset_index(drop=True), pm], axis=1)
    rows = []
    for _, r in df.iterrows():
        r = r.to_dict()
        r["U"], _ = orig.urgency(r["obj"], r["title"])
        r["anchor"], _ = orig.pick_anchor(r["title"])
        r.update(orig.simulate_hours(r["anchor"]))
        r["W"] = orig.workload_score(r["H"])
        r["P"], _, _ = orig.win_probability(r)
        r["award"], _ = orig.typical_award(r)
        r["V"] = orig.value_score(r["award"], None) if r["award"] and pd.notna(r["award"]) else orig.V_UNKNOWN
        r["final"] = orig.final_score(r["A"], r["P"], r["V"], r["U"], r["W"])
        rows.append(r)
    return pd.DataFrame(rows)


def port_scores(orig, csv: str, as_of: date) -> dict:
    raw = pd.read_csv(csv, low_memory=False)
    opps = []
    for i, r in raw.iterrows():
        types = [t.strip() for t in str(r.get("applicant_types") or "").split(";") if t.strip() and t.strip() != "nan"]
        n = _num(r.get("expected_number_of_awards"))
        opps.append(Opportunity(
            id=str(i), number=str(r["opportunity_number"]), title=orig.norm(r["opportunity_title"]),
            agency_name=orig.norm(r.get("agency_name")), agency_code=str(r.get("agency_code") or ""),
            description=orig._clean_html(r.get("summary_description")), award_floor=_num(r.get("award_floor")),
            award_ceiling=_num(r.get("award_ceiling")), expected_awards=int(n) if n and 0 < n < 9999 else None,
            applicant_types=[ApplicantType(id=CODE.get(t, "x-" + t), description=t) for t in types]))
    # score the whole export so both sides calibrate S and K on the same population
    items, cal = fs.score_all(opps, {o.id for o in opps}, today=as_of)
    print("port calibration", cal)
    return {s.opp_id: s for s in items}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True, help="the team's dncr_grant_scoring.py")
    ap.add_argument("--csv", required=True, help="Grants.gov search export (.csv)")
    ap.add_argument("--as-of", default=None, help="run date for the gates, yyyy-mm-dd (default: today)")
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("rubric", args.orig)
    orig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(orig)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    orig.AS_OF_DATE = as_of

    ref, port = rubric_scores(orig, args.csv), port_scores(orig, args.csv, as_of)
    out = []
    for i, r in ref.iterrows():
        s = port.get(str(i))
        if s is None or not r["gate_pass"]:
            continue
        c = s.components
        out.append({"title": r["title"][:50], "same_objective": s.objective_id == r["obj"], "dA": abs(c["A"] - r["A"]),
                    "dP": abs(c["P"] - r["P"]), "dV": abs(c["V"] - r["V"]), "dFinal": abs(s.final - r["final"])})
    d = pd.DataFrame(out)
    print(f"grants compared: {len(d)} | same objective: {d.same_objective.mean():.1%}")
    for k in ("dA", "dP", "dV", "dFinal"):
        print(f"{k:7} max {d[k].max():.3f}")
    print(d.sort_values("dFinal", ascending=False).head(5).to_string(index=False))


if __name__ == "__main__":
    main()
