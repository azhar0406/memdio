from types import SimpleNamespace

from benchmarks.longmemeval import extract
from benchmarks.longmemeval.ingest import cleanup_question_db, ingest_question


def test_parse_facts_strips_prefixes_and_drops_none():
    text = """
    1. The user bought feed.
    - The user has three hens.
    *
    NONE

    """

    assert extract._parse_facts(text) == [
        "The user bought feed.",
        "The user has three hens.",
    ]


def test_parse_facts_returns_multiline_fact_block():
    text = """
    The user attended a farmers market on 2026-07-01.
    2. The user bought a 20-pound bag of layer feed.
    * The user built a Tamiya 1/48 Spitfire model kit.
    """

    assert extract._parse_facts(text) == [
        "The user attended a farmers market on 2026-07-01.",
        "The user bought a 20-pound bag of layer feed.",
        "The user built a Tamiya 1/48 Spitfire model kit.",
    ]


def test_extract_facts_returns_parsed_lines_from_fake_client():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="1. The user bought feed.\n- The user owns three hens.\n"
                )
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )

    facts = extract.extract_facts(
        client=client,
        model="fake-model",
        session_text="session",
        session_date="2026-07-05",
    )

    assert facts == ["The user bought feed.", "The user owns three hens."]


def test_extract_facts_returns_empty_list_when_client_keeps_failing(monkeypatch):
    attempts = {"count": 0}

    def fail_create(**_kwargs):
        attempts["count"] += 1
        raise RuntimeError("boom")

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fail_create)
        )
    )
    monkeypatch.setattr(extract.time, "sleep", lambda _seconds: None)

    facts = extract.extract_facts(
        client=client,
        model="fake-model",
        session_text="session",
        max_retries=3,
    )

    assert facts == []
    assert attempts["count"] == 3


def test_extraction_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("MEMDIO_EXTRACT", "1")
    assert extract.extraction_enabled() is True

    monkeypatch.setenv("MEMDIO_EXTRACT", "0")
    assert extract.extraction_enabled() is False


def test_extract_model_reads_env_with_default(monkeypatch):
    monkeypatch.delenv("MEMDIO_EXTRACT_MODEL", raising=False)
    assert extract.extract_model() == "google/gemini-2.5-flash"

    monkeypatch.setenv("MEMDIO_EXTRACT_MODEL", "custom/model")
    assert extract.extract_model() == "custom/model"


def test_extract_session_memories_uses_combined_pref_prompt_when_flag_set(monkeypatch):
    monkeypatch.setenv("MEMDIO_PREF_V3", "1")

    captured_prompt = []

    def fake_create(**kwargs):
        captured_prompt.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="FACTS:\nNONE\n\nPREFERENCES:\nNONE"))]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    memories = extract.extract_session_memories(
        client=client,
        model="fake-model",
        session_text="test session",
        session_date="2026-07-08",
    )

    assert memories.facts == []
    assert memories.preferences == []
    assert len(captured_prompt) == 1
    assert "Return EXACTLY two sections" in captured_prompt[0]
    assert "FACTS:" in captured_prompt[0]
    assert "PREFERENCES:" in captured_prompt[0]


def test_extract_session_memories_parses_fact_and_preference_sections(monkeypatch):
    monkeypatch.setenv("MEMDIO_PREF_V3", "1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        "FACTS:\n"
                        "1. The user bought a Tamiya Spitfire kit.\n"
                        "\n"
                        "PREFERENCES:\n"
                        "- The user prefers history podcasts for commute listening.\n"
                    )
                )
            )
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
    )

    memories = extract.extract_session_memories(
        client=client,
        model="fake-model",
        session_text="session",
        session_date="2026-07-08",
    )

    assert memories.facts == ["The user bought a Tamiya Spitfire kit."]
    assert memories.preferences == ["The user prefers history podcasts for commute listening."]


def test_ingest_question_stores_preference_profile_memories():
    question = {
        "question_id": "q_pref",
        "haystack_sessions": [[{"role": "user", "content": "I want history podcasts for my commute."}]],
        "haystack_dates": ["2026/07/08 (Wed) 10:00"],
    }

    extractor_result = extract.ExtractedMemories(
        facts=["The user asked for commute listening recommendations."],
        preferences=["The user prefers history podcasts for commute listening."],
    )

    storage, db_dir = ingest_question(question, extractor=lambda _text, _date: extractor_result)
    try:
        preferences = storage.search_by_tag("preference")
        assert len(preferences) == 1
        assert preferences[0]["content"] == (
            "[2026/07/08 (Wed) 10:00] The user prefers history podcasts for commute listening."
        )
    finally:
        storage.close()
        cleanup_question_db(db_dir)
