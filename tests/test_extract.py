from types import SimpleNamespace

from benchmarks.longmemeval import extract


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
