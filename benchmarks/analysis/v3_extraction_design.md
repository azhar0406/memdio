# V3 extraction design draft — close the multi-session ingest gap

Context:
- `v2_results.md` shows the remaining generalization wall is **multi-session**: 87.5% on seed 42, 50% on seed 123.
- The failure shape is now mostly **extraction**, not retrieval: if ingest never emits a fact for an incidental mention, query-time exhaustive scan cannot surface it.
- Current benchmark ingest already stores both raw `session` memories and extracted `fact` memories when `MEMDIO_EXTRACT=1`; the weak link is the fact prompt, not the storage layout.

Dataset-scale constraints:
- LongMemEval-S full set is 500 questions.
- Average haystack sessions per question: **47.7**; median **48**.
- Average session size: about **10.3 turns** and **10.3k characters**.
- At full500 scale, any per-session extractor costs about **23,865 LLM calls** once. A second pass roughly doubles that.

## Problem statement

The current `EXTRACT_PROMPT` is tuned for durable, obvious facts. It explicitly ignores vague interest and filler, which is good, but it still misses **incidental instance mentions embedded inside otherwise unrelated conversations**:
- "I had Domino's Pizza three times last week"
- "my weekends have been all about Uber Eats lately"
- "I picked up a Revell F-15 Eagle kit on a whim"

Those mentions are often:
- not the main topic of the session
- phrased as side comments rather than direct asks
- expressed in user language that does not match the benchmark query category

That creates a hard ceiling for multi-session count/aggregation questions. Query expansion can broaden recall only for facts that already exist.

## Option survey

### Option A — exhaustive single-pass per-session extraction

Design:
- Keep one extractor call per session.
- Tighten the prompt so it must emit **all concrete user events/possessions/usages**, including incidental mentions.
- Require category-normalized wording in the returned facts so later retrieval matches category questions better.

What changes at ingest:
- No storage-schema change.
- `extract_facts()` still returns `list[str]`.
- Facts become more numerous and more query-aligned.

Examples of desired normalized outputs:
- `The user used the food delivery service Domino's Pizza.`
- `The user used the food delivery service Uber Eats.`
- `The user bought the model kit Revell F-15 Eagle.`
- `The user owns a 20-gallon freshwater community tank named Amazonia.`

Pros:
- Lowest implementation risk.
- Same call count as current `MEMDIO_EXTRACT`.
- Composes directly with existing `tags="fact"` storage and current search stack.
- Improves both lexical and semantic retrieval because the category words are present in the fact text.

Cons:
- Higher output volume per session.
- More duplicate facts across nearby sessions.
- Still relies on one prompt to do both detection and normalization correctly.

Cost / latency estimate:
- **LLM calls/question:** ~47.7, same as today.
- **Relative ingest cost vs current extract path:** about **1.0x calls**, likely **1.1x-1.3x tokens** because the prompt is stricter and output is denser.
- **Full500 run:** still ~23.9k extractor calls.

### Option B — entity-normalized fact schema in one pass

Design:
- Still one extractor call per session, but ask for strongly normalized facts around a small taxonomy:
  - service usage
  - purchase/acquisition
  - ownership
  - attendance/visit
  - completion/progress
- Facts stay as strings, but each line is forced into a more regular sentence pattern.

Pros:
- Better retrieval for category questions than free-form facts.
- Easier supersession/count reasoning downstream because fact surface forms are more consistent.
- Still fits the current `list[str]` API.

Cons:
- More prompt brittleness.
- Higher hallucination risk if the model force-fits a taxonomy when the session is ambiguous.
- Harder to keep useful assistant-side recommendation facts without over-normalizing them.

Cost / latency estimate:
- **LLM calls/question:** ~47.7, same as today.
- **Relative ingest cost vs current extract path:** about **1.0x calls**, **1.15x-1.35x tokens**.
- **Full500 run:** still ~23.9k extractor calls.

### Option C — two-pass extraction: entities first, facts second

Design:
- Pass 1 extracts candidate entities/instances from every session.
- Pass 2 converts those entities into normalized countable facts, optionally with category labels.

Pros:
- Highest recall ceiling.
- Better separation of "spot the instance" vs "normalize the fact".
- More robust for side comments that are easy to notice but hard to normalize in one shot.

Cons:
- Materially slower and more expensive.
- More plumbing: two prompts, two parsers, more failure modes.
- Hard to justify before exhausting the simpler single-pass upgrade.

Cost / latency estimate:
- **LLM calls/question:** ~95.5 worst-case.
- **Relative ingest cost vs current extract path:** about **2.0x calls** worst-case, or **1.4x-1.7x** if pass 2 is gated to entity-positive sessions only.
- **Full500 run:** ~47.7k calls worst-case.

### Option D — question-conditioned extraction

Design:
- Extract facts with awareness of the current benchmark question.

Pros:
- Highest short-term benchmark score potential.

Cons:
- Violates the spirit of reusable memory ingest.
- Couples ingest to evaluation questions.
- Not acceptable for an honest benchmark architecture.

Recommendation:
- Reject.

