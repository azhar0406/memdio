"""Generate answers using LLM provider APIs."""

import time

from openai import OpenAI

from benchmarks.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
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
    provider: str = "openrouter",
    max_retries: int = 3,
) -> str:
    """Generate an answer using the specified model/provider."""
    prompt = ANSWER_PROMPT.format(
        context=context,
        question=question,
        question_date=question_date,
    )

    for attempt in range(max_retries):
        try:
            if provider == "openai":
                response = client.responses.create(
                    model=model,
                    input=prompt,
                    max_output_tokens=512,
                )
                return response.output_text.strip()
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=512,
                )
                return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                return f"ERROR: {e}"
