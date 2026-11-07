"""Sensitive-data guard (Requirement R7).

Runs on EVERY text before it is sent to an LLM (called by core/llm.py).
Grant listings are public, but user notes or pasted text could contain
identifiers that must never leave the state's control.

Owner: lead. Add patterns here; add a test in tests/test_guard.py for each one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (name, pattern). Keep patterns specific to limit false positives on grant numbers.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("EIN", re.compile(r"\b\d{2}-\d{7}\b")),
    # UEI: 12 chars, uppercase letters + digits, must contain both
    ("UEI", re.compile(r"\b(?=[A-Z0-9]{12}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{12}\b")),
    ("BANK_ROUTING", re.compile(r"(?i)\b(routing|aba)\s*(number|no\.?|#)?\s*[:#]?\s*\d{9}\b")),
    ("BANK_ACCOUNT", re.compile(r"(?i)\b(account|acct)\s*(number|no\.?|#)?\s*[:#]?\s*\d{6,17}\b")),
    ("PASSWORD", re.compile(r"(?i)\b(password|passwd|pwd|passcode)\s*[:=]\s*\S+")),
    ("API_KEY", re.compile(r"(?i)\b(api[_-]?key|secret|token)\s*[:=]\s*\S{8,}")),
]


@dataclass
class GuardResult:
    text: str              # redacted text, safe to send
    findings: list[str]    # pattern names that were redacted


def scrub(text: str) -> GuardResult:
    """Redact every sensitive match. Returns redacted text + what was found."""
    findings: list[str] = []
    for name, pattern in PATTERNS:
        if pattern.search(text):
            findings.append(name)
            text = pattern.sub(f"[REDACTED-{name}]", text)
    return GuardResult(text=text, findings=findings)
