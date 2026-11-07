"""Card Agent — turns each top-N item into a plain-English decision card.  Owner: LLM-A
Spec: docs/agents/card_agent.md

The card answers the user's 5 questions (can DNCR apply / which objective & division /
money type / time & prerequisites / worth it) plus "why it might NOT fit",
with verbatim evidence quotes the Verifier can check.

Design (v2):
- The prompt separates QUOTABLE text (listing + plan, exactly what the Verifier searches)
  from FACTS our code computed (deadline, days left, award, hints). Facts may be used but
  never quoted, so the model can't "quote" a line the Verifier will never find.
- Code, not the model, owns ids, the ranked objective, and every date / dollar figure:
  figures in the card that aren't in the source trigger one self-repair call, then get masked.
- Cards are cached by a hash of (prompt version, deployment, listing text + facts, matched
  objective) — never days left or score, which change daily — so an unchanged listing costs
  nothing the next morning and an offline demo can replay real cards.
- If the model names a different objective than the ranking, the ranked one stays on the card
  and the disagreement is written into why_not_fit, so a person still sees it.
- When the LLM is off or fails, an *extractive* offline card is built: every quote is an
  exact substring of the source, so the Verifier and UI still work end to end.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Optional

from rapidfuzz import fuzz

from core import config, storage
from core.llm import LLMUnavailable, chat_json
from core.reference import load_profile
from core.schemas import Card, Evidence, Objective, Opportunity, Profile, ScoredItem

PROMPT_VERSION = "v2.1"   # bump on any prompt change: it invalidates the card cache
SYSTEM_PROMPT = (config.PROMPTS_DIR / "card_agent.md").read_text(encoding="utf-8")
MAX_WORKERS = 5          # keep below Azure OpenAI rate limits
DESC_CHARS = 6000        # listing description sent to the model
CACHE_PREFIX = "cache/cards"
OFFLINE = "[offline placeholder]"

STATE_CODES = {"00"}             # Grants.gov applicant type: State governments
UNRESTRICTED_CODES = {"99"}
# "recovery" / "emergency" alone are left out: they also mean substance-use recovery and emergency medicine
ONE_TIME_WORDS = ("disaster", "supplemental", "hurricane", "one-time", "one time")
MATCH_WORDS = ("cost share", "cost-share", "cost sharing", "matching", "match ", "non-federal share", "1:1")
PREREQ_WORDS = ("letter of intent", "pre-application", "preapplication", "letters of support", "sam.gov",
                "unique entity", "partner", "consortium", "section 106", "environmental review", "nepa")


# --------------------------------------------------------------------------- #
# Small text helpers
# --------------------------------------------------------------------------- #
def _sentences(text: str) -> list[str]:
    """Split into sentences without changing characters, so each piece is an exact substring."""
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 20]


def _clip(sentence: str, limit: int = 220) -> str:
    """Shorten at a word boundary. The result is still an exact substring of the sentence."""
    if len(sentence) <= limit:
        return sentence
    cut = sentence[:limit]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def _find(sentences: list[str], words: tuple[str, ...]) -> Optional[str]:
    for s in sentences:
        low = s.lower()
        if any(w in low for w in words):
            return s
    return None


def _money(x: Optional[float]) -> str:
    if x is None or x <= 0:
        return "not stated"
    if x >= 1_000_000:
        return f"${x / 1_000_000:g} million"
    return f"${x:,.0f}"


def _is_placeholder_text(text: str) -> bool:
    return not text or text.strip().upper().startswith("TODO")


def _listing_text(opp: Opportunity) -> str:
    """Exactly the text the Verifier searches for source='listing'."""
    return " ".join([opp.title, opp.description, opp.eligibility_text])


@lru_cache(maxsize=1)
def _annual_programs() -> dict[str, dict]:
    path = config.REFERENCE_DIR / "annual_programs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {p["cfda"]: p for p in data.get("programs", [])}


def _objective(profile: Profile, objective_id: Optional[str]) -> Optional[Objective]:
    return next((o for o in profile.objectives if o.objective_id == objective_id), None)


# --------------------------------------------------------------------------- #
# Facts computed by code (the model may use them, never quote them)
# --------------------------------------------------------------------------- #
def eligibility_summary(opp: Opportunity) -> str:
    codes = {a.id for a in opp.applicant_types}
    if codes & STATE_CODES:
        return "State governments are listed as eligible applicants"
    if codes & UNRESTRICTED_CODES:
        return "Eligibility is unrestricted (anyone may apply)"
    if "state" in opp.eligibility_text.lower():
        return "State governments are not a listed applicant type; the eligibility text mentions states (unclear)"
    return "State governments are not listed as eligible"


def money_type_hint(opp: Opportunity, item: ScoredItem) -> tuple[str, str]:
    """A *hint* for money_type with its reason. The model decides; we only measure agreement.

    Never forced: CFDA 15.916 is both the LWCF state program (pass-through) and ORLP (competitive),
    so a CFDA match alone is not proof (see GenAI log, 2026-11-07).
    """
    title = opp.title.lower()
    body = opp.description.lower()
    title_word = next((w for w in ONE_TIME_WORDS if w in title), None)
    if title_word:
        return "one-time disaster", f'title says "{title_word}"'
    # One passing mention ("disaster resilience", "chemical disasters") is not a disaster program
    body_hits = body.count("disaster") + body.count("hurricane")
    if body_hits >= 3:
        return "one-time disaster", f'listing mentions disasters/hurricanes {body_hits} times'
    annual = next((_annual_programs()[c] for c in opp.cfda if c in _annual_programs()), None)
    if annual and fuzz.token_set_ratio(annual["name"].lower(), title) >= 60:
        return annual.get("type", "formula"), f'same CFDA and name as recurring program "{annual["name"]}"'
    if opp.funding_instruments and all(i.lower() in ("grant", "cooperative agreement") for i in opp.funding_instruments):
        return "competitive", "discretionary grant / cooperative agreement listing"
    return "not stated", "no signal"


def facts_block(opp: Opportunity, item: ScoredItem) -> list[str]:
    """Only facts that stay the same while the listing does. Days left and ranking score change
    every morning; they are shown by the app, never baked into the card (and never in the cache key)."""
    hint, why = money_type_hint(opp, item)
    if opp.status == "forecasted":
        timing = "Forecast only (not open yet)" + (f"; expected close {opp.close_date}" if opp.close_date else "")
    elif opp.close_date:
        timing = f"Closes {opp.close_date}"
    else:
        timing = "No close date stated (rolling or not stated)"
    return [
        f"- Eligibility: {eligibility_summary(opp)}",
        f"- Timing: {timing}",
        f"- Award: floor {_money(opp.award_floor)}, ceiling {_money(opp.award_ceiling)}",
        f"- Cost sharing flag on the record: {'yes' if opp.cost_sharing else 'no' if opp.cost_sharing is False else 'not stated'} (the flag is often missing; check the text)",
        f"- Funding instruments: {', '.join(opp.funding_instruments) or 'not stated'}",
        f"- CFDA / assistance listing: {', '.join(opp.cfda) or 'not stated'}",
        f"- Recurring program DNCR already receives: {'yes' if item.already_yours else 'no'}",
        f"- Money type hint (from rules, may be wrong): {hint} — {why}",
    ]


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def build_user_prompt(opp: Opportunity, item: ScoredItem, profile: Profile, feedback: str | None = None) -> str:
    obj = _objective(profile, item.objective_id)
    plan_text = obj.text if obj and not _is_placeholder_text(obj.text) else ""
    parts = [
        "## LISTING TEXT (source = \"listing\"; you may quote ONLY from this block or the PLAN TEXT block)",
        f"Title: {opp.title}",
        f"Eligibility text: {opp.eligibility_text or 'not stated'}",
        f"Description: {opp.description[:DESC_CHARS]}",
        "",
        "## PLAN TEXT (source = \"plan\")",
        f"Objective {obj.objective_id} (p. {obj.page}): {plan_text}" if obj and plan_text else
        (f"Objective {obj.objective_id}: [plan wording not loaded yet — do not quote the plan]" if obj else "none"),
        f"Divisions for this objective: {', '.join(obj.divisions) if obj and obj.divisions else 'not stated'}",
        "",
        "## FACTS computed by our code (use them, but NEVER quote them — they are not in the source)",
        f"Funder: {opp.agency_name} ({opp.agency_code})",
        *facts_block(opp, item),
    ]
    if feedback:
        parts += ["", "## Reviewer feedback on your previous card — fix every point", feedback]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Post-processing owned by code
# --------------------------------------------------------------------------- #
_DOLLAR = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|thousand|[MBK])\b)?", re.I)
_DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.I,
)
_NARRATIVE_FIELDS = ("can_dncr_apply", "who_applies", "worth_it", "summary", "why_fit", "why_not_fit")


def _dollar_value(token: str) -> Optional[float]:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", token)
    if not m:
        return None
    value = float(m.group().replace(",", ""))
    low = token.lower()
    if "billion" in low or re.search(r"\db\b", low):
        value *= 1e9
    elif "million" in low or re.search(r"\dm\b", low):
        value *= 1e6
    elif "thousand" in low or re.search(r"\dk\b", low):
        value *= 1e3
    return value


def unsupported_figures(card: Card, opp: Opportunity) -> list[str]:
    """Dollar amounts and dates in the card's narrative that appear nowhere in the source record."""
    source = _listing_text(opp)
    low_source = source.lower()
    known_money = {v for v in (opp.award_floor, opp.award_ceiling) if v}
    known_money |= {v for t in _DOLLAR.findall(source) if (v := _dollar_value(t))}
    known_dates = {d for d in (opp.close_date, opp.open_date) if d}

    bad: list[str] = []
    for name in _NARRATIVE_FIELDS:
        text = getattr(card, name) or ""
        for tok in _DOLLAR.findall(text):
            value = _dollar_value(tok)
            if value is None or not any(abs(value - k) <= max(1.0, 0.01 * k) for k in known_money):
                bad.append(tok.strip())
        for tok in _DATE.findall(text):
            if tok.lower() not in low_source and tok not in known_dates:
                bad.append(tok.strip())
    return sorted(set(bad))


