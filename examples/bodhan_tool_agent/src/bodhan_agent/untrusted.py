"""Untrusted content wrapping and prompt injection scanning (SPEC §8.7)."""

import re

from pydantic import BaseModel, ConfigDict

UNTRUSTED_START = "<UNTRUSTED_CONTENT_START>"
UNTRUSTED_END = "<UNTRUSTED_CONTENT_END>"

INJECTION_PATTERNS = [
    (r"\bignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|directions)\b", "ignore_previous_instructions"),
    (r"\bdisregard\s+(all\s+)?(previous|prior)\b", "disregard_previous"),
    (r"\bsystem\s*:\s*", "system_role_spoofing"),
    (r"\byou\s+are\s+now\s+(in\s+)?(developer|jailbreak|unrestricted|god)\s+mode\b", "mode_switch_attempt"),
    (r"\byour\s+new\s+(task|instruction|goal)\s+is\b", "task_override_attempt"),
    (r"\b(format|delete|rm\s+-rf|erase)\s+(disk|all|system|database)\b", "destructive_command"),
    (r"(<\s*tool_call\b|\btool_call\s*:|transcribe_audio\(|translate_text\(|synthesize_speech\()", "raw_tool_injection"),
    (r"\byou\s+must\s+(output|say|print|reveal)\s+(the\s+)?(api[_\s]?key|secret|password|token)\b", "credential_exfiltration"),
]


class InjectionHit(BaseModel):
    """Detected suspicious instruction pattern within untrusted data."""
    model_config = ConfigDict(extra="forbid")

    pattern_name: str
    matched_text: str
    position: int


def wrap(text: str) -> str:
    """Wrap raw untrusted text (OCR, transcripts) in structural demarcations."""
    return f"{UNTRUSTED_START}\n{text}\n{UNTRUSTED_END}"


def is_wrapped(text: str) -> bool:
    """Check if text is enclosed in untrusted content tags."""
    return text.strip().startswith(UNTRUSTED_START) and text.strip().endswith(UNTRUSTED_END)


def scan(text: str) -> list[InjectionHit]:
    """Scan text for common prompt injection and instruction hijack signatures.

    Content flagged here is logged in the trace as a security event, but is NEVER
    executed. It continues to be processed purely as data.
    """
    hits: list[InjectionHit] = []
    text_lower = text.lower()

    for pattern_regex, name in INJECTION_PATTERNS:
        for match in re.finditer(pattern_regex, text_lower, re.IGNORECASE):
            hits.append(
                InjectionHit(
                    pattern_name=name,
                    matched_text=match.group(0),
                    position=match.start(),
                )
            )

    return hits
