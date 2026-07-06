"""Evaluate answers using an LLM judge — matches LongMemEval protocol.

Ported from: https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py
Uses task-specific prompts for each of the 6 question types.
"""

import time

from openai import OpenAI

from benchmarks.config import (
    JUDGE_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)

# ---------- Task-specific evaluation prompts (from LongMemEval) ----------

EVAL_PROMPTS = {
    "single-session-user": """Decide if the following response correctly answers the question based on the reference answer.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response contain the correct answer? Answer with only "yes" or "no".""",

    "single-session-assistant": """Decide if the following response correctly answers the question based on the reference answer.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response contain the correct answer? Answer with only "yes" or "no".""",

    "single-session-preference": """Decide if the following response correctly answers the question about user preferences based on the reference answer.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response correctly reflect the user's preference as described in the reference answer? Answer with only "yes" or "no".""",

    "multi-session": """Decide if the following response correctly answers the question based on the reference answer. The question requires synthesizing information from multiple conversation sessions.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response contain the correct answer with all necessary information? Answer with only "yes" or "no".""",

    "temporal-reasoning": """Decide if the following response correctly answers the temporal reasoning question based on the reference answer. Note: for questions about "how long ago" or specific dates, an off-by-one error in days, weeks, or months is acceptable.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response contain the correct answer (allowing for minor off-by-one temporal errors)? Answer with only "yes" or "no".""",

    "knowledge-update": """Decide if the following response correctly answers the question based on the reference answer. The reference answer reflects the most up-to-date information after a knowledge update/correction occurred in the conversation history.
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response contain the correct (updated) answer? Answer with only "yes" or "no".""",
}

# Abstention questions: the correct answer is to decline/say unknown
ABSTENTION_PROMPT = """Decide if the following response correctly abstains from answering (i.e., indicates it doesn't know or cannot answer).
Question: {question}
Reference answer: {answer}
Response: {hypothesis}

Does the response correctly indicate that it cannot answer or doesn't have the information? Answer with only "yes" or "no"."""


def get_judge_client(provider: str = "openrouter") -> OpenAI:
    """Create client for judging."""
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


def evaluate_single(
    client: OpenAI,
    question_id: str,
    question_type: str,
    question: str,
    reference_answer: str,
    hypothesis: str,
    provider: str = "openrouter",
    judge_model: str = JUDGE_MODEL,
    max_retries: int = 3,
) -> dict:
    """Evaluate a single answer against reference. Returns dict with label."""
    is_abstention = "_abs" in question_id

    if is_abstention:
        prompt = ABSTENTION_PROMPT.format(
            question=question,
            answer=reference_answer,
            hypothesis=hypothesis,
        )
    else:
        template = EVAL_PROMPTS.get(question_type, EVAL_PROMPTS["single-session-user"])
        prompt = template.format(
            question=question,
            answer=reference_answer,
            hypothesis=hypothesis,
        )

    for attempt in range(max_retries):
        try:
            if provider == "openai":
                response = client.responses.create(
                    model=judge_model,
                    input=prompt,
                    max_output_tokens=10,
                )
                raw = response.output_text.strip().lower()
            else:
                response = client.chat.completions.create(
                    model=judge_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=10,
                )
                raw = response.choices[0].message.content.strip().lower()
            label = raw.startswith("yes")
            return {
                "question_id": question_id,
                "question_type": question_type,
                "is_abstention": is_abstention,
                "label": label,
                "judge_raw": raw,
            }
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  Judge retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
            else:
                return {
                    "question_id": question_id,
                    "question_type": question_type,
                    "is_abstention": is_abstention,
                    "label": False,
                    "judge_raw": f"ERROR: {e}",
                }
