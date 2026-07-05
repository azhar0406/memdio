"""Optional default LLM client for fact extraction — multi-provider.

Builds a provider-agnostic ``llm(prompt) -> str`` callable so
``StorageManager.remember(..., llm=default_llm())`` works out of the box.
Returns ``None`` when no credentials (or the needed SDK) are available — callers
then fall back to plain, extraction-free storage.

Supported providers (select via ``MEMDIO_LLM_PROVIDER``, or auto-detected from
whichever API key is set):

  openai       OpenAI            OPENAI_API_KEY            (needs `openai`)
  openrouter   OpenRouter        OPENROUTER_API_KEY        (needs `openai`)
  anthropic    Claude (native)   ANTHROPIC_API_KEY         (needs `anthropic`)
  ollama       Ollama (local)    http://localhost:11434/v1 (needs `openai`)
  llamacpp     llama.cpp (local) http://localhost:8080/v1  (needs `openai`)

OpenAI, OpenRouter, Ollama and llama.cpp all speak the OpenAI chat API, so they
share one client; Claude uses the official Anthropic SDK. Any other
OpenAI-compatible server works via ``MEMDIO_LLM_BASE_URL`` (+ optional
``MEMDIO_LLM_API_KEY``).

Env overrides:
  MEMDIO_LLM_PROVIDER   force a provider (else auto-detect)
  MEMDIO_LLM_BASE_URL   custom OpenAI-compatible endpoint
  MEMDIO_LLM_API_KEY    key for a custom/local endpoint (default: a dummy key)
  MEMDIO_EXTRACT_MODEL / MEMDIO_LLM_MODEL   override the model id
"""

from __future__ import annotations

import os
from collections.abc import Callable

# provider -> (base_url, api_key_env or None, default_model)
_OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "google/gemini-2.5-flash"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1"),
    "llamacpp": ("http://localhost:8080/v1", None, "local-model"),
}


def default_llm(model: str | None = None) -> Callable[[str], str] | None:
    """Return an ``llm(prompt) -> str`` callable, or ``None`` if unavailable."""
    provider = os.environ.get("MEMDIO_LLM_PROVIDER", "").strip().lower()
    base_url = os.environ.get("MEMDIO_LLM_BASE_URL")
    model = model or os.environ.get("MEMDIO_EXTRACT_MODEL") or os.environ.get("MEMDIO_LLM_MODEL")

    # Auto-detect the provider from whichever key is set (only when nothing forced).
    if not provider and not base_url:
        if os.environ.get("OPENROUTER_API_KEY"):
            provider = "openrouter"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            return None

    if provider in ("anthropic", "claude"):
        return _anthropic_llm(model)
    return _openai_compatible_llm(provider, base_url, model)


def _anthropic_llm(model: str | None) -> Callable[[str], str] | None:
    """Native Anthropic SDK (Claude). Cheap/fast default: claude-haiku-4-5."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    client = Anthropic(api_key=key)
    model = model or "claude-haiku-4-5"

    def llm(prompt: str) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    return llm


def _openai_compatible_llm(
    provider: str, base_url: str | None, model: str | None
) -> Callable[[str], str] | None:
    """OpenAI / OpenRouter / Ollama / llama.cpp / any OpenAI-compatible endpoint."""
    try:
        from openai import OpenAI
    except ImportError:
        return None

    if base_url:
        # Custom OpenAI-compatible server.
        api_key = os.environ.get("MEMDIO_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "sk-no-key"
    elif provider in _OPENAI_COMPAT:
        base_url, key_env, default_model = _OPENAI_COMPAT[provider]
        model = model or default_model
        if key_env:
            api_key = os.environ.get(key_env)
            if not api_key:
                return None  # a real key is required for hosted providers
        else:
            api_key = os.environ.get("MEMDIO_LLM_API_KEY") or "sk-no-key"  # local: key ignored
    else:
        return None

    model = model or "gpt-4o-mini"
    client = OpenAI(base_url=base_url, api_key=api_key)

    def llm(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000,
        )
        return resp.choices[0].message.content or ""

    return llm
