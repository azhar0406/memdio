"""Search memdio for relevant memories given a question."""

import os
import re
from itertools import zip_longest

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


# Ordering questions ("which did I do first/last") need every instance of the
# entity, like aggregation — but don't match the aggregation/temporal patterns.
_ORDER_PATTERNS = re.compile(
    r'\bfirst\b|\blast\b|\bearliest\b|\blatest\b|\bmost recent(?:ly)?\b|\bwhat order\b',
    re.IGNORECASE,
)

# Function/question words that carry no entity signal for a keyword scan.
_STOPWORDS = frozenset("""
    what when where which whose whom while would could should shall might must
    this that these those there their theirs them then than they have has had
    having been being because before after during between both each some such
    about above below again against into just more most only other over same
    from with your yours mine ours does did doing will time times total number
    many much often mention mentioned mentioning tell told user assistant
    question answer past months month weeks week years year days recently
""".split())


def _needs_exhaustive(query: str) -> bool:
    """Aggregation and ordering questions fail whenever a single instance of the
    target entity is missed, so top-k retrieval isn't enough for them."""
    cls = classify_query(query)
    return cls in ("aggregation", "temporal") or bool(_ORDER_PATTERNS.search(query))


def _content_keywords(query: str, max_keywords: int = 8) -> list[str]:
    """Entity-bearing keywords from the query (stopword-filtered, deduped)."""
    words = re.findall(r"[a-z']+", query.lower())
    seen = set()
    keywords = []
    for w in words:
        if len(w) > 3 and w not in _STOPWORDS and w not in seen:
            seen.add(w)
            keywords.append(w)
            if len(keywords) >= max_keywords:
                break
    return keywords


def _keyword_variants(kw: str) -> list[str]:
    """Singular/plural forms — the FTS5 table uses the default tokenizer (no
    stemming), so 'kits' never matches 'kit'. Search both forms."""
    variants = [kw]
    if kw.endswith("ies") and len(kw) > 4:
        variants.append(kw[:-3] + "y")   # hobbies -> hobby
    elif kw.endswith("es") and len(kw) > 4:
        variants.append(kw[:-2])          # dishes -> dish
        variants.append(kw[:-1])          # recipes -> recipe
    elif kw.endswith("s") and not kw.endswith("ss"):
        variants.append(kw[:-1])          # kits -> kit
    elif kw.endswith("y") and len(kw) > 4:
        variants.append(kw[:-1] + "ies")  # hobby -> hobbies
    else:
        variants.append(kw + "s")         # kit -> kits
    return variants