def _mask_figures(card: Card, figures: list[str]) -> Card:
    updates = {}
    for name in _NARRATIVE_FIELDS:
        text = getattr(card, name)
        for fig in figures:
            text = text.replace(fig, "[see listing]")
        updates[name] = text
    return card.model_copy(update=updates)


def objective_disagreement(card: Card, item: ScoredItem, profile: Optional[Profile]) -> Optional[str]:
    """Plain-English note when the model picked a different objective than the ranking.
    The ranked objective stays on the card (UI and scores agree); the disagreement stays visible."""
    picked, ranked = (card.objective_id or "").strip(), item.objective_id
    if not picked or not ranked or picked == ranked:
        return None
    known = {o.objective_id for o in profile.objectives} if profile else set()
    if picked in known:
        return f"Card writer thought objective {picked} fits better than the ranked objective {ranked} — check which one applies."
    return f"Card writer named objective {picked}, which is not in the DNCR plan; kept the ranked objective {ranked}."


def finalize(card: Card, opp: Opportunity, item: ScoredItem, profile: Optional[Profile] = None) -> Card:
    """Code-owned fields and cleanup. Never drops a quote: judging quotes is the Verifier's job."""
    seen: set[tuple[str, str]] = set()
    evidence: list[Evidence] = []
    for ev in card.evidence:
        quote = re.sub(r"\s+", " ", ev.quote).strip().strip('"“”\'').strip()
        key = (ev.source, quote.lower())
        if quote and key not in seen:
            seen.add(key)
            evidence.append(Evidence(field=ev.field, quote=quote, source=ev.source))
    why_not_fit = card.why_not_fit.strip()
    note = objective_disagreement(card, item, profile)
    if note:
        why_not_fit = f"{why_not_fit} {note}".strip()
    return card.model_copy(update={
        "opp_id": opp.id,                                          # never trust the model with ids
        "objective_id": item.objective_id or card.objective_id,    # ranked objective stays; disagreement noted above
        "division": card.division if card.division and card.division != "not stated" else (item.division or "not stated"),
        "why_not_fit": why_not_fit or "not stated",
        "evidence": evidence,
    })


