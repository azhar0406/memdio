from types import SimpleNamespace

from benchmarks.longmemeval import answer


def test_classify_question_routes_preference():
    assert answer.classify_question("Can you recommend some resources for video editing?") == "preference"
    assert answer.classify_question("Do you have any suggestions for my trip?") == "preference"


def test_classify_question_routes_counting_before_preference():
    question = "Can you give me the total weight of the feed I bought?"

    assert answer.classify_question(question) == "aggregation"


def test_classify_question_routes_ordering_as_temporal():
    question = "What is the order of airlines I flew with from earliest to latest?"

    assert answer.classify_question(question) == "temporal"


def test_generate_answer_preserves_old_prompt_when_v2_unset(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="old prompt answer")
                )
            ]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )
    monkeypatch.delenv("MEMDIO_PROMPT_V2", raising=False)

    result = answer.generate_answer(
        client=client,
        model="fake-model",
        question="Can you recommend a book?",
        context="memory context",
        question_date="2026/07/06 (Mon) 12:00",
    )

    assert result == "old prompt answer"
    assert captured["max_tokens"] == 512
    assert captured["messages"] == [
        {
            "role": "user",
            "content": answer.ANSWER_PROMPT.format(
                context="memory context",
                question="Can you recommend a book?",
                question_date="2026/07/06 (Mon) 12:00",
            ),
        }
    ]


def test_build_preference_profile_dedupes_memories():
    profile = answer.build_preference_profile(
        [
            {"content": "[2026/07/08 (Wed) 10:00] The user prefers history podcasts."},
            {"content": "[2026/07/08 (Wed) 10:00] The user prefers history podcasts."},
            {"content": "[2026/07/09 (Thu) 10:00] The user uses Premiere Pro."},
        ]
    )

    assert profile == (
        "- [2026/07/08 (Wed) 10:00] The user prefers history podcasts.\n"
        "- [2026/07/09 (Thu) 10:00] The user uses Premiere Pro."
    )


def test_generate_answer_uses_preference_prompt_v3_when_flag_set(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="tailored answer"))]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setenv("MEMDIO_PROMPT_V2", "1")
    monkeypatch.setenv("MEMDIO_PREF_V3", "1")

    result = answer.generate_answer(
        client=client,
        model="fake-model",
        question="Can you recommend some podcasts for my commute?",
        context="retrieved memory context",
        question_date="2026/07/06 (Mon) 12:00",
        preference_profile="- The user prefers history and science podcasts.",
    )

    assert result == "tailored answer"
    assert captured["max_tokens"] == 512
    prompt = captured["messages"][0]["content"]
    assert "## User Preference Profile" in prompt
    assert "- The user prefers history and science podcasts." in prompt
    assert "You MUST tailor every recommendation to the profile" in prompt
