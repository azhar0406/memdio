"""Search memdio for relevant memories given a question."""

import os
import re

from benchmarks.config import MAX_MEMORY_CHARS, SEARCH_TOP_K
from memdio.core.rerank import cross_encoder_rerank, reciprocal_rank_fusion
from memdio.core.storage import StorageManager

# Patterns to detect temporal queries
_TEMPORAL_PATTERNS = re.compile(
    r'when did|how long ago|what date|what day|which month|which year'
    r'|last (?:week|month|year)|in (?:january|february|march|april|may|june'
    r'|july|august|september|october|november|december)',
    re.IGNORECASE,
)

_MONTH_NAMES = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


# Aggregation/counting questions ("how many kits", "total weight", "how often").
# Counting/totalling questions. Deliberately excludes "how long" (a duration query,
# best answered from the raw session's anchor date, not by counting facts).
_AGG_PATTERNS = re.compile(
    r'\bhow many\b|\bhow much\b|\bhow often\b|\bnumber of\b|\btotal\b|\bcount\b'
    r'|\bhow frequently\b|\btimes\b',
    re.IGNORECASE,
)


def classify_query(query: str) -> str:
    """Aggregation/temporal questions answer best from discrete dated facts;
    detail questions answer best from the full raw+fact hybrid."""
    if _AGG_PATTERNS.search(query):
        return "aggregation"
    if _TEMPORAL_PATTERNS.search(query):
        return "temporal"
    return "detail"


def hybrid_search(storage: StorageManager, query: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """Combine FTS5 + semantic + temporal + relation expansion."""
    rerank_mode = os.getenv("MEMDIO_RERANK", "1")

    # Route aggregation/temporal queries through the SAME pipeline but restricted to
    # discrete facts (raw sessions crowd out the countable facts these questions need).
    fact_only = os.getenv("MEMDIO_ROUTE") == "1" and classify_query(query) in ("aggregation", "temporal")
    fetch_k = top_k * 3 if fact_only else top_k

    results = {}
    fts_ids = []
    sem_ids = []
    temporal_ids = []

    def _keep(r):
        return not fact_only or (r.get("tags") or "") == "fact"

    # FTS5 word search
    try:
        fts_results = storage.search(query)
        for r in [x for x in fts_results if _keep(x)][:fetch_k]:
            results[r["id"]] = r
            fts_ids.append(r["id"])
    except Exception:
        pass

    # Semantic search
    try:
        sem_results = storage.semantic_search(query, top_k=fetch_k)
        for r in sem_results:
            if not _keep(r):
                continue
            sem_ids.append(r["id"])
            if r["id"] not in results:
                results[r["id"]] = r
    except Exception:
        pass

    # Temporal search — if query has temporal keywords
    if _TEMPORAL_PATTERNS.search(query):
        try:
            # Try to detect month references for date range
            for month_name, month_num in _MONTH_NAMES.items():
                if month_name in query.lower():
                    # Search across common years
                    for year in range(2020, 2027):
                        start = f"{year}-{month_num}-01"
                        end = f"{year}-{month_num}-28"
                        temporal_results = storage.temporal_search(start, end, top_k=5)
                        for r in temporal_results:
                            if not _keep(r):
                                continue
                            temporal_ids.append(r["id"])
                            if r["id"] not in results:
                                results[r["id"]] = r
                    break
            else:
                # Broad temporal search — last 5 years
                temporal_results = storage.temporal_search("2020-01-01", "2027-12-31", top_k=fetch_k)
                for r in temporal_results:
                    if not _keep(r):
                        continue
                    temporal_ids.append(r["id"])
                    if r["id"] not in results:
                        results[r["id"]] = r
        except Exception:
            pass

    final_k = 30 if fact_only else top_k
    if rerank_mode == "1":
        rankings = [fts_ids, sem_ids]
        if temporal_ids:
            rankings.append(temporal_ids)
        fused_ids = [doc_id for doc_id, _score in reciprocal_rank_fusion(rankings)]
        ranked = [results[doc_id] for doc_id in fused_ids if doc_id in results][:final_k]
    elif rerank_mode == "cross":
        fts_ranked = [results[rid] for rid in fts_ids if rid in results]
        sem_ranked = sorted(
            [r for rid, r in results.items() if rid not in fts_ids],
            key=lambda x: x.get("score", 0),
            reverse=True,
        )
        ranked = fts_ranked + sem_ranked
    else:
        # Rank: FTS matches first, then semantic by score
        fts_ranked = [results[rid] for rid in fts_ids if rid in results]
        sem_ranked = sorted(
            [r for rid, r in results.items() if rid not in fts_ids],
            key=lambda x: x.get("score", 0),
            reverse=True,
        )
        ranked = (fts_ranked + sem_ranked)[:final_k]

    # Relation expansion — pull in related memories for multi-session reasoning
    seed_ids = list(results.keys()) if rerank_mode == "cross" else [r["id"] for r in ranked]
    try:
        related = storage.get_related_memories(seed_ids)
        for r in related:
            if r["id"] not in {m["id"] for m in ranked}:
                ranked.append(r)
    except Exception:
        pass

    if rerank_mode == "cross":
        return cross_encoder_rerank(query, ranked, top_n=top_k)

    return ranked[:top_k + 5]  # allow a few extra from relations


def _extract_relevant_window(content: str, query: str, max_chars: int) -> str:
    """Extract the most relevant window of text around query keyword matches."""
    if len(content) <= max_chars:
        return content

    words = [w.lower() for w in query.split() if len(w) > 3]
    positions = []
    content_lower = content.lower()
    for w in words:
        idx = content_lower.find(w)
        while idx != -1:
            positions.append(idx)
            idx = content_lower.find(w, idx + 1)

    if not positions:
        half = max_chars // 2
        return content[:half] + "\n...\n" + content[-half:]

    positions.sort()
    best_start = max(0, positions[len(positions) // 2] - max_chars // 2)
    best_end = min(len(content), best_start + max_chars)
    best_start = max(0, best_end - max_chars)

    turn_pattern = re.compile(r'\n(?:user|assistant):', re.IGNORECASE)
    before = content[:best_start]
    m = list(turn_pattern.finditer(before))
    if m:
        best_start = m[-1].start() + 1

    snippet = content[best_start:best_end]
    prefix = "..." if best_start > 0 else ""
    suffix = "..." if best_end < len(content) else ""
    return f"{prefix}{snippet}{suffix}"


def format_context(results: list[dict], query: str = "") -> str:
    """Format search results into a context string for LLM."""
    if not results:
        return "No relevant memories found."

    parts = []
    for i, r in enumerate(results, 1):
        content = r.get("content", "")
        if len(content) > MAX_MEMORY_CHARS and query:
            content = _extract_relevant_window(content, query, MAX_MEMORY_CHARS)
        elif len(content) > MAX_MEMORY_CHARS:
            content = content[:MAX_MEMORY_CHARS] + "..."

        # Prefer the real session/event date over created_at (the ingest timestamp).
        date = r.get("document_date") or r.get("event_date") or r.get("created_at")
        header = f"[Memory {i}]"
        if date:
            header += f" (date: {date})"
        if r.get("score"):
            header += f" (relevance: {r['score']:.3f})"
        if r.get("is_related"):
            header += f" (related: {r.get('relation_type', 'extends')})"
        parts.append(f"{header}\n{content}")
    return "\n\n".join(parts)
