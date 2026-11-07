"""LLM gateway — the ONLY path from our code to Azure OpenAI.

Every agent calls chat_json() or embed(). The gateway handles:
  1. Sensitive-data guard (core/guard.py) before anything leaves our process
  2. Per-deployment parameters (tested 2026-11-07, see docs/azure-setup.md):
       - GPT-6 deployments (chat, chat-sol, chat-luna, chat-astra): NO temperature
         (only default 1 allowed) -> send reasoning_effort instead
       - chat-fallback (gpt-4.1): temperature = 0
  3. Structured output: responses are parsed into a Pydantic model
  4. Retries with backoff on 429 / 5xx, then automatic fallback deployment
  5. Audit log: one JSON line per call in logs/llm/YYYY-MM-DD.jsonl
     (no prompt text, only hashes + token usage) -> GenAI Field Notes + debugging

Owner: lead. Agents must not import `openai` directly.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional, Type, TypeVar

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core import config
from core.guard import scrub

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when LLM_ENABLED is false or every deployment failed. Callers must degrade gracefully."""


@lru_cache(maxsize=1)
def _client():
    from openai import AzureOpenAI

    if not (config.AOAI_ENDPOINT and config.AOAI_KEY):
        raise LLMUnavailable("AOAI_ENDPOINT / AOAI_KEY not set (see .env.example)")
    return AzureOpenAI(
        azure_endpoint=config.AOAI_ENDPOINT,
        api_key=config.AOAI_KEY,
        api_version=config.AOAI_API_VERSION,
    )


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in (408, 409, 429, 500, 502, 503, 504) or exc.__class__.__name__ in (
        "APITimeoutError",
        "APIConnectionError",
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _audit(record: dict) -> None:
    config.LOG_DIR.joinpath("llm").mkdir(parents=True, exist_ok=True)
    path = config.LOG_DIR / "llm" / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _params(deployment: str, max_tokens: int, reasoning_effort: str) -> dict:
    if deployment in config.AOAI_REASONING_DEPLOYMENTS:
        return {"max_completion_tokens": max_tokens, "reasoning_effort": reasoning_effort}
    return {"max_completion_tokens": max_tokens, "temperature": 0}


@retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _parse_once(deployment: str, messages: list[dict], schema: Type[T], params: dict):
    completions = _client().chat.completions
    parse = getattr(completions, "parse", None) or _client().beta.chat.completions.parse
    return parse(model=deployment, messages=messages, response_format=schema, **params)


def chat_json(
    *,
    agent: str,
    system: str,
    user: str,
    schema: Type[T],
    deployment: Optional[str] = None,
    max_tokens: int = 4000,
    reasoning_effort: str = "low",
    use_fallback: bool = True,
) -> T:
    """Call the chat model and return a validated `schema` instance.

    agent: name of the calling agent (for the audit log)
    Raises LLMUnavailable if disabled or every deployment fails.
    """
    if not config.LLM_ENABLED:
        raise LLMUnavailable("LLM_ENABLED=false")

    sys_g, user_g = scrub(system), scrub(user)
    messages = [{"role": "system", "content": sys_g.text}, {"role": "user", "content": user_g.text}]

    primary = deployment or config.AOAI_CHAT_DEPLOYMENT
    chain = [primary] + ([config.AOAI_FALLBACK_DEPLOYMENT] if use_fallback and primary != config.AOAI_FALLBACK_DEPLOYMENT else [])

    last_error: Optional[Exception] = None
    for dep in chain:
        started = time.time()
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agent": agent,
            "deployment": dep,
            "prompt_hash": _hash(sys_g.text + user_g.text),
            "redacted": sys_g.findings + user_g.findings,
        }
        try:
            resp = _parse_once(dep, messages, schema, _params(dep, max_tokens, reasoning_effort))
            choice = resp.choices[0]
            usage = resp.usage
            details = getattr(usage, "completion_tokens_details", None)
            record.update(
                ok=True,
                latency_s=round(time.time() - started, 2),
                finish_reason=choice.finish_reason,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                reasoning_tokens=getattr(details, "reasoning_tokens", None) if details else None,
            )
            _audit(record)
            parsed = choice.message.parsed
            if parsed is None:  # refusal or truncated output
                raise ValueError(f"no parsed output (finish_reason={choice.finish_reason}, refusal={choice.message.refusal})")
            return parsed
        except Exception as exc:  # noqa: BLE001 — we log and try the next deployment
            record.update(ok=False, latency_s=round(time.time() - started, 2), error=f"{type(exc).__name__}: {exc}"[:300])
            _audit(record)
            last_error = exc
    raise LLMUnavailable(f"all deployments failed: {last_error}")


def embed(texts: list[str], *, agent: str = "fit_scorer", batch_size: int = 64) -> list[list[float]]:
    """Embed texts with the embedding deployment. Raises LLMUnavailable when disabled."""
    if not config.LLM_ENABLED:
        raise LLMUnavailable("LLM_ENABLED=false")
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = [scrub(t).text for t in texts[i : i + batch_size]]
        started = time.time()
        resp = _client().embeddings.create(model=config.AOAI_EMBED_DEPLOYMENT, input=batch)
        vectors.extend(d.embedding for d in resp.data)
        _audit({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agent": agent,
            "deployment": config.AOAI_EMBED_DEPLOYMENT,
            "ok": True,
            "n": len(batch),
            "latency_s": round(time.time() - started, 2),
        })
    return vectors
