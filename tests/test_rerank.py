from memdio.core import rerank
from memdio.core.rerank import cross_encoder_rerank, reciprocal_rank_fusion


def test_rrf_prefers_document_ranked_in_multiple_lists():
    fused = reciprocal_rank_fusion([["doc-a", "doc-b"], ["doc-b", "doc-c"]])

    assert fused[0][0] == "doc-b"


def test_rrf_score_math_matches_known_example():
    fused = reciprocal_rank_fusion([["doc-a", "doc-b"], ["doc-b", "doc-a"]], k=10)
    scores = dict(fused)

    assert scores["doc-a"] == (1 / 11) + (1 / 12)
    assert scores["doc-b"] == (1 / 12) + (1 / 11)


def test_rrf_empty_input_returns_empty_list():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_single_list_preserves_rank_order():
    fused = reciprocal_rank_fusion([["doc-a", "doc-b", "doc-c"]], k=1)

    assert [doc_id for doc_id, _score in fused] == ["doc-a", "doc-b", "doc-c"]


def test_cross_encoder_rerank_returns_top_n_by_score(monkeypatch):
    class FakeEncoder:
        def rerank(self, pairs):
            assert pairs == [
                ("query", "first"),
                ("query", "second"),
                ("query", "third"),
            ]
            return [0.1, 0.9, 0.4]

    monkeypatch.setattr(rerank, "_get_cross_encoder", lambda _model_name: FakeEncoder())
    candidates = [
        {"id": "a", "content": "first"},
        {"id": "b", "content": "second"},
        {"id": "c", "content": "third"},
    ]

    ranked = cross_encoder_rerank("query", candidates, top_n=2)

    assert ranked == [candidates[1], candidates[2]]


def test_cross_encoder_rerank_falls_back_when_encoder_unavailable(monkeypatch):
    monkeypatch.setattr(rerank, "_get_cross_encoder", lambda _model_name: (_ for _ in ()).throw(ImportError))
    candidates = [
        {"id": "a", "content": "first"},
        {"id": "b", "content": "second"},
    ]

    assert cross_encoder_rerank("query", candidates, top_n=1) == candidates[:1]
