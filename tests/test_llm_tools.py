"""chat_with_tools() loop in the LLM gateway (owner: lead). Uses a fake client — no Azure calls."""
import json
from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from core import config, llm


class Answer(BaseModel):
    days: int


def tool_call(call_id, name, args):
    return NS(id=call_id, function=NS(name=name, arguments=json.dumps(args)))


class FakeClient:
    """First create() asks for tools; second create() answers without tools; parse() returns the final schema."""

    def __init__(self, first_calls):
        self.create_calls = 0
        self.parse_messages = None
        self.chat = NS(completions=NS(create=self.create, parse=self.parse))
        self._first = first_calls

    def create(self, model, messages, tools, **params):
        self.create_calls += 1
        assert "temperature" not in params or model == config.AOAI_FALLBACK_DEPLOYMENT
        calls = self._first if self.create_calls == 1 else None
        return NS(choices=[NS(message=NS(content=None, tool_calls=calls))])

    def parse(self, model, messages, response_format, **params):
        self.parse_messages = messages
        tool_msgs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        days = next((t["days"] for t in tool_msgs if "days" in t), -1)
        return NS(choices=[NS(finish_reason="stop", message=NS(parsed=response_format(days=days), refusal=None))],
                  usage=None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)


def test_tool_result_reaches_final_answer(monkeypatch, enabled):
    fake = FakeClient([tool_call("t1", "days_until", {"target_date": "2999-01-01"})])
    monkeypatch.setattr(llm, "_client", lambda: fake)
    out = llm.chat_with_tools(agent="card_agent", system="s", user="u", schema=Answer, tools=["days_until"])
    assert out.days > 300_000
    assert any(m["role"] == "tool" for m in fake.parse_messages)


def test_disallowed_tool_is_refused(monkeypatch, enabled):
    fake = FakeClient([tool_call("t1", "lookup_program_owner", {"top_agency_code": "DHS", "text": "x"})])
    monkeypatch.setattr(llm, "_client", lambda: fake)
    llm.chat_with_tools(agent="card_agent", system="s", user="u", schema=Answer, tools=["days_until"])
    tool_msg = next(m for m in fake.parse_messages if m["role"] == "tool")
    assert "not allowed" in tool_msg["content"]


def test_tool_calls_are_audited(monkeypatch, enabled):
    fake = FakeClient([tool_call("t1", "days_until", {"target_date": "2030-01-01"})])
    monkeypatch.setattr(llm, "_client", lambda: fake)
    llm.chat_with_tools(agent="card_agent", system="s", user="u", schema=Answer, tools=["days_until"])
    lines = [json.loads(x) for f in (config.LOG_DIR / "llm").glob("*.jsonl") for x in f.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("kind") == "tool_call" and r["tool"] == "days_until" for r in lines)
    assert any(r.get("kind") == "chat_with_tools" and r["ok"] and r["tool_calls"] == 1 for r in lines)


def test_unknown_tool_name_is_rejected(enabled):
    with pytest.raises(ValueError):
        llm.chat_with_tools(agent="x", system="s", user="u", schema=Answer, tools=["send_email"])


def test_disabled_raises_unavailable():
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_with_tools(agent="card_agent", system="s", user="u", schema=Answer)
