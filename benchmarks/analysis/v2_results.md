# V2 campaign results — LongMemEval-S, gpt-4o answerer

Campaign date: 2026-07-06. Goal: match/beat Supermemory's 85.4%. Design: `v2_design.md`;
inputs: `failure_analysis_66422f77.md`, `sota_research.md`.

## Protocol

- Dataset: `longmemeval_s_cleaned.json`, stratified n=48 (8/task type), seeds 42 & 123.
- Answerer: `openai/gpt-4o` (OpenRouter). Extractor: `google/gemini-2.5-flash`.
- Judge: **`openai/gpt-4o` with official LongMemEval judge prompts** (switched mid-campaign;
  the previous `gemini-2.5-flash` judge scored ~4pp lower on identical answers and flipped
  borderline preference judgments run-to-run).
- Champion flags: `MEMDIO_EXTRACT=1 MEMDIO_ROUTE=1 MEMDIO_TOPK=20 MEMDIO_MEMCHARS=4000
  MEMDIO_RERANK=0 MEMDIO_PROMPT_V2=1 MEMDIO_EXHAUSTIVE=1 MEMDIO_MULTIWINDOW=1
  MEMDIO_QUERYEXPAND=1`

## Score ladder (seed 42 unless noted)

| Run | Config | Judge | Score | Result file |
|---|---|---|---|---|
| baseline | extract+route | gemini-flash | 72.9% | 66422f77 |
| baseline re-judged | same answers | **gpt-4o** | **77.1%** | (rejudge) |
| V2 | + prompts V2 + exhaustive | gemini-flash | 68.8% → 77.1% re-judged | f3014399 |
| V2.1 | + multi-window + date-aware counts | gpt-4o | 83.3% | 8db84eb9 |
| **V2.2** | + query expansion + preference prompt v3 | gpt-4o | **85.4%** (41/48) | 054e74e9 |
| V2.2 seed 123 | same config, unseen questions | gpt-4o | **77.1%** | 22940205 |
| V2.3 | + interleaved raw channels + 12-term expansion | gpt-4o | **85.4%** (41/48, zero flips vs V2.2) | b26ec687 |

## Per-type (V2.2)

| Type | seed 42 | seed 123 |
|---|---|---|
| single-session-user | 100% | 87.5% |
| single-session-assistant | 100% | 100% |
| single-session-preference | 62.5% | 62.5% |
| knowledge-update | 87.5% | 87.5% |
| multi-session | 87.5% | 50% |
| temporal-reasoning | 75% | 75% |

## What moved the number (causally traced)

1. **Official judge** (+4.2pp on identical answers): gemini-flash is harsher and unstable
   on preference rubrics — near-identical answers flipped between runs.
2. **Multi-window extraction**: single window silently dropped co-located evidence
   (2nd feed purchase, coin acquisition). Fixed bc149d6b, 8a2466db.
3. **Exhaustive entity scan + FTS plural variants**: FTS5 has no stemming — "kits" never
   matched "kit". Fixed gpt4_59c863d7 (5 model kits).
4. **Semantic raw-session top-up**: found the JetBlue session (no lexical overlap with
   "airlines"). Fixed evidence for gpt4_f420262c (order still imperfect).
5. **LLM query expansion**: "food delivery services" → Domino's/Uber Eats/DoorDash.
   Surfaced Uber Eats for d682f1a2 (2/3 instances; Domino's landed in V2.3's wider
   expansion).
6. **Date-aware count supersession**: "explicit total supersedes; same-conversation
   additions are included; later additions add." Fixed 8fb83627, kept 4d6b87c8 correct.

## Honest caveats (for any external claim)

- **n=48 is preliminary**; competitors publish full-500. σ ≈ ±5-7pp at n=48.
- **Seed-42 was the development set** — 85.4% is the tuned-set number. Unseen seed-123:
  77.1%. True score likely ~79-82%.
- Multi-session generalizes partially (87.5% tuned vs 50% unseen): expansion/exhaustive
  fixes convert *retrieval* misses, but unseen aggregation questions also fail on
  *extraction* gaps (incidental instances never become facts) — the known remaining wall.
- Vendor numbers (Supermemory 85.4%, Zep 71.2%, mem0 ~67% LOCOMO) are self-reported.
- **Do not publish "matched/beat Supermemory" until the full n=500 run** under this
  protocol confirms it. Planned next session.

## Next steps

1. Full n=500 run (~2-3h, est. $25-40): the publishable number.
2. Multi-session extraction gap: exhaustive per-session fact extraction (extract
   incidental instances), or entity-normalized facts ("used food delivery service X").
3. Temporal 75% → frontier 91-95%: event-date resolution at ingest (dual timestamps),
   time-aware query expansion.
4. Preference 62.5% → frontier 96-100%: preference profile store at ingest.
