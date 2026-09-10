"""Targeted benchmark selection and CLI wiring without LLM calls."""

import pytest

from benchmarks.longmemeval import run


@pytest.fixture
def dataset():
    return [
        {"question_id": "3", "question_type": "single-session-preference"},
        {"question_id": "1", "question_type": "multi-session"},
        {"question_id": "4", "question_type": "temporal-reasoning"},
        {"question_id": "2", "question_type": "single-session-preference"},
    ]


@pytest.mark.parametrize("types", [None, []])
def test_filter_noop(dataset, types):
    assert run.filter_by_types(dataset, types) is dataset


def test_filter_exact_types_preserves_order(dataset):
    assert run.filter_by_types(dataset, ["single-session-preference"]) == [dataset[0], dataset[3]]
    assert run.filter_by_types(dataset, ["multi-session", "single-session-preference"]) == [
        dataset[0], dataset[1], dataset[3],
    ]
    assert run.filter_by_types(dataset, ["single-session", "unknown"]) == []
    assert run.filter_by_types(dataset, ["Single-session-preference"]) == []


def test_stratified_sample_after_filter(dataset):
    filtered = run.filter_by_types(dataset, ["single-session-preference", "multi-session"])
    sampled = run.stratified_sample(filtered, 2, seed=123)
    assert len(sampled) == 2
    assert {q["question_type"] for q in sampled} == {"single-session-preference", "multi-session"}
    assert all(q in filtered for q in sampled)
    assert sampled == run.stratified_sample(filtered, 2, seed=123)


@pytest.mark.parametrize("selection", [{"limit": 1}, {"stratified": 1, "seed": 123}])
def test_runner_filters_before_selection(dataset, monkeypatch, capsys, selection):
    monkeypatch.setattr(run, "load_dataset", lambda: dataset)
    monkeypatch.setattr(run, "load_checkpoint", lambda *args: ({}, []))
    monkeypatch.setattr(run, "get_client", lambda *args: None)
    monkeypatch.setattr(run, "get_judge_client", lambda *args: None)
    monkeypatch.setattr(run, "process_question", lambda q, *args: {**q, "label": True})
    monkeypatch.setattr(run, "save_checkpoint", lambda *args: None)
    monkeypatch.setattr(run, "save_results", lambda *args: None)
    monkeypatch.setattr(run, "print_report", lambda *args, **kwargs: None)
    results = run.run_benchmark("fake", "test", workers=1,
                                question_types=["multi-session"], **selection)
    assert [r["question_id"] for r in results] == ["1"]
    assert "Filtered to 1 questions of types: multi-session" in capsys.readouterr().out


@pytest.mark.parametrize("flags, expected", [([], "12345678"),
                                           (["--resume", "old"], "old"),
                                           (["--run-id", "new"], "new"),
                                           (["--resume", "old", "--run-id", "new"], "new")])
def test_cli_run_id_precedence(monkeypatch, flags, expected):
    calls = []
    monkeypatch.setattr(run.uuid, "uuid4", lambda: "12345678-rest")
    monkeypatch.setattr(run, "run_benchmark", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("sys.argv", ["run", "--model", "fake", *flags])
    run.main()
    assert calls[0][0] == ("fake", expected)
    assert calls[0][1]["question_types"] is None


def test_cli_parses_type_list(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "run_benchmark", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("sys.argv", ["run", "--model", "fake", "--question-types",
                                    " single-session-preference, ,multi-session "])
    run.main()
    assert calls[0]["question_types"] == ["single-session-preference", "multi-session"]