def hybrid_search(
    storage: StorageManager,
    query: str,
    top_k: int = SEARCH_TOP_K,
    extra_terms: list[str] | None = None,
) -> list[dict]:
    """Combine FTS5 + semantic + temporal + relation expansion.

    ``extra_terms`` are additional search terms (e.g. LLM-expanded instance
    names like "Domino's", "Uber Eats" for "food delivery services") used by
    the exhaustive scan.
    """
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

    # Exhaustive entity scan — aggregation/ordering questions fail whenever a
    # single instance is missed, and incidental instances are often never
    # extracted as facts. Union in every FTS keyword hit: facts first, then the
    # raw sessions that contain the missed instances (memory-as-index,
    # chunk-as-payload). Flag-gated for A/B.
    if os.getenv("MEMDIO_EXHAUSTIVE") == "1" and _needs_exhaustive(query):
        max_items = int(os.getenv("MEMDIO_EXHAUSTIVE_MAX", "80"))
        max_raw = int(os.getenv("MEMDIO_EXHAUSTIVE_RAW", "20"))
        seen = {m["id"] for m in ranked}
        extra_facts, extra_raw_sem, extra_raw_kw = [], [], []

        # Semantic raw-session top-up — brand/entity instances ("JetBlue") are
        # not derivable from the query's keywords ("airlines", "flew"), so a
        # keyword scan alone misses them. Pull back the semantically closest
        # raw sessions beyond what the main retrieval kept.
        try:
            for r in storage.semantic_search(query, top_k=max(fetch_k, top_k * 3)):
                if r["id"] in seen or (r.get("tags") or "") == "fact":
                    continue
                seen.add(r["id"])
                extra_raw_sem.append(r)
        except Exception:
            pass

        # Keyword scan over query keywords plus any LLM-expanded instance
        # terms (category questions like "food delivery services" need the
        # instances — "Domino's", "Uber Eats" — which share no surface form
        # with the category).
        scan_terms = []
        for kw in _content_keywords(query):
            scan_terms.extend(_keyword_variants(kw))
        for term in extra_terms or []:
            term = term.strip().lower()
            if term and term not in scan_terms:
                scan_terms.append(term)
        for term in scan_terms:
            try:
                hits = storage.search(term)
            except Exception:
                continue
            for r in hits:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                if (r.get("tags") or "") == "fact":
                    extra_facts.append(r)
                else:
                    extra_raw_kw.append(r)
        ranked.extend(extra_facts)
        # Interleave keyword and semantic raw hits — either channel alone can
        # flood the raw budget and starve the other (semantic finds "JetBlue",
        # keyword finds the low-similarity session with the coin acquisition).
        interleaved = []
        for pair in zip_longest(extra_raw_kw, extra_raw_sem):
            for r in pair:
                if r is not None:
                    interleaved.append(r)
        ranked.extend(interleaved[:max_raw])
        return ranked[:max_items]

    return ranked[:top_k + 5]  # allow a few extra from relations


_TURN_PATTERN = re.compile(r'\n(?:user|assistant):', re.IGNORECASE)


def _extract_relevant_window(content: str, query: str, max_chars: int) -> str:
    """Extract the most relevant window(s) of text around query keyword matches.

    With MEMDIO_MULTIWINDOW=1, up to three windows around distinct keyword
    clusters are returned — a single window silently drops co-located evidence
    (e.g. the second feed purchase later in the same session).
    """
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

    if os.getenv("MEMDIO_MULTIWINDOW") == "1":
        # Cluster keyword hits; a gap larger than half the budget starts a new
        # cluster. One window per cluster (up to 3), sharing the char budget.
        clusters = [[positions[0]]]
        for p in positions[1:]:
            if p - clusters[-1][-1] > max_chars // 2:
                clusters.append([p])
            else:
                clusters[-1].append(p)
        clusters = clusters[:3]
        win = max_chars // len(clusters)
        snippets = []
        prev_end = 0
        for cl in clusters:
            center = cl[len(cl) // 2]
            end = min(len(content), max(prev_end, center - win // 2) + win)
            start = max(prev_end, end - win)
            m = list(_TURN_PATTERN.finditer(content[:start]))
            if m and m[-1].start() + 1 > prev_end:
                start = m[-1].start() + 1
            if start >= end:
                continue
            snippets.append(content[start:end])
            prev_end = end
        prefix = "..." if snippets and not content.startswith(snippets[0]) else ""
        suffix = "..." if prev_end < len(content) else ""
        joined = "\n...\n".join(snippets)
        return f"{prefix}{joined}{suffix}"

    best_start = max(0, positions[len(positions) // 2] - max_chars // 2)
    best_end = min(len(content), best_start + max_chars)
    best_start = max(0, best_end - max_chars)

    before = content[:best_start]
    m = list(_TURN_PATTERN.finditer(before))
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

    # Chronological evidence ordering for aggregation/ordering questions — the
    # LongMemEval paper always sorts retrieved evidence by timestamp, which
    # makes ordering/supersession reasoning much easier for the reader.
    if os.getenv("MEMDIO_EXHAUSTIVE") == "1" and query and _needs_exhaustive(query):
        results = sorted(
            results,
            key=lambda r: r.get("document_date") or r.get("event_date") or r.get("created_at") or "9999",
        )

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
