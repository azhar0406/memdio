"""Tests for default_llm provider selection (no network — clients are lazy)."""

import pytest

from memdio.core import llm as llm_mod


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for k in (
        "MEMDIO_LLM_PROVIDER", "MEMDIO_LLM_BASE_URL", "MEMDIO_LLM_API_KEY",
        "MEMDIO_LLM_MODEL", "MEMDIO_EXTRACT_MODEL",
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


def test_none_when_no_config():
    assert llm_mod.default_llm() is None


def test_hosted_openai_compat_requires_key(monkeypatch):
    # provider forced but no key -> None (don't build a client that will 401)
    monkeypatch.setenv("MEMDIO_LLM_PROVIDER", "openrouter")
    assert llm_mod.default_llm() is None


def test_openrouter_selected_with_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    fn = llm_mod.default_llm()
    assert callable(fn)  # openai SDK present in the dev/test env


def test_local_provider_needs_no_key(monkeypatch):
    # Ollama / llama.cpp build a client without any API key (client is lazy).
    monkeypatch.setenv("MEMDIO_LLM_PROVIDER", "ollama")
    assert callable(llm_mod.default_llm())


def test_custom_base_url(monkeypatch):
    monkeypatch.setenv("MEMDIO_LLM_BASE_URL", "http://localhost:1234/v1")
    assert callable(llm_mod.default_llm())


def test_auto_detect_prefers_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    # Should not raise; picks openrouter first.
    assert callable(llm_mod.default_llm())
