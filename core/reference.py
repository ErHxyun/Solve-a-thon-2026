"""Loaders for shared reference data in reference/. Used by several agents, so it lives in core."""
from __future__ import annotations

import json

from core import config
from core.schemas import Profile


def load_profile() -> Profile:
    path = config.REFERENCE_DIR / f"{config.AGENCY}_profile.json"
    return Profile(**json.loads(path.read_text(encoding="utf-8")))


def load_annual_cfdas() -> dict[str, str]:
    """CFDA/ALN number -> division, for recurring programs DNCR already receives."""
    data = json.loads((config.REFERENCE_DIR / "annual_programs.json").read_text(encoding="utf-8"))
    return {p["cfda"]: p["division"] for p in data["programs"]}
