"""Regression coverage for paired benchmark comparisons."""

import math

import pytest

from benchmarks.tools import pref_comparison


def record(qid, label, **extra):
    return {"question_id": qid, "question_type": "single-session-preference",
            "label": label, **extra}


def test_flips_join_by_id_with_duplicate_question_text():
    control = [record("a", False, question="Same text"),
               record("b", True, question="Same text")]
    variant = [record("b", True, question="Same text"),
               record("a", True, question="Changed text")]
    flips = pref_comparison.question_flips(control, variant)
    assert len(flips) == 1
    assert flips[0]["question_id"] == "a"
    assert flips[0]["prefctl"] is False
    assert flips[0]["prefv3"] is True


def test_error_records_without_question_or_memory_count():
    control = [record("a", False, error="ingest failed"),
               record("b", True, num_memories_found=20)]
    variant = [record("a", True, num_memories_found=10)]
    assert pref_comparison.question_flips(control, variant)[0]["question"] == "a"
    delta = pref_comparison.memories_delta(control, variant)
    assert delta["prefctl_mean"] == 20
    assert delta["prefctl_missing"] == 1
    assert delta["delta_mean"] == -10
    assert math.isnan(pref_comparison.memories_delta(control[:1], variant)["delta_mean"])


@pytest.mark.parametrize("gate_args, verdict", [([], "target>=85%  FAIL"),
                                              (["--gate", "0.5"], "target>=50%  PASS")])
def test_main_reports_unmatched_ids(monkeypatch, capsys, gate_args, verdict):
    runs = {
        "control": {"results": [record("shared", False), record("ctl-only", True)]},
        "variant": {"results": [record("shared", True), record("v3-only-1", True),
                                record("v3-only-2", False)]},
    }
    monkeypatch.setattr(pref_comparison, "load_results", runs.__getitem__)
    monkeypatch.setattr(pref_comparison.sys, "argv",
                        ["pref_comparison", "control", "variant", *gate_args])
    pref_comparison.main()
    output = capsys.readouterr().out
    assert "Unmatched question_ids: prefctl=1  prefv3=2" in output
    assert "Missing counts: prefctl=2  prefv3=3" in output
    assert verdict in output
