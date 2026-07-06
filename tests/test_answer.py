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
