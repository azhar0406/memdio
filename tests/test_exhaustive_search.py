"""Tests for the V2-B exhaustive entity scan (benchmarks/longmemeval/search.py)."""

import pytest

from benchmarks.longmemeval.search import (
    _content_keywords,
    _needs_exhaustive,
    format_context,
    hybrid_search,
)


@pytest.fixture(autouse=True)
def clear_flags(monkeypatch):
    for k in ("MEMDIO_EXHAUSTIVE", "MEMDIO_EXHAUSTIVE_MAX", "MEMDIO_EXHAUSTIVE_RAW",
              "MEMDIO_ROUTE", "MEMDIO_RERANK"):
        monkeypatch.delenv(k, raising=False)


# ---- classification ----

@pytest.mark.parametrize("query,expected", [
    ("How many model kits have I bought this year?", True),        # aggregation
    ("What is the total weight of the feed I purchased?", True),   # aggregation
    ("Which airline did I fly with first this year?", True),       # ordering
    ("In what order did I visit the museums?", True),              # ordering
    ("When did I adopt my cat?", True),                            # temporal
    ("What is my sister-in-law's name?", False),                   # detail
    ("Can you recommend resources to learn video editing?", False),  # preference/detail
])
def test_needs_exhaustive(query, expected):
    assert _needs_exhaustive(query) is expected


def test_content_keywords_filter_stopwords():
    kws = _content_keywords("How many model kits have I bought this year?")
    assert "model" in kws and "kits" in kws
    assert "many" not in kws and "have" not in kws and "this" not in kws


def test_content_keywords_deduped_and_capped():
    kws = _content_keywords("kits kits kits alpha bravo charlie delta echo foxtrot golf hotel india")
    assert kws.count("kits") == 1
    assert len(kws) <= 8


# ---- exhaustive union (fake storage, no audio/db needed) ----

class FakeStorage:
    """Minimal StorageManager stand-in for hybrid_search."""

    def __init__(self, memories):
        self.memories = memories

    def search(self, query):
        return [m for m in self.memories if query.lower() in m["content"].lower()]

    def semantic_search(self, query, top_k=10):
        return []

    def temporal_search(self, start, end, top_k=10):
        return []

    def get_related_memories(self, ids):
        return []


MEMS = [
    {"id": "f1", "content": "Fact: bought Tamiya Spitfire kit", "tags": "fact"},
    {"id": "f2", "content": "Fact: bought B-29 kit", "tags": "fact"},
    {"id": "r1", "content": "user: I just bought a Revell F-15 kit today!", "tags": "session"},
    {"id": "r2", "content": "user: my Camaro kit arrived", "tags": "session"},
    {"id": "x1", "content": "user: I love gardening", "tags": "session"},
]


def test_exhaustive_off_by_default():
    storage = FakeStorage(MEMS)
    results = hybrid_search(storage, "How many kits have I bought?", top_k=1)
    # without the flag, capped at top_k + 5
    assert len(results) <= 6


def test_exhaustive_unions_facts_and_raw(monkeypatch):
    monkeypatch.setenv("MEMDIO_EXHAUSTIVE", "1")
    storage = FakeStorage(MEMS)
    results = hybrid_search(storage, "How many kits have I bought?", top_k=1)
    ids = [r["id"] for r in results]
    # every keyword hit is included: both facts and both raw sessions
    assert {"f1", "f2", "r1", "r2"}.issubset(set(ids))
    assert "x1" not in ids  # non-matching memory not dragged in
    # facts are appended before raw sessions among the extras
    assert ids.index("f1") < ids.index("r1") or ids.index("f2") < ids.index("r1")


def test_exhaustive_respects_raw_cap(monkeypatch):
    monkeypatch.setenv("MEMDIO_EXHAUSTIVE", "1")
    monkeypatch.setenv("MEMDIO_EXHAUSTIVE_RAW", "1")
    storage = FakeStorage(MEMS)
    results = hybrid_search(storage, "How many kits have I bought?", top_k=1)
    raw_ids = [r["id"] for r in results if (r.get("tags") or "") == "session"]
    assert len(raw_ids) <= 1 + 1  # top_k seed may include one raw + capped extras


def test_exhaustive_not_triggered_for_detail(monkeypatch):
    monkeypatch.setenv("MEMDIO_EXHAUSTIVE", "1")
    storage = FakeStorage(MEMS)
    results = hybrid_search(storage, "What is my cousin's name?", top_k=2)
    assert len(results) <= 7  # detail query keeps the old top_k+5 cap


# ---- chronological sort in format_context ----

def test_format_context_chronological_when_flagged(monkeypatch):
    monkeypatch.setenv("MEMDIO_EXHAUSTIVE", "1")
    results = [
        {"id": "b", "content": "second event", "document_date": "2024-05-10"},
        {"id": "a", "content": "first event", "document_date": "2024-01-02"},
    ]
    ctx = format_context(results, query="In what order did I visit the museums?")
    assert ctx.index("first event") < ctx.index("second event")


def test_format_context_order_preserved_without_flag():
    results = [
        {"id": "b", "content": "second event", "document_date": "2024-05-10"},
        {"id": "a", "content": "first event", "document_date": "2024-01-02"},
    ]
    ctx = format_context(results, query="In what order did I visit the museums?")
    assert ctx.index("second event") < ctx.index("first event")
