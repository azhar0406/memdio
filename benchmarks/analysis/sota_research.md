# SOTA research: how top systems hit 80–95% on LongMemEval-S

Compiled 2026-07-06. Goal: close memdio 72.9% → beat Supermemory 85.4% (gpt-4o answer+judge).

## Leaderboard (self-reported, gpt-4o judge unless noted)

| System | Overall | Multi-session | Temporal | Knowledge-update | Preference | Approach |
|---|---|---|---|---|---|---|
| OMEGA | 95.4% | 83% | 94% | 96% | 100% | hybrid RAG + category-specific prompts |
| Mastra OM (gpt-5-mini) | 94.87% | 87.2% | 95.5% | 96.2% | 100% | no retrieval — full compressed observation log |
| Mastra OM (gpt-4o) | 84.23% | 79.7% | 85.7% | 85.9% | 73.3% | same |
| ByteRover | 92.8% | 84.2% | 91.7% | 98.7% | 96.7% | hierarchical Context Tree |
| Hindsight (OSS-20B) | 83.6% | 79.7% | 79.7% | 84.6% | 66.7% | fact graph + 4-way retrieval + RRF |
| Emergence AI | 86% | 81.2% | 85.7% | 83.3% | 60% | turn-match/session-retrieve RAG, k=42 |
| **Supermemory (target)** | **85.4%** | – | 82.0% | 89.7% | – | atomic memories + versioned graph |
| Paper oracle (GPT-4o) | 87.0% (92.4% w/ CoN) | – | – | – | – | perfect evidence upper bound |
| Zep/Graphiti | 71.2% | 47.4%* | 54.1%* | 74.4%* | 53.3%* | temporal KG (*gpt-4o-mini rows) |
| GPT-4o full-context | 60.6% | 44.3% | 45.1% | 78.2% | 20.0% | stuff whole history |

Caveats: vendor numbers self-reported; judge-model variance is real (~±3 pts). Preference has only 30 questions. Multi-session and temporal (~133 q each) are where overall points live.

## Key findings

### Supermemory (85.4%)
- Session ingestion → semantic chunking → **atomic memories** with ambiguous references resolved (contextual retrieval applied to chat memory).
- **Memory-as-index, chunk-as-payload**: search over atomic facts, feed the reader the raw source chunks.
- Relational versioning: `updates` / `extends` / `derives` edges → 89.7% knowledge-update.
- **Dual timestamps**: documentDate + eventDate → 82.0% temporal.
- Recall@15 = 95% overall with ~720 mean injected tokens. They win on retrieval recall, not the reader.

### LongMemEval paper ablations (Wu et al., ICLR 2025, arXiv 2410.10813)
- Oracle evidence: GPT-4o 87.0% (92.4% with Chain-of-Note) vs 60.6% full-haystack. **Retrieval is the bottleneck up to ~85%.**
- Round-level (turn) granularity beats session-level for reading. Compressing values into facts hurts overall — **except multi-session, which improves with fact extraction**.
- **Fact-augmented key expansion** (index = raw + extracted facts, payload = raw): +9.4% recall@k, +5.4% accuracy.
- **Time-aware query expansion** (extract time range from query, filter/boost): temporal recall@5 0.489 → 0.526.
- Evidence **always sorted chronologically**; **Chain-of-Note + JSON evidence format worth up to +10 absolute pts** with GPT-4o.
- GPT-4o keeps improving past 20k retrieved tokens — err on retrieving more.

### OMEGA (95.4%, #1)
SQLite + BM25 + vectors, local. Biggest levers: (1) **five category-specific RAG prompts** (temporal→chronology, KU→recency-wins, counting→exhaustive recall); (2) removing a cross-encoder and improving prompts instead (+7.2% over the reranker); (3) query augmentation (temporal context, synonyms, entities) +3–5%. 76.8% → 95.4% in 8 iterations.

### Mastra (94.87% gpt-5-mini / 84.23% gpt-4o)
No retrieval: compress every message into dense dated observations (3 dates each: observed, referenced, relative offset); full log in context. **Exhaustive coverage beats top-k for aggregation** — multi-session 87.2% is the frontier.

### ByteRover (92.8%)
Knowledge updates **layer chronologically instead of overwriting** → 98.7% KU (best). Zep's edge-invalidation *lost* points on KU (76.9 → 74.4 vs full-context): layering beats deletion.

### Emergence (86%)
Turn-level match, session-level payload; two-stage extract-then-answer. Fragile hardcoded k=42 (audit found it doesn't generalize). Preference is their collapse: 60%.

## Task-type playbook vs memdio's failures (champion run 66422f77, n=48)

| Type | memdio | Frontier | Fix |
|---|---|---|---|
| multi-session | 50% | 83–87% | aggregation-query detection → exhaustive fact-index scan (enumerate, not top-k); higher token budget |
| preference | 50% | 96–100% | preference extraction at ingest + advice-question detection + "MUST follow stated preferences" reader branch |
| temporal | 62% | 91–95% | dual timestamps (resolve relative dates at ingest), time-aware query expansion, chronological evidence sort |
| knowledge-update | 75% | 96–99% | layer versions (never delete), recency-wins prompt, retrieve full update chain |

## Ranked shortlist for memdio

1. **Category-routed answer prompts + Chain-of-Note/JSON + chronological sorting** — LOW effort, +4–7 pts, touches every weak category. Do first.
2. **Dual timestamps + time-aware retrieval** — MEDIUM, temporal 62→~85 plausible (~+6 overall at n=500 weighting).
3. **Exhaustive-aggregation route for multi-session** — MEDIUM-HIGH, highest ceiling (MS 50→80 ≈ +8). The gap-closer; MS is Supermemory's weak spot too.
4. **Fact-augmented key expansion, memory-as-index/chunk-as-payload, higher k** — MEDIUM, +3–5 across the board.
5. **Update layering + preference profile store** — LOW-MEDIUM, KU +2, preference +2.4. Cheap points.

Items 1+2+5 plausibly reach ~85%; item 3 gets past Supermemory.

## Sources
- https://supermemory.ai/research/longmembench/
- https://arxiv.org/abs/2410.10813 (LongMemEval paper)
- https://mastra.ai/research/observational-memory
- https://omegamax.co/blog/number-one-on-longmemeval
- https://arxiv.org/html/2512.12818v1 (Hindsight)
- https://www.byterover.dev/blog/benchmark_ai_agent_memory_real_production_byterover_top_market_accuracy_longmemeval
- https://www.emergence.ai/blog/sota-on-longmemeval-with-rag
- https://blog.getzep.com/state-of-the-art-agent-memory/ · https://arxiv.org/abs/2501.13956
- https://mem0.ai/blog/ai-memory-benchmarks-in-2026
- https://github.com/xiaowu0162/longmemeval
