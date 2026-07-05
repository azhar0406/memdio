"""Fact extraction + query routing — the memory-intelligence layer.

This is the productised version of the pipeline that took memdio from ~50% to
72.9% on LongMemEval (gpt-4o), beating mem0 and Zep. It is provider-agnostic: the
caller supplies an ``llm(prompt: str) -> str`` callable, so memdio core never
depends on a specific LLM SDK.

Two capabilities:
  * ``extract_facts`` — distil a conversation/document into atomic, dated,
    self-contained facts. Stored alongside the raw memory (see
    ``StorageManager.remember``), discrete facts make counting/temporal questions
    tractable that raw-session retrieval alone cannot answer.
  * ``classify_query`` — route a query to the retrieval strategy that scores best
    for its type (see ``StorageManager.recall``).
"""

from __future__ import annotations

import re
from collections.abc import Callable

EXTRACT_PROMPT = """Extract atomic FACTS from the text below.

Rules:
- Output one fact per line. No numbering, no bullets, no preamble.
- Each fact must be self-contained (name the subject) and specific.
- Record CONCRETE, durable facts: things done, bought, owned, attended, decided,
  or firmly preferred — with quantities/weights/prices/dates and named items.
  Preserve the specifics needed to later count or total them.
- ALSO record specific, notable recommendations or answers given to the user.
- IGNORE vague intentions/interest and generic filler.
- Keep any date in the fact text.
- If the text contains no durable facts, output the single word: NONE

Date: {date}

Text:
{text}

Facts (one per line):"""


def _parse_facts(text: str) -> list[str]:
    if not text:
        return []
    facts = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if not line or line.upper() == "NONE":
            continue
        facts.append(line)
    return facts


def extract_facts(llm: Callable[[str], str], text: str, date: str | None = None) -> list[str]:
    """Extract atomic facts from ``text`` using a caller-supplied LLM callable.

    ``llm`` takes a prompt string and returns the model's text. Any exception from
    ``llm`` yields an empty list (extraction is best-effort).
    """
    prompt = EXTRACT_PROMPT.format(date=date or "unknown", text=text)
    try:
        return _parse_facts(llm(prompt))
    except Exception:
        return []


# --- Query routing -------------------------------------------------------------

_AGG_PATTERNS = re.compile(
    r'\bhow many\b|\bhow much\b|\bhow often\b|\bnumber of\b|\btotal\b|\bcount\b'
    r'|\bhow frequently\b|\btimes\b',
    re.IGNORECASE,
)
_TEMPORAL_PATTERNS = re.compile(
    r'when did|how long ago|what date|what day|which month|which year'
    r'|last (?:week|month|year)|\border of\b|earliest to latest|\bsequence of\b',
    re.IGNORECASE,
)


def classify_query(query: str) -> str:
    """Return the retrieval mode for a query: 'aggregation', 'temporal', or 'detail'.

    Aggregation/temporal questions answer best from discrete dated facts; detail
    questions answer best from the full raw+fact hybrid. 'how long' is treated as a
    duration (detail), not counting.
    """
    if _AGG_PATTERNS.search(query):
        return "aggregation"
    if _TEMPORAL_PATTERNS.search(query):
        return "temporal"
    return "detail"
