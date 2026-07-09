"""Ingest-time fact extraction: turn raw conversation sessions into atomic,
dated, self-contained facts about the user. Each fact becomes its own memory,
so multi-session aggregation ("how many X", "total Y") reduces to retrieving
and counting discrete facts instead of re-reading raw session dumps.
"""

import os
import time
from typing import NamedTuple

from openai import OpenAI

EXTRACT_PROMPT = """Extract atomic FACTS about the USER from the conversation below.

Rules:
- Output one fact per line. No numbering, no bullets, no preamble.
- Each fact must be self-contained (name the subject) and specific.
- Record CONCRETE, durable facts about the USER: things done, bought, owned, attended,
  decided, or firmly preferred — with quantities/weights/prices/dates and named items
  (e.g. "The user bought a 20-pound bag of layer feed", "The user built a Tamiya 1/48
  Spitfire model kit"). Preserve the specifics needed to later count or total them.
- ALSO record specific, notable things the ASSISTANT told or recommended to the user
  (named recommendations, resources, concrete advice, or direct answers) — e.g.
  "The assistant recommended the book 'Matthew for Everyone' by N.T. Wright".
- IGNORE vague intentions/interest ("looking for ideas", "is interested in") and
  generic filler.
- Keep any date in the fact text.
- If the conversation contains no durable facts, output the single word: NONE

Conversation date: {date}

Conversation:
{session}

Facts (one per line):"""


PREF_EXTRACT_PROMPT = """Extract durable USER PREFERENCES from the conversation below.

Rules:
- Output one preference per line. No numbering, no bullets, no preamble.
- Extract the user's stated preferences, interests, constraints, and current setup,
  with specifics (exact brands, genres, tools, activities, named topics).
- Capture both explicit preferences ("I like X") and revealed preferences supported
  by the conversation (the user asked for resources on Y, attended Z, is building W).
- Prefer normalized, category-bearing wording when the category is clear.
- Distinguish durable standing preferences from one-off task requests.
- Keep any date or context needed to scope the preference.
- Do NOT invent preferences not explicitly supported by the conversation.
- If the conversation contains no durable preferences, output the single word: NONE

Conversation date: {date}

Conversation:
{session}

Preferences (one per line):"""


COMBINED_EXTRACT_PROMPT = """Extract durable USER FACTS and PREFERENCES from the conversation below.

Return EXACTLY two sections with these headers:
FACTS:
<one fact per line, or NONE>

PREFERENCES:
<one preference per line, or NONE>

FACTS rules:
- Output one fact per line. No numbering, no bullets, no preamble.
- Each fact must be self-contained (name the subject) and specific.
- Record CONCRETE, durable facts about the USER: things done, bought, owned, attended,
  decided, or firmly preferred — with quantities/weights/prices/dates and named items
  (e.g. "The user bought a 20-pound bag of layer feed", "The user built a Tamiya 1/48
  Spitfire model kit"). Preserve the specifics needed to later count or total them.
- ALSO record specific, notable things the ASSISTANT told or recommended to the user
  (named recommendations, resources, concrete advice, or direct answers) — e.g.
  "The assistant recommended the book 'Matthew for Everyone' by N.T. Wright".
- IGNORE vague intentions/interest ("looking for ideas", "is interested in") and
  generic filler.
- Keep any date in the fact text.

PREFERENCES rules:
- Output one preference per line. No numbering, no bullets, no preamble.
- Extract the user's stated preferences, interests, constraints, and current setup,
  with specifics (exact brands, genres, tools, activities, named topics).
- Capture both explicit preferences ("I like X") and revealed preferences supported
  by the conversation (the user asked for resources on Y, attended Z, is building W).
- Prefer normalized, category-bearing wording when the category is clear.
- Distinguish durable standing preferences from one-off task requests.
- Keep any date or context needed to scope the preference.
- Do NOT invent preferences not explicitly supported by the conversation.

Conversation date: {date}

Conversation:
{session}
"""


class ExtractedMemories(NamedTuple):
    facts: list[str]
    preferences: list[str]


def _clean_line(line: str) -> str:
    return line.strip().lstrip("-*0123456789. ").strip()


def _run_extract_completion(
    client: OpenAI,
    model: str,
    prompt: str,
    provider: str = "openrouter",
    max_retries: int = 3,
) -> str:
    for attempt in range(max_retries):
        try:
            if provider == "openai":
                resp = client.responses.create(model=model, input=prompt, max_output_tokens=700)
                return resp.output_text
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=700,
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                print(f"  extract failed: {e}")
                return ""
    return ""


def _parse_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = _clean_line(line)
        if not line or line.upper() == "NONE":
            continue
        lines.append(line)
    return lines


def _parse_combined_sections(text: str) -> ExtractedMemories:
    if not text:
        return ExtractedMemories([], [])

    sections = {"facts": [], "preferences": []}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper == "FACTS:":
            current = "facts"
            continue
        if upper == "PREFERENCES:":
            current = "preferences"
            continue
        if current is None:
            continue
        cleaned = _clean_line(line)
        if not cleaned or cleaned.upper() == "NONE":
            continue
        sections[current].append(cleaned)

    return ExtractedMemories(sections["facts"], sections["preferences"])


def extract_session_memories(
    client: OpenAI,
    model: str,
    session_text: str,
    session_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> ExtractedMemories:
    """Extract benchmark facts and preference memories from one session."""
    use_pref = os.getenv("MEMDIO_PREF_V3") == "1"
    if use_pref:
        prompt = COMBINED_EXTRACT_PROMPT.format(date=session_date or "unknown", session=session_text)
        text = _run_extract_completion(client, model, prompt, provider=provider, max_retries=max_retries)
        return _parse_combined_sections(text)

    prompt = EXTRACT_PROMPT.format(date=session_date or "unknown", session=session_text)
    text = _run_extract_completion(client, model, prompt, provider=provider, max_retries=max_retries)
    return ExtractedMemories(_parse_lines(text), [])


def extract_facts(
    client: OpenAI,
    model: str,
    session_text: str,
    session_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> list[str]:
    """Extract a list of atomic user facts from one session. Empty list on failure."""
    return extract_session_memories(
        client,
        model,
        session_text,
        session_date,
        provider=provider,
        max_retries=max_retries,
    ).facts


def extract_preferences(
    client: OpenAI,
    model: str,
    session_text: str,
    session_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> list[str]:
    return extract_session_memories(
        client,
        model,
        session_text,
        session_date,
        provider=provider,
        max_retries=max_retries,
    ).preferences


def _parse_facts(text: str) -> list[str]:
    return _parse_lines(text)


def extraction_enabled() -> bool:
    return os.getenv("MEMDIO_EXTRACT") == "1"


def extract_model() -> str:
    return os.getenv("MEMDIO_EXTRACT_MODEL", "google/gemini-2.5-flash")
