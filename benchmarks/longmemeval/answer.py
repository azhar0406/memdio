"""Generate answers using LLM provider APIs."""

import os
import re
import time

from openai import OpenAI

from benchmarks.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)


_TEMPORAL_PATTERNS = re.compile(
    # Bare 'before'/'after'/'order' are deliberately excluded — they appear in
    # the narrative text of ordinary recall questions and misroute them.
    r"when did|how long ago|what date|what day|which month|which year"
    r"|last (?:week|month|year)|in (?:january|february|march|april|may|june"
    r"|july|august|september|october|november|december)"
    r"|\bfirst\b|\blast\b|\bearliest\b|\blatest\b|\bmost recent(?:ly)?\b|\bwhat order\b",
    re.IGNORECASE,
)

_AGG_PATTERNS = re.compile(
    r"\bhow many\b|\bhow much\b|\bhow often\b|\bnumber of\b|\btotal\b|\bcount\b"
    r"|\bhow frequently\b|\btimes\b",
    re.IGNORECASE,
)

_PREFERENCE_PATTERNS = re.compile(
    # 'recommended'/'suggested' (past tense) are recall questions about a prior
    # recommendation, not advice requests — deliberately not matched.
    r"\brecommend(?:ations?)?\b|\bsuggest(?:ions?)?\b|\bideas for\b|\bwhat should i\b"
    r"|\bcan you give me\b|\btips\b|\badvice\b|\bhelp me (?:choose|pick|plan)\b",
    re.IGNORECASE,
)


def get_client(provider: str = "openrouter") -> OpenAI:
    """Create a client for the selected provider."""
    if provider == "openai":
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set. Add it to .env file or environment.")
        kwargs = {"api_key": OPENAI_API_KEY}
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        return OpenAI(**kwargs)

    if provider != "openrouter":
        raise ValueError(f"Unsupported provider: {provider}")
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set. Add it to .env file.")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )


ANSWER_PROMPT = """You are a helpful assistant with access to a user's conversation history stored as memories.
Use the retrieved memories below to answer the user's question accurately.

## Retrieved Memories
{context}

## Question
{question}

## Question Date
This question was asked on: {question_date}

## Instructions
- Read ALL retrieved memories carefully before answering. The answer may require combining details across multiple memories.
- Extract specific facts (names, dates, numbers, preferences, events) directly from the memories — they are often present but embedded inside a longer memory.
- Answer based only on the memories, but make a genuine effort to locate the answer before giving up.
- Only respond "I don't have enough information to answer this question" if the answer is genuinely absent from every memory.
- For temporal questions, compute dates/durations from the dated memories (each memory shows its date).
- If information was updated/corrected in a later conversation, use the most recent version.
- Be concise and direct."""


PREFERENCE_PROMPT = """You are a helpful assistant with access to a user's conversation history stored as memories.
Use the retrieved memories below to answer the user's question accurately.

## Retrieved Memories
{context}

## Question
{question}

## Question Date
This question was asked on: {question_date}

## Instructions
This is a recommendation/advice request.
- Step 1: From the memories, identify the user's stated preferences, interests, constraints, and current setup relevant to the topic — be SPECIFIC (exact brands, genres, tools, activities they mentioned).
- Step 2: Give concrete recommendations, tying EACH one explicitly to a specific preference or experience from the memories (e.g. "since you mentioned X, ...").
- Ground the preferences in the memories, but you may draw on general knowledge for the recommendations themselves.
- Do NOT give generic recommendations that ignore the user's stated specifics — tailoring to their exact stated interests is the whole point.
- If the memories reveal the user's interests in this topic area, you MUST provide recommendations based on them rather than declining.
- Only respond "I don't have enough information to answer this question" if the memories genuinely contain nothing about the topic.
- Be concise and direct."""


PREFERENCE_PROMPT_V3 = """You are a helpful assistant with access to a user's conversation history stored as memories.
Use the retrieved memories below to answer the user's question accurately.

## User Preference Profile
{preference_profile}

## Retrieved Memories
{context}

## Question
{question}

## Question Date
This question was asked on: {question_date}

## Instructions
This is a recommendation/advice request.
- First, read the preference profile and identify the user's stated preferences, interests, constraints, and current setup relevant to the topic.
- You MUST tailor every recommendation to the profile and the retrieved memories. Do not give generic advice.
- Tie EACH recommendation explicitly to a specific preference, interest, constraint, or prior experience from the profile or memories.
- Ground the preferences in stored memories, but you may draw on general knowledge for the recommendations themselves.
- If the profile or memories contain on-topic preferences, you MUST provide recommendations based on them rather than declining.
- Only respond "I don't have enough information to answer this question" if the profile and memories genuinely contain nothing about the topic.
- Be concise and direct."""


AGGREGATION_PROMPT = """You are a helpful assistant with access to a user's conversation history stored as memories.
Use the retrieved memories below to answer the user's question accurately.

## Retrieved Memories
{context}

## Question
{question}

## Question Date
This question was asked on: {question_date}

## Instructions
This question requires aggregation/counting.
- Step 1: Enumerate every distinct item/event with its date as a numbered list.
- Step 2: Dedupe. The same item often appears in multiple memories under slightly different descriptions — collapse entries that refer to the same physical item/event (same size, purpose, or occasion) into one.
- Step 3: A memory stating an explicit current total/count SUPERSEDES all earlier counts. Items added in the same conversation as (or before) that stated total are ALREADY INCLUDED in it — do not add them again.
- Step 4: Items added/acquired in conversations dated AFTER the latest stated total must be ADDED to it. Separate purchases/events with no stated running total are summed.
- Step 5: State the final count/total clearly.
- Only respond "I don't have enough information to answer this question" if the answer is genuinely absent from every memory."""


