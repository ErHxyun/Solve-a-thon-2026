"""Shared test setup: run everything offline, in a temp data folder, on the committed fixture."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402


@pytest.fixture(autouse=True)
def offline_local(tmp_path, monkeypatch):
    """Every test: local storage in a temp dir, fixture collector, no LLM calls."""
    monkeypatch.setattr(config, "STORAGE_MODE", "local")
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "COLLECTOR_MODE", "fixture")
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    yield


@pytest.fixture
def raw_records() -> list[dict]:
    return json.loads((config.FIXTURES_DIR / "grants_sample_raw.json").read_text(encoding="utf-8"))


@pytest.fixture
def opportunities() -> list[dict]:
    """The same 40 records already normalized (snapshot 2026-11-07), so tests don't depend on the Collector."""
    return json.loads((config.FIXTURES_DIR / "opportunities_sample.json").read_text(encoding="utf-8"))
