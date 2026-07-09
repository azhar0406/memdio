"""Ingest-time fact extraction: turn raw conversation sessions into atomic,
dated, self-contained facts about the user. Each fact becomes its own memory,
so multi-session aggregation ("how many X", "total Y") reduces to retrieving
and counting discrete facts instead of re-reading raw session dumps.
"""

import os
import re
import time

from openai import OpenAI

_EVENTDATE_PREFIX_RE = re.compile(
    r"^event_date=(?P<event_date>\d{4}-\d{2}-\d{2}|UNKNOWN)\s*\|\s*(?P<fact>.+)$"
)

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


EXTRACT_PROMPT_V3 = """Extract atomic FACTS about the USER from the conversation below.

Rules:
- Output one fact per line. No numbering, no bullets, no preamble.
- Extract EVERY concrete user instance that could matter later for counting, ordering,
  totals, ownership, attendance, purchases, service usage, completion, or updates,
  even if it appears only as a side comment.
- Prefer normalized, category-bearing wording when the category is clear:
  "The user used the food delivery service Domino's Pizza."
  "The user bought the model kit Revell F-15 Eagle."
  "The user visited the Science Museum."
- Keep exact names, quantities, prices, weights, dates, and version/update words.
- If a later line in the same session updates or supersedes an earlier state, emit
  the latest state and the concrete new event if both matter.
- Do not invent categories or facts that are not explicitly supported by the conversation.
- Keep notable assistant recommendations/resources as separate facts when they are
  specific and named.
- If no durable facts exist, output NONE.

Conversation date: {date}

Conversation:
{session}

Facts (one per line):"""


EXTRACT_PROMPT_EVENTDATE_V3 = """Extract atomic FACTS about the USER from the conversation below.

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
- Prefix every fact with `event_date=YYYY-MM-DD | ` when the event date is clear.
- Use the conversation date to resolve relative time phrases like "yesterday",
  "last Tuesday", or "two weeks ago".
- If the event date is unclear, prefix the fact with `event_date=UNKNOWN | `.
- Do not invent dates.
- Keep any date in the fact text.
- If the conversation contains no durable facts, output the single word: NONE

Conversation date: {date}

Conversation:
{session}

Facts (one per line):"""


def _select_prompt_template() -> str:
    if os.getenv("MEMDIO_EVENTDATE_V3") == "1":
        return EXTRACT_PROMPT_EVENTDATE_V3
    if os.getenv("MEMDIO_EXTRACT_V3") == "1":
        return EXTRACT_PROMPT_V3
    return EXTRACT_PROMPT


def _build_prompt(session_text: str, session_date: str = "") -> str:
    return _select_prompt_template().format(date=session_date or "unknown", session=session_text)


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


def extract_fact_records(
    client: OpenAI,
    model: str,
    session_text: str,
    session_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> list[tuple[str, str | None]]:
    """Extract fact records as (clean_text, event_date)."""
    prompt = _build_prompt(session_text, session_date)
    text = _run_extract_completion(client, model, prompt, provider=provider, max_retries=max_retries)
    return _parse_fact_records(text)


def extract_facts(
    client: OpenAI,
    model: str,
    session_text: str,
    session_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> list[str]:
    """Extract a list of atomic user facts from one session. Empty list on failure."""
    records = extract_fact_records(
        client,
        model,
        session_text,
        session_date,
        provider=provider,
        max_retries=max_retries,
    )
    return [fact for fact, _event_date in records]


def _clean_fact_line(line: str) -> str:
    return line.strip().lstrip("-*0123456789. ").strip()


def _parse_facts(text: str) -> list[str]:
    if not text:
        return []
    facts = []
    for line in text.splitlines():
        line = _clean_fact_line(line)
        if not line or line.upper() == "NONE":
            continue
        facts.append(line)
    return facts


def _parse_fact_records(text: str) -> list[tuple[str, str | None]]:
    if not text:
        return []
    facts = []
    for line in text.splitlines():
        line = _clean_fact_line(line)
        if not line or line.upper() == "NONE":
            continue
        match = _EVENTDATE_PREFIX_RE.match(line)
        if not match:
            facts.append((line, None))
            continue
        event_date = match.group("event_date")
        facts.append((match.group("fact").strip(), None if event_date == "UNKNOWN" else event_date))
    return facts


def extraction_enabled() -> bool:
    return os.getenv("MEMDIO_EXTRACT") == "1"


def extract_model() -> str:
    return os.getenv("MEMDIO_EXTRACT_MODEL", "google/gemini-2.5-flash")
