"""Staff UI (MODE=app) — minimal placeholder so the pipeline output can be seen.  Owner: P5
Spec: docs/agents/README.md (UI section) — P5 replaces this with the full decision-card UI
(Chase / Waitlist / Skip / Route, early-warning panel, filters, export) or the npm frontend.

Run locally:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from core import config, storage  # noqa: E402
from core.schemas import Decision  # noqa: E402

st.set_page_config(page_title="Federal Funds Matchmaker — DNCR", layout="wide")
st.title("Federal Funds Matchmaker — DNCR")

run = storage.read_json("runs/latest.json", default=None)
rows = storage.read_json(f"scored/{config.AGENCY}/latest.json", default=[])
if run:
    st.caption(f"Last refreshed: {run.get('finished_at')} UTC · status {run.get('status')} · {run.get('counts')}")
if not rows:
    st.info("No results yet. Run `python pipeline.py` first.")
    st.stop()

show_low = st.toggle("Show low-confidence items", value=False)
for row in rows[: config.TOP_N]:
    checked = row.get("checked") or {}
    conf = checked.get("confidence", row["confidence"])
    if conf == "Low" and not show_low:
        continue
    opp, card = row["opportunity"], (checked.get("card") or {})
    with st.container(border=True):
        st.subheader(opp["title"])
        st.write(f"**{conf}** · Objective {row.get('objective_id')} · {row.get('division') or '—'} · "
                 f"{opp.get('days_to_close') if opp.get('days_to_close') is not None else 'rolling / not stated'} days left"
                 + (" · already yours" if row.get("already_yours") else ""))
        if card:
            st.write(card.get("summary", ""))
            st.write(f"Why it fits: {card.get('why_fit', '')}")
            st.write(f"Why it might not: {card.get('why_not_fit', '')}")
        for w in checked.get("warnings", []):
            st.warning(w)
        cols = st.columns(4)
        for col, label in zip(cols, ["Chase", "Waitlist", "Skip", "Route"], strict=True):
            if col.button(label, key=f"{label}-{row['opp_id']}"):
                decisions = storage.read_json("decisions/dncr.json", default=[])
                decisions.append(Decision(opp_id=row["opp_id"], decision=label).model_dump())
                storage.write_json("decisions/dncr.json", decisions)
                st.success(f"Saved: {label}")
        st.link_button("Open listing", opp["url"])
