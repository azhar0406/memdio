# V2 design: 72.9% → ≥85.4% on LongMemEval-S (beat Supermemory)

Inputs: `failure_analysis_66422f77.md` (worker, verified by manager) × `sota_research.md` (research agent).
Baseline: 35/48 (72.9%), gpt-4o answer + gemini-2.5-flash judge, stratified n=48 seed=42. Target: ≥41/48.

## Where the 13 failures actually are

| Root cause | n | Failures | Fix |
|---|---|---|---|
| Retrieval-miss (enumerate entity instances across sessions) | 5 | 46a3abf7, d682f1a2, gpt4_59c863d7 (MS count); gpt4_f420262c, gpt4_7abb270c (temporal ordering) | V2-B exhaustive entity scan |
| Synthesis-miss: preference abstention | 3 | 75832dbd, 8a2466db, fca70973 | V2-A preference prompt |
| Synthesis-miss: failed addition (50+20) | 1 | bc149d6b | V2-A aggregation CoN |
| Preference-style (generic answer) | 1 | 1c0ddc50 | V2-A preference prompt |
| Temporal: supersession treated as additive | 2 | 69fee5aa, 8fb83627 | V2-A recency/supersession rules |
| Temporal: date comparison error | 1 | gpt4_385a5000 | V2-A temporal CoN |

Convergence with research: every fix maps to a proven technique (OMEGA category prompts +7.2%; paper CoN +10; Supermemory recall-first; ByteRover layering).

## V2-A — Category-routed answer prompts (`answer.py`) — flag `MEMDIO_PROMPT_V2=1`

Classify the **question** with our own lexical heuristics (never gold `question_type` — benchmark honesty):
- `preference`: `recommend|suggest(ion)?|ideas for|what should i|can you give me|tips for|help me (choose|pick|plan)`
- `aggregation`: existing `_AGG_PATTERNS`
- `temporal`: existing `_TEMPORAL_PATTERNS` + ordering (`first|last|order|before|after`)
- else `detail` → unchanged base prompt (protects the two 100% categories)

Prompt branches (all keep the same context/question skeleton):
1. **PREFERENCE**: "This is a recommendation/advice request. Step 1: from the memories, identify the user's stated preferences, interests, constraints, and current setup relevant to the topic. Step 2: give concrete recommendations explicitly tailored to those preferences, referencing them. If the memories reveal the user's interests in this topic area, you MUST provide recommendations based on them rather than declining."
   - Guard: abstention stays allowed when memories genuinely contain nothing about the topic (LongMemEval `_abs` questions must still abstain).
2. **AGGREGATION** (Chain-of-Note): "Step 1: enumerate every distinct item/event with its date as a numbered list. Step 2: dedupe — the same item may appear in multiple memories. Step 3: if a later memory UPDATES a count/state (e.g. 'now finished five'), the later statement REPLACES the earlier one — never add stale and current states. If separate purchases/events accumulate, sum them. Step 4: state the final count/total."
3. **TEMPORAL** (Chain-of-Note): "Step 1: extract each relevant event with its ABSOLUTE date (resolve relative dates using the memory's date header). Step 2: sort chronologically. Step 3: compare/compute carefully (earlier = smaller date). Answer with the final result."

## V2-B — Exhaustive entity scan for aggregation/ordering (`search.py`) — flag `MEMDIO_EXHAUSTIVE=1`

Problem: top-k semantic retrieval misses instance #4 and #5 of "model kits I bought" — all 5 retrieval-misses share this shape.

For queries classified `aggregation` or temporal-ordering:
1. Extract content keywords from the query (stopword-filtered tokens, len>3).
2. FTS-search each keyword **separately** (`storage.search(kw)`); union all hits — facts AND raw sessions.
3. Merge with the existing routed results (routed results first, then new unique hits).
4. Cap total context budget (~60 items / existing MEMCHARS), no other truncation — gpt-4o improves past 20k tokens (paper).
5. `format_context`: sort evidence chronologically by `document_date` for these query classes; keep date headers prominent.

## Expected conversions (honest estimate)

| Fix | Failures addressed | Expected converts |
|---|---|---|
| V2-A preference | 4 | 3–4 |
| V2-A aggregation/temporal CoN | 4 | 2–3 |
| V2-B exhaustive scan | 5 | 3–4 |
| **Total** | 13 | **8–11 → 43–46/48 (89–95%)** |

Even at the pessimistic end (+6) we hit 41/48 = 85.4%.

## Risks & guards
- Preference branch must not break abstention questions → conditional wording, verify on `_abs` questions if sampled.
- Exhaustive scan adds noise → gpt-4o is noise-tolerant at this scale (paper); budget cap.
- Prompt churn regressing SSU/SSA (100%) → `detail` route keeps the current champion prompt verbatim.
- Both changes flag-gated → clean A/B vs 72.9% baseline on identical question set (seed=42).

## Validation protocol
1. A/B: n=48 seed=42, gpt-4o, flags on vs baseline. Require ≥41/48.
2. Confirm: second run seed=123 n=48 to check we didn't overfit to seed-42 questions.
3. Per-type report both runs; investigate any regression in previously-perfect types.
4. Production parity: after benchmark validation, port winning logic into `memdio/core` (classify_query, recall) in a follow-up commit.

## Work split
- worker: V2-A (answer.py prompt routing + classifier extension)
- manager: V2-B (search.py exhaustive scan + format_context chronological sort)
- inspector: reviews this design first, then both diffs before the A/B run
