"""Prompt-injection defenses on retrieved content (report Section 10).

Everything the tools retrieve -- SOP passages, schedule/fleet records from
external systems, diagnosis payloads built from them -- is DATA, but it is
rendered into the same prompt the model reads. A tampered SOP file or a
malicious `delay_reason` coming over the fleet API could therefore try to
smuggle instructions in.

Before evidence is packed into the prompt it is scrubbed:

- invisible / BiDi control characters are removed (the classic vector for
  instructions hidden from human reviewers),
- chat-role line prefixes ("system:", "assistant:", ...) are neutralized so
  retrieved text cannot masquerade as conversation turns,
- known override phrasings ("ignore previous instructions", "reveal your
  system prompt", ...) are redacted in place.

Scrubbing never fails the request; it returns findings so the orchestrator
can note them in the answer, escalate, and audit (defense is layered with the
prompt-side rule that the evidence block is untrusted data).
"""
import re

REDACTION = "[redacted: instruction-like content]"
ROLE_NEUTRALIZED = "[role-prefix removed]"

# Zero-width, BiDi-override, and other invisible control characters.
_INVISIBLE = re.compile("[​-‏ -‮⁠-⁤﻿]")

# A line inside retrieved text trying to look like a chat turn.
_ROLE_LINE = re.compile(r"(?im)^[ \t>*-]*(system|assistant|developer|tool)\s*:")

_OVERRIDES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+\w*\s*(?:instructions?|rules?|prompts?|messages?)",
        r"disregard\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|your)\s+\w*\s*(?:instructions?|rules?|prompts?)",
        r"forget\s+(?:all\s+|everything|your)\s*(?:instructions?|rules?|above)?",
        r"new\s+instructions?\s*:",
        r"override\s+(?:the\s+)?(?:system|safety|previous)\s*\w*",
        r"do\s+anything\s+now",
        r"reveal\s+(?:your\s+)?(?:system\s+prompt|hidden\s+instructions?|instructions?)",
        r"you\s+are\s+now\s+(?:a|an|the)\s",
        r"act\s+as\s+(?:the\s+)?(?:system|administrator|developer|root)",
        r"respond\s+only\s+with\s+your\s+prompt",
    )
]


def scrub_text(text: str) -> tuple[str, list[str]]:
    """Returns (clean_text, findings). Findings are human-readable labels."""
    findings: list[str] = []
    if _INVISIBLE.search(text):
        text = _INVISIBLE.sub("", text)
        findings.append("invisible control characters removed")
    if _ROLE_LINE.search(text):
        text = _ROLE_LINE.sub(ROLE_NEUTRALIZED, text)
        findings.append("chat-role prefix neutralized")
    for pattern in _OVERRIDES:
        if pattern.search(text):
            text = pattern.sub(REDACTION, text)
            findings.append("instruction-override phrase redacted")
    return text, findings


def scrub_value(value):
    """Recursively scrub strings inside dict/list payloads."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        findings: list[str] = []
        clean = {}
        for k, v in value.items():
            cv, f = scrub_value(v)
            clean[k] = cv
            findings.extend(f)
        return clean, findings
    if isinstance(value, list):
        findings = []
        clean_list = []
        for v in value:
            cv, f = scrub_value(v)
            clean_list.append(cv)
            findings.extend(f)
        return clean_list, findings
    return value, []


def scrub_evidence(evidence_items):
    """Scrub every evidence payload; returns (new_items, deduplicated findings)."""
    clean_items = []
    findings: list[str] = []
    for ev in evidence_items:
        payload, f = scrub_value(ev.payload)
        if f:
            findings.extend(f"{label} (source: {ev.source})" for label in f)
            ev = ev.model_copy(update={"payload": payload})
        clean_items.append(ev)
    # dedupe, keep order
    return clean_items, list(dict.fromkeys(findings))
