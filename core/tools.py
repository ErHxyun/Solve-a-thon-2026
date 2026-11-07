"""Read-only tools that LLM agents may call through core.llm.chat_with_tools().  Owner: lead

Rules for every tool here:
  - read-only: never writes storage, never sends anything, never submits
  - no sensitive data: never looks up EIN / UEI / bank / credentials
  - deterministic where possible (dates are ALWAYS computed here, never by the model)
  - returns a small JSON-serializable dict; errors come back as {"error": "..."} instead of raising

Each tool = a Pydantic argument model + a function + a description. The registry `TOOLS`
is what the gateway exposes; each agent gets an explicit allow-list of tool names.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

from core import config, storage
from core.reference import load_profile

DEFAULT_PREP_DAYS = {"formula": 7, "pass-through": 7, "competitive": 30, "one-time disaster": 21, "not stated": 30}
MAX_TEXT = 4000  # keep tool results small so prompts stay cheap


def _read_reference(name: str) -> dict | None:
    """Reference files may live on another branch until merged; return None if missing."""
    path = config.REFERENCE_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _parse_date(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


# --------------------------------------------------------------------------- #
# 1. lookup_program_owner
# --------------------------------------------------------------------------- #
class LookupProgramOwnerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_agency_code: str = Field(description="Funder's top agency code, e.g. 'DHS', 'DOI', 'HHS'. Empty string if unknown.")
    text: str = Field(description="Opportunity title and/or key phrases to match against program keywords.")


def lookup_program_owner(args: LookupProgramOwnerArgs) -> dict:
    data = _read_reference("program_owners.json")
    if data is None:
        return {"error": "program_owners.json not available on this branch"}
    text = args.text.lower()
    for rule in data["rules"]:
        m = rule["match"]
        if ("top_agency_code" in m and args.top_agency_code.upper() == m["top_agency_code"]) or (
            "keyword" in m and m["keyword"].lower() in text
        ):
            return {"found": True, "owner": rule["owner"], "reason": rule["reason"]}
    return {"found": False, "note": "No NC owner rule matched; DNCR may be the right applicant, or check manually."}


# --------------------------------------------------------------------------- #
# 2. check_annual_program
# --------------------------------------------------------------------------- #
class CheckAnnualProgramArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cfda: list[str] = Field(description="Assistance listing (CFDA/ALN) numbers of the opportunity, e.g. ['15.904'].")
    title: str = Field(description="Opportunity title.")


def check_annual_program(args: CheckAnnualProgramArgs) -> dict:
    """Recurring programs DNCR already receives. A CFDA number can be shared by several programs
    (e.g. 15.916 covers LWCF State Assistance AND the competitive ORLP), so the title must also match."""
    data = _read_reference("annual_programs.json")
    if data is None:
        return {"error": "annual_programs.json not available"}
    candidates = []
    for p in data["programs"]:
        if p["cfda"] in args.cfda:
            name_score = fuzz.token_set_ratio(p["name"].lower(), args.title.lower())
            candidates.append({"program": p["name"], "division": p["division"], "type": p["type"],
                               "cfda": p["cfda"], "title_similarity": int(name_score)})
    likely = [c for c in candidates if c["title_similarity"] >= 60]
    return {
        "already_yours": bool(likely),
        "matches": likely,
        "cfda_only_matches": [c for c in candidates if c not in likely],
        "note": "cfda_only_matches share the number but the title differs — usually a separate competitive program.",
    }


# --------------------------------------------------------------------------- #
# 3. get_objective / 4. list_objectives
# --------------------------------------------------------------------------- #
class GetObjectiveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective_id: str = Field(description="DNCR objective id, e.g. '5.1'.")


def get_objective(args: GetObjectiveArgs) -> dict:
    for o in load_profile().objectives:
        if o.objective_id == args.objective_id:
            return o.model_dump()
    return {"error": f"objective {args.objective_id} not found", "valid_ids": [o.objective_id for o in load_profile().objectives]}


class ListObjectivesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def list_objectives(args: ListObjectivesArgs) -> dict:
    return {"objectives": [{"objective_id": o.objective_id, "text": o.text[:200], "urgency": o.urgency,
                            "divisions": o.divisions} for o in load_profile().objectives]}


# --------------------------------------------------------------------------- #
# 5. get_opportunity_details
# --------------------------------------------------------------------------- #
class GetOpportunityDetailsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opp_id: str = Field(description="Grants.gov opportunity id.")


def get_opportunity_details(args: GetOpportunityDetailsArgs) -> dict:
    """Full stored record (from today's run). Live mode falls back to the Grants.gov API."""
    for source in (lambda: storage.read_json("opportunities/latest.json", default=[]),
                   lambda: json.loads((config.FIXTURES_DIR / "opportunities_sample.json").read_text(encoding="utf-8"))):
        for o in source():
            if str(o["id"]) == args.opp_id:
                o = dict(o)
                o["description"] = o.get("description", "")[:MAX_TEXT]
                return o
    if config.COLLECTOR_MODE == "live":
        try:
            import httpx

            r = httpx.post("https://api.grants.gov/v1/api/fetchOpportunity", json={"opportunityId": int(args.opp_id)}, timeout=30)
            r.raise_for_status()
            d = r.json()["data"]
            body = d.get("synopsis") or d.get("forecast") or {}
            return {"id": args.opp_id, "title": d.get("opportunityTitle"), "eligibility": body.get("applicantEligibilityDesc", "")[:MAX_TEXT],
                    "description": (body.get("synopsisDesc") or body.get("forecastDesc") or "")[:MAX_TEXT],
                    "award_ceiling": body.get("awardCeiling"), "cost_sharing": body.get("costSharing")}
        except Exception as exc:
            return {"error": f"Grants.gov fetch failed: {exc}"}
    return {"error": f"opportunity {args.opp_id} not found"}


# --------------------------------------------------------------------------- #
# 6. days_until / 7. backward_timeline / 8. get_prep_time
# --------------------------------------------------------------------------- #
class DaysUntilArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_date: str = Field(description="ISO date yyyy-mm-dd.")


def days_until(args: DaysUntilArgs, today: date | None = None) -> dict:
    try:
        d = _parse_date(args.target_date)
    except ValueError:
        return {"error": f"not an ISO date: {args.target_date}"}
    today = today or date.today()
    return {"today": today.isoformat(), "target_date": d.isoformat(), "days": (d - today).days}


class GetPrepTimeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    money_type: str = Field(description="One of: formula, competitive, one-time disaster, pass-through, not stated.")


def get_prep_time(args: GetPrepTimeArgs) -> dict:
    pm = _read_reference("process_map.json")
    table = pm["prep_time_days_estimate"] if pm else DEFAULT_PREP_DAYS
    days = table.get(args.money_type, table.get("not stated", 30))
    return {"money_type": args.money_type, "prep_days": days, "source": "process_map.json" if pm else "default estimate"}


class BackwardTimelineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close_date: str = Field(description="Application close date from the listing, ISO yyyy-mm-dd.")
    prep_days: int = Field(description="Days of preparation needed (use get_prep_time).")


def backward_timeline(args: BackwardTimelineArgs, today: date | None = None) -> dict:
    try:
        close = _parse_date(args.close_date)
    except ValueError:
        return {"error": f"not an ISO date: {args.close_date}"}
    today = today or date.today()
    prep = max(args.prep_days, 1)
    # milestone = (fraction of prep time before the deadline, label)
    milestones = [(1.0, "Decide to pursue; notify division lead"), (0.8, "Confirm match / cost share and partners"),
                  (0.6, "Gather SF-424 information; open Grants.gov workspace"), (0.4, "Draft narrative and budget"),
                  (0.15, "Internal review and approvals"), (0.05, "AOR submits (buffer before deadline)")]
    steps = []
    for frac, label in milestones:
        d = close - timedelta(days=round(prep * frac))
        steps.append({"date": d.isoformat(), "days_before_close": round(prep * frac), "step": label, "already_past": d < today})
    days_left = (close - today).days
    return {"close_date": close.isoformat(), "days_left": days_left, "prep_days": prep,
            "too_late": days_left < prep, "steps": steps}


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., dict]

    def call(self, raw_args: str | dict) -> dict:
        try:
            args = self.args_model.model_validate_json(raw_args) if isinstance(raw_args, str) else self.args_model.model_validate(raw_args)
            return self.fn(args)
        except Exception as exc:  # tools never crash the agent
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("lookup_program_owner", "Find which NC office normally owns this kind of federal program (for Route decisions).",
         LookupProgramOwnerArgs, lookup_program_owner),
    Tool("check_annual_program", "Check if the opportunity is a recurring program DNCR already receives ('already yours').",
         CheckAnnualProgramArgs, check_annual_program),
    Tool("get_objective", "Get the exact text, page, urgency and divisions of one DNCR strategic-plan objective.",
         GetObjectiveArgs, get_objective),
    Tool("list_objectives", "List all DNCR strategic-plan objectives (short text) to check whether another one fits better.",
         ListObjectivesArgs, list_objectives),
    Tool("get_opportunity_details", "Get the full record of a Grants.gov opportunity (eligibility, award, cost sharing, description).",
         GetOpportunityDetailsArgs, get_opportunity_details),
    Tool("days_until", "Compute days from today to a date. Always use this instead of doing date math yourself.",
         DaysUntilArgs, days_until),
    Tool("get_prep_time", "Typical preparation time in days for a type of money.",
         GetPrepTimeArgs, get_prep_time),
    Tool("backward_timeline", "Build a dated backward plan from the close date. Always use this for timelines.",
         BackwardTimelineArgs, backward_timeline),
]}

# Default allow-lists per agent (an agent may pass a narrower list)
AGENT_TOOLS: dict[str, list[str]] = {
    "card_agent": ["get_objective", "list_objectives", "check_annual_program", "lookup_program_owner", "days_until"],
    "apply_assist": ["get_opportunity_details", "get_prep_time", "backward_timeline", "days_until"],
    "route_agent": [],  # owner lookup is done in code before the LLM call
}


def openai_tool_specs(names: list[str]) -> list[dict[str, Any]]:
    """Function-tool definitions in the strict format Azure OpenAI expects."""
    from openai import pydantic_function_tool

    return [pydantic_function_tool(TOOLS[n].args_model, name=n, description=TOOLS[n].description) for n in names]