# --------------------------------------------------------------------------- #
# Offline card: extractive, every quote is an exact substring of the source
# --------------------------------------------------------------------------- #
def placeholder_card(opp: Opportunity, item: ScoredItem, profile: Optional[Profile] = None) -> Card:
    obj = _objective(profile, item.objective_id) if profile else None
    desc_sents = _sentences(opp.description)
    elig_sents = _sentences(opp.eligibility_text)
    codes = {a.id for a in opp.applicant_types}
    evidence: list[Evidence] = []

    def add(field: str, sentence: Optional[str], source: str = "listing") -> None:
        if sentence:
            evidence.append(Evidence(field=field, quote=_clip(sentence), source=source))

    state_sent = _find(elig_sents + desc_sents, ("state",))
    add("can_dncr_apply", state_sent)
    match_sent = _find(desc_sents + elig_sents, MATCH_WORDS)
    add("prerequisites", match_sent)
    add("summary", desc_sents[0] if desc_sents else None)
    if obj and not _is_placeholder_text(obj.text):
        add("why_fit", _sentences(obj.text)[0] if _sentences(obj.text) else obj.text, source="plan")

    prereqs = []
    if opp.cost_sharing or match_sent:
        prereqs.append("Cost share / match may be required — see the listing")
    if _find(desc_sents + elig_sents, PREREQ_WORDS):
        prereqs.append("Other steps mentioned (partners, letters or reviews) — see the listing")

    risks = []
    if opp.status == "forecasted":
        risks.append("forecast only, not open yet")
    if not codes & (STATE_CODES | UNRESTRICTED_CODES):
        risks.append("state eligibility is unclear")
    if opp.cost_sharing:
        risks.append("cost share required")
    if item.confidence == "Low":
        risks.append("weak match to the plan")

    hint, _ = money_type_hint(opp, item)
    hits = [k for k in (profile.keywords if profile else []) if k.lower() in f"{opp.title} {opp.description}".lower()]
    return Card(
        opp_id=opp.id,
        can_dncr_apply=f"{OFFLINE} {eligibility_summary(opp)}.",
        who_applies=f"DNCR – {item.division} (to confirm)" if item.division else "not stated",
        objective_id=item.objective_id or "",
        division=item.division or "not stated",
        money_type=hint,
        prerequisites=prereqs,
        worth_it=f"Award ceiling {_money(opp.award_ceiling)}; effort not assessed offline.",
        summary=" ".join(desc_sents[:2]) if desc_sents else opp.title,
        why_fit=(f"Ranked {item.score} against objective {item.objective_id}"
                 + (f"; plan words found: {', '.join(hits[:5])}." if hits else ".")),
        why_not_fit=("Possible issues: " + "; ".join(risks) + ".") if risks else "Offline mode: risks were not assessed — read the eligibility section.",
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def cache_inputs(opp: Opportunity, item: ScoredItem, profile: Profile, deployment: Optional[str]) -> dict:
    """Everything the card is written from — and nothing that changes daily.

    In: prompt version + system prompt, deployment, the listing text and listing facts,
    the matched objective (id + wording), already_yours.
    Out on purpose: days_to_close, score, confidence, watch, ingested_at (they change every
    morning or whenever weights change; the app shows them next to the card).
    """
    obj = _objective(profile, item.objective_id)
    return {
        "prompt": [PROMPT_VERSION, hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()],
        "deployment": deployment or config.AOAI_CHAT_DEPLOYMENT,
        "listing": opp.model_dump(include={
            "title", "eligibility_text", "description", "agency_code", "agency_name", "status", "close_date",
            "award_floor", "award_ceiling", "cost_sharing", "funding_instruments", "cfda", "applicant_types",
        }),
        "objective": [item.objective_id, obj.text if obj else "", list(obj.divisions) if obj else []],
        "already_yours": item.already_yours,
    }


def cache_key(opp: Opportunity, item: ScoredItem, profile: Profile, deployment: Optional[str]) -> str:
    raw = json.dumps(cache_inputs(opp, item, profile, deployment), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_path(opp_id: str, key: str) -> str:
    return f"{CACHE_PREFIX}/{opp_id}-{key}.json"


# --------------------------------------------------------------------------- #
# Main entry points
# --------------------------------------------------------------------------- #
def _call(opp: Opportunity, item: ScoredItem, profile: Profile, feedback: Optional[str], deployment: Optional[str]) -> Card:
    card = chat_json(
        agent="card_agent",
        system=SYSTEM_PROMPT,
        user=build_user_prompt(opp, item, profile, feedback),
        schema=Card,
        deployment=deployment,
        reasoning_effort="low",
    )
    return finalize(card, opp, item, profile)


def make_card(
    opp: Opportunity,
    item: ScoredItem,
    profile: Profile,
    feedback: str | None = None,
    *,
    deployment: Optional[str] = None,
    use_cache: bool = True,
) -> Card:
    key = cache_key(opp, item, profile, deployment)
    if use_cache and feedback is None:
        cached = storage.read_json(_cache_path(opp.id, key), default=None)
        if cached:
            return Card(**cached)
    try:
        card = _call(opp, item, profile, feedback, deployment)
        figures = unsupported_figures(card, opp)
        if figures:  # one self-repair, then mask whatever is still unsupported
            note = ("These dates or dollar amounts are not in the listing; remove them or use 'not stated': "
                    + ", ".join(figures))
            card = _call(opp, item, profile, "\n".join(filter(None, [feedback, note])), deployment)
            left = unsupported_figures(card, opp)
            if left:
                card = _mask_figures(card, left)
    except LLMUnavailable:
        return placeholder_card(opp, item, profile)
    if use_cache and feedback is None:
        storage.write_json(_cache_path(opp.id, key), card.model_dump())
    return card


def run(state: dict) -> dict:
    profile = load_profile()
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    items = [ScoredItem(**s) for s in state["top"]]
    feedback = state.get("card_feedback", {})  # opp_id -> verifier feedback (retry round)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        cards = list(pool.map(lambda it: make_card(opps[it.opp_id], it, profile, feedback.get(it.opp_id)), items))
    return {"cards": [c.model_dump() for c in cards]}
