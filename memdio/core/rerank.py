"""Ranking helpers for combining multiple retrieval result lists."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)
_CROSS_ENCODER_CACHE = {}


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked document ID lists using Reciprocal Rank Fusion.

    Each ranking contributes ``1 / (k + r)`` to a document's total score, where
    ``r`` is the document's 1-indexed position in that ranking. Scores are
    summed across rankings. The returned list is sorted by score descending and
    preserves first-seen order for exact score ties.

    Args:
        rankings: Ranked document ID lists ordered from most to least relevant.
        k: RRF smoothing constant. Larger values flatten rank differences.

    Returns:
        A list of ``(document_id, score)`` pairs sorted by score descending.
        Empty input, or rankings containing only empty lists, returns ``[]``.
    """

    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores.setdefault(doc_id, 0.0)
            scores[doc_id] += 1.0 / (k + rank)

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _get_cross_encoder(model_name: str):
    """Lazy-load and cache the requested cross encoder."""
    if model_name not in _CROSS_ENCODER_CACHE:
        from fastembed import TextCrossEncoder

        _CROSS_ENCODER_CACHE[model_name] = TextCrossEncoder(model_name=model_name)
    return _CROSS_ENCODER_CACHE[model_name]


def cross_encoder_rerank(
    query: str,
    candidates: list[dict],
    top_n: int,
    model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2",
) -> list[dict]:
    """Score candidates with a cross encoder and return the best ``top_n`` items.

    The input dict objects are preserved in the returned list. If the cross
    encoder is unavailable, the function degrades gracefully and returns the
    first ``top_n`` candidates unchanged.
    """

    if not candidates or top_n <= 0:
        return []

    try:
        encoder = _get_cross_encoder(model_name)
        pairs = [(query, candidate.get("content", "")) for candidate in candidates]
        scores = list(encoder.rerank(pairs))
    except Exception as exc:
        logger.warning("Cross encoder unavailable, using fallback ordering: %s", exc)
        return candidates[:top_n]

    ranked = sorted(
        zip(candidates, scores),
        key=lambda item: item[1],
        reverse=True,
    )
    return [candidate for candidate, _score in ranked[:top_n]]