TEMPORAL_PROMPT = """You are a helpful assistant with access to a user's conversation history stored as memories.
Use the retrieved memories below to answer the user's question accurately.

## Retrieved Memories
{context}

## Question
{question}

## Question Date
This question was asked on: {question_date}

## Instructions
This question requires temporal reasoning.
- Step 1: Extract each relevant event with its ABSOLUTE date. Resolve relative dates using the memory's date header.
- Step 2: Sort the events chronologically.
- Step 3: Compare/compute carefully. Earlier means the smaller date.
- Step 4: Answer with the final result.
- If information was updated/corrected in a later conversation, use the most recent version.
- Only respond "I don't have enough information to answer this question" if the answer is genuinely absent from every memory."""


EXPAND_PROMPT = """List up to 12 short search terms that might appear in past conversations relevant to this question: specific instance names, well-known brand names, synonyms, or closely related words. For category words (e.g. "food delivery services"), list likely concrete instances broadly — including adjacent kinds (e.g. Domino's, Uber Eats, DoorDash, Grubhub, pizza, takeout). One term per line, no numbering, no explanations.

Question: {question}"""


def expand_query(
    client: OpenAI,
    model: str,
    question: str,
    provider: str = "openrouter",
    max_retries: int = 2,
) -> list[str]:
    """LLM query expansion for exhaustive retrieval — returns instance/synonym
    terms for category questions. Empty list on any failure (never blocks)."""
    prompt = EXPAND_PROMPT.format(question=question)
    for attempt in range(max_retries):
        try:
            if provider == "openai":
                response = client.responses.create(model=model, input=prompt, max_output_tokens=120)
                text = response.output_text
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=120,
                )
                text = response.choices[0].message.content or ""
            terms = [t.strip().strip("-•*").strip() for t in text.splitlines()]
            return [t for t in terms if 2 < len(t) < 40][:12]
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2)
    return []


DISTILL_PROMPT = """You are extracting facts from a user's conversation history to help answer a question.

## Question
{question}

## Question Date
{question_date}

## Retrieved Memories
{context}

## Task
Extract EVERY fact from the memories that could be relevant to answering the question.
- Output a concise bulleted list of specific facts (names, dates, numbers, events, preferences).
- Include the date of each fact when available.
- If answering requires combining information across multiple memories, extract all the pieces.
- If a fact was later updated or corrected, keep the most recent version and note the change.
- Do NOT answer the question — only extract relevant facts.
- If no relevant facts exist, output "NO RELEVANT FACTS"."""


def classify_question(question: str) -> str:
    """Classify a question using lexical heuristics only."""
    if _AGG_PATTERNS.search(question):
        return "aggregation"
    if _TEMPORAL_PATTERNS.search(question):
        return "temporal"
    if _PREFERENCE_PATTERNS.search(question):
        return "preference"
    return "detail"


def build_preference_profile(memories: list[dict]) -> str:
    lines = []
    seen = set()
    for memory in memories:
        content = (memory.get("content") or "").strip()
        if not content or content in seen:
            continue
        seen.add(content)
        lines.append(f"- {content}")
    if not lines:
        return "- No stored preference profile found."
    return "\n".join(lines)


def _build_answer_prompt(
    question: str,
    context: str,
    question_date: str = "",
    preference_profile: str = "",
) -> tuple[str, int]:
    if os.getenv("MEMDIO_PROMPT_V2") != "1":
        return (
            ANSWER_PROMPT.format(
                context=context,
                question=question,
                question_date=question_date,
            ),
            512,
        )

    question_class = classify_question(question)
    prompt_by_class = {
        "preference": PREFERENCE_PROMPT,
        "aggregation": AGGREGATION_PROMPT,
        "temporal": TEMPORAL_PROMPT,
        "detail": ANSWER_PROMPT,
    }

    if os.getenv("MEMDIO_PREF_V3") == "1" and question_class == "preference":
        return (
            PREFERENCE_PROMPT_V3.format(
                preference_profile=preference_profile or "- No stored preference profile found.",
                context=context,
                question=question,
                question_date=question_date,
            ),
            512,
        )

    max_tokens = 800 if question_class in ("aggregation", "temporal") else 512
    return (
        prompt_by_class[question_class].format(
            context=context,
            question=question,
            question_date=question_date,
        ),
        max_tokens,
    )


def distill_context(
    client: OpenAI,
    model: str,
    question: str,
    context: str,
    question_date: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> str:
    """Condense retrieved memories into the facts relevant to the question.

    A cheap LLM pass that turns a large, noisy raw-session context into a short
    fact list, so the answering model reasons over clean evidence. Falls back to
    the original context on failure.
    """
    prompt = DISTILL_PROMPT.format(question=question, question_date=question_date, context=context)
    for attempt in range(max_retries):
        try:
            if provider == "openai":
                response = client.responses.create(model=model, input=prompt, max_output_tokens=800)
                return response.output_text.strip()
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=800,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
            else:
                print(f"  Distill failed, using raw context: {e}")
                return context
    return context


def generate_answer(
    client: OpenAI,
    model: str,
    question: str,
    context: str,
    question_date: str = "",
    preference_profile: str = "",
    provider: str = "openrouter",
    max_retries: int = 3,
) -> str:
    """Generate an answer using the specified model/provider."""
    prompt, max_tokens = _build_answer_prompt(
        question,
        context,
        question_date,
        preference_profile=preference_profile,
    )

    for attempt in range(max_retries):
        try:
            if provider == "openai":
                response = client.responses.create(
                    model=model,
                    input=prompt,
                    max_output_tokens=max_tokens,
                )
                return response.output_text.strip()
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                return f"ERROR: {e}"