## Recommended design

Recommend **Option A with a controlled dose of Option B**:
- keep the current one-pass per-session architecture
- keep `extract_facts()` returning plain strings
- revise the prompt so it is **exhaustive for concrete user instances**
- require **lightweight category normalization** inside those strings

Why this is the right next move:
- It directly targets the known seed-123 failure mode.
- It preserves the current ingest and retrieval plumbing.
- It avoids doubling full500 ingest cost.
- It gives query expansion and exhaustive scan better raw material instead of asking search to recover nonexistent facts.

### Proposed flag

`MEMDIO_EXTRACT_V3=1`

Behavior:
- `MEMDIO_EXTRACT=1` remains the master switch for fact extraction.
- `MEMDIO_EXTRACT_V3=1` selects the stronger V3 prompt and any small parser cleanup needed.
- Without `MEMDIO_EXTRACT_V3`, the current extraction path remains unchanged for A/B comparison.

### How it composes with the existing path

Current flow:
- `process_question()` checks `MEMDIO_EXTRACT`
- if enabled, it passes `extract_facts()` into `ingest_question()`
- `ingest_question()` stores raw `session` memories plus extracted `fact` memories

V3 composition:
- Keep that exact flow.
- Add `EXTRACT_PROMPT_V3` in `benchmarks/longmemeval/extract.py`.
- Inside `extract_facts()`, choose `EXTRACT_PROMPT_V3` when `MEMDIO_EXTRACT_V3=1`, else keep the current prompt.
- Keep `_parse_facts()` returning `list[str]`; no storage change needed for the first cut.

That means V3 is:
- benchmark-honest
- fully flag-gated
- reversible
- compatible with the existing `MEMDIO_ROUTE`, exhaustive scan, and prompt-V2 answer path

## Prompt sketch

Intent:
- capture incidental but concrete user instances
- normalize them enough to be retrievable by category questions
- avoid force-inventing taxonomy when evidence is weak

Prompt sketch:

```text
Extract atomic FACTS about the USER from the conversation below.

Rules:
- Output one fact per line. No numbering, no bullets, no preamble.
- Extract EVERY concrete user instance that could matter later for counting, ordering, totals, ownership, attendance, purchases, service usage, completion, or updates, even if it appears only as a side comment.
- Prefer normalized, category-bearing wording when the category is clear:
  "The user used the food delivery service Domino's Pizza."
  "The user bought the model kit Revell F-15 Eagle."
  "The user visited the Science Museum."
- Keep exact names, quantities, prices, weights, dates, and version/update words.
- If a later line in the same session updates or supersedes an earlier state, emit the latest state and the concrete new event if both matter.
- Do not invent categories or facts that are not explicitly supported by the conversation.
- Keep notable assistant recommendations/resources as separate facts when they are specific and named.
- If no durable facts exist, output NONE.
```

Notes:
- The key change is the explicit requirement to extract **side comments** and to use **category-bearing wording**.
- This should turn "Domino's Pizza three times last week" into a retrievable service-use fact rather than leaving it buried in a raw session.

## Risks and guards

### Risk: fact explosion and duplicate clutter

Impact:
- More fact memories per session can increase retrieval noise.

Guard:
- Keep facts atomic and dedupe aggressively at answer time as already done in V2 prompts.
- If needed later, add lightweight exact-string dedupe at ingest, but not in the first V3 cut.

### Risk: normalization hallucination

Impact:
- The extractor may over-label ambiguous mentions as category facts.

Guard:
- Prompt must say "prefer normalized wording when the category is clear" and "do not invent categories."
- Inspector should review failure samples where the main topic is unrelated and the signal is incidental.

### Risk: cost creep

Impact:
- More verbose extraction can increase token usage materially at full500 scale.

Guard:
- Start with the single-pass design.
- Do not introduce a second pass until seed-123 proves single-pass V3 insufficient.

## Validation plan

Primary rule:
- **Seed 123 first.** That is the honest set for this failure mode.

Plan:
1. Implement V3 behind `MEMDIO_EXTRACT_V3=1` only.
2. Run seed-123 n=48 with champion V2.2 flags plus `MEMDIO_EXTRACT_V3=1`.
3. Compare especially the `multi-session` slice against the current 50%.
4. Inspect converted questions manually to verify the win came from new ingest facts, not judge variance.
5. If seed-123 multi-session rises materially without regressing SSU/SSA, run seed 42 for symmetry.
6. Only if single-pass V3 stalls should we prototype two-pass extraction.

Success criteria:
- Multi-session on seed 123 improves clearly above 50%.
- No meaningful regressions in the already-strong categories.
- Retrieval traces show new category-bearing fact memories for previously missed incidental instances.

## Bottom line

The next bottleneck is not smarter retrieval. It is the absence of countable, normalized facts at ingest for incidental multi-session mentions. The cheapest credible fix is a **flag-gated V3 extractor prompt** that stays inside the current `extract_facts() -> ingest_question() -> fact memory` architecture while demanding exhaustive extraction of side-comment instances in category-bearing language.
