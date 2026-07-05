"""Optional default LLM client for fact extraction.

Builds a provider-agnostic ``llm(prompt) -> str`` callable from environment
credentials so ``StorageManager.remember(..., llm=default_llm())`` works out of
the box. Returns ``None`` when no credentials (or the ``openai`` package) are
available — callers then fall back to plain, extraction-free storage.

Env:
  OPENROUTER_API_KEY  -> OpenRouter (default model google/gemini-2.5-flash)
  OPENAI_API_KEY      -> OpenAI     (default model gpt-4o-mini)
  MEMDIO_EXTRACT_MODEL -> override the model id
"""

from __future__ import annotations

import os
from collections.abc import Callable


def default_llm(model: str | None = None) -> Callable[[str], str] | None:
    """Return an ``llm(prompt) -> str`` callable, or ``None`` if unavailable."""
    try:
        from openai import OpenAI
    except ImportError:
        return None

    or_key = os.environ.get("OPENROUTER_API_KEY")
    oa_key = os.environ.get("OPENAI_API_KEY")
    if or_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key)
        model = model or os.environ.get("MEMDIO_EXTRACT_MODEL", "google/gemini-2.5-flash")
    elif oa_key:
        client = OpenAI(api_key=oa_key)
        model = model or os.environ.get("MEMDIO_EXTRACT_MODEL", "gpt-4o-mini")
    else:
        return None

    def llm(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000,
        )
        return resp.choices[0].message.content or ""

    return llm
