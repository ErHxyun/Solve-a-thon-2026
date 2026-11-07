"""Central configuration. Every setting comes from environment variables (.env locally,
Container Apps env/secrets in Azure). Nothing secret is ever hard-coded here."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Azure OpenAI
AOAI_ENDPOINT = _get("AOAI_ENDPOINT")
AOAI_KEY = _get("AOAI_KEY")
AOAI_API_VERSION = _get("AOAI_API_VERSION", "2025-04-01-preview")
AOAI_CHAT_DEPLOYMENT = _get("AOAI_CHAT_DEPLOYMENT", "chat")
AOAI_FALLBACK_DEPLOYMENT = _get("AOAI_FALLBACK_DEPLOYMENT", "chat-fallback")
AOAI_EMBED_DEPLOYMENT = _get("AOAI_EMBED_DEPLOYMENT", "embed")
AOAI_REASONING_DEPLOYMENTS = {
    d.strip() for d in _get("AOAI_REASONING_DEPLOYMENTS", "chat,chat-sol,chat-luna,chat-astra").split(",") if d.strip()
}

# Storage
STORAGE_MODE = _get("STORAGE_MODE", "local")          # local | blob
STORAGE_CONN = _get("STORAGE_CONN")
STORAGE_CONTAINER = _get("STORAGE_CONTAINER", "ffm-data")
LOCAL_DATA_DIR = ROOT / "data"

# Pipeline
AGENCY = _get("AGENCY", "dncr")
TOP_N = int(_get("TOP_N", "15"))
COLLECTOR_MODE = _get("COLLECTOR_MODE", "fixture")    # fixture | live
LLM_ENABLED = _get("LLM_ENABLED", "false").lower() == "true"

# Paths to committed reference data and fixtures
REFERENCE_DIR = ROOT / "reference"
PROMPTS_DIR = ROOT / "prompts"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
LOG_DIR = ROOT / "logs"
