# V3-T temporal design draft — event-date resolution at ingest

Context:
- Full500 temporal-reasoning landed at **74.4%** over **133 questions**, making it the second-largest absolute lever after multi-session.
- Seed-42 forensics already showed the recurring failure shape: the benchmark often stores the **session date**, while the question depends on the **event date** embedded inside the session text.
- The codebase already has dual timestamp fields in storage: `document_date` and `event_date`. The benchmark path does not reliably populate `event_date` for extracted facts in a way that temporal ordering can trust.

Scale assumptions:
- LongMemEval-S full set: **500 questions**.
- Average sessions/question: **47.7**.
- Full ingest pass: about **23,865 sessions**.
- Any second ingest-time pass roughly doubles call count unless aggressively gated.

## Problem statement

Temporal questions often ask about when something actually happened:
- "last Tuesday"
- "two weeks ago"
- "visited the museum today"

But current benchmark ingestion mainly anchors memories to the **conversation/session date**. That is sufficient for knowledge-update supersession, but it is not sufficient for event ordering when:
- the event happened earlier than the session date
- the session mentions multiple dated events
- the ordering question spans many sessions and the wrong anchor collapses them into misleading chronology

The core issue is not retrieval surface form. It is timestamp semantics. We need a more reliable **event-date signal** for extracted facts and temporal retrieval.

## Option survey

### Option A — ingest-time dual timestamps on extracted facts

Design:
- During fact extraction, resolve an explicit `event_date` per extracted fact whenever the fact text supports it.
- Keep `document_date` as the conversation timestamp.
- Store both on the fact memory so temporal search and chronological formatting can prefer `event_date`.

What this means in practice:
- Raw `session` memory keeps the original session date.
- Each extracted `fact` can carry:
  - `document_date`: when the conversation happened
  - `event_date`: when the mentioned event happened
- Relative phrases such as "last Tuesday" or "two weeks ago" are resolved against the session date at ingest time.

Pros:
- Best fit for temporal ordering questions.
- One resolution cost per fact at ingest, not repeated at answer time.
- Reuses the existing `event_date` column, `temporal_search()`, and `format_context()` preference for date-bearing memories.
- Composes naturally with `MEMDIO_EXTRACT=1` and the upcoming extraction V3 work.

Cons:
- Requires the extractor to return more structure than plain free-form facts, or requires a follow-up resolver over each emitted fact.
- Some sessions mention multiple events with different dates; naive single-date extraction per whole session is not enough.
- Ambiguous relative references can still fail if the session text lacks enough anchoring detail.

Cost / latency estimate:
- If done as a **deterministic post-pass per extracted fact** with no extra LLM call: about **1.0x current calls**, with small CPU-only overhead.
- If done by asking the extractor to emit fact + event date in one LLM pass: still **1.0x current calls**, likely **1.1x-1.25x tokens**.
- If done with a second LLM resolver pass per session/fact block: **1.8x-2.0x calls**, which is too expensive for a first cut.

### Option B — answer-time event-date resolution

Design:
- Leave ingest unchanged.
- At answer time, resolve event dates from retrieved memories before or inside the answer prompt.

Variants:
- deterministic preprocessor over retrieved memories
- extra LLM "temporal distill" pass over retrieved context

Pros:
- No ingest migration.
- Easy to A/B because it affects only temporal questions.
- Can leverage the full retrieved context when a fact is ambiguous by itself.

Cons:
- Pays the resolution cost on every query instead of once at ingest.
- Only helps facts that were retrieved in the first place.
- Makes the answer path more complex and latency-sensitive.
- Less useful for temporal search because retrieval still happens before resolution.

Cost / latency estimate:
- Deterministic preprocessor only: **0 extra LLM calls**, but repeated per question.
- LLM temporal distill per temporal question: about **1 extra LLM call/question**. On full500, temporal is 133 questions, so roughly **133 extra calls/run**.
- Token cost is lower than full ingest-time reprocessing, but the improvement ceiling is lower because retrieval quality is unchanged.

### Option C — time-aware query expansion

Design:
- Expand temporal queries with explicit date-related hints or entity/date terms before search.
- Example: ordering questions gain "visited", "on", month names, weekday variants, etc.

Pros:
- Cheap relative to full extraction changes.
- Can improve recall of temporal evidence that already exists.
- Useful as a complement to better event dates.

Cons:
- Does not solve the core timestamp problem.
- Expands retrieval vocabulary, not timestamp accuracy.
- Cannot recover cases where every fact is anchored to the wrong date.

Cost / latency estimate:
- If done with current LLM expansion path: roughly **1 extra expansion call per temporal/exhaustive query**.
- On full500 temporal alone: up to **133 extra calls/run**.
- Helps retrieval breadth, not chronological correctness.

### Option D — hybrid: ingest-time fact dates plus lightweight answer-time repair

Design:
- Primary fix at ingest: populate `event_date` for extracted facts.
- Secondary fix at answer time: only when retrieved memories still lack resolved dates or disagree, run a cheap deterministic repair/sort step.

Pros:
- Best accuracy ceiling without forcing a full second ingest pass.
- Keeps temporal retrieval aligned with stored event dates.
- Contains answer-time complexity to edge cases instead of every question.

Cons:
- More moving pieces than a pure ingest-only solution.
- Requires clear precedence rules between `document_date` and `event_date`.

Cost / latency estimate:
- Baseline ingest cost close to Option A.
- Answer-time repair cost near zero if deterministic and only applied on temporal questions.

## Recommended design

Recommend **Option D with Option A as the primary lever**:
- make ingest-time event-date resolution the authoritative fix
- keep a tiny deterministic answer-time fallback for unresolved or conflicting memories

Why:
- Temporal questions fail because the stored date meaning is wrong or incomplete.
- The storage layer already supports `event_date`; the benchmark pipeline should start using that capability instead of trying to reason around missing timestamps later.
- This composes cleanly with extraction V3: once facts become more exhaustive, each fact also needs the right event anchor.

## Proposed flags

- `MEMDIO_EXTRACT=1` remains the master switch for extracted facts.
- `MEMDIO_EXTRACT_V3=1` controls the richer exhaustive fact prompt.
- New temporal flag: `MEMDIO_EVENTDATE_V3=1`

Behavior:
- If unset, benchmark behavior stays unchanged.
- If set with extraction enabled, extracted facts should attempt explicit event-date resolution at ingest.
- This should be orthogonal to prompt V2/V3 answer routing and to exhaustive retrieval.

## Composition with the current pipeline

Current relevant behavior:
- `ingest_question()` stores raw sessions and extracted facts.
- `StorageManager.store()` already accepts both `document_date` and `event_date`.
- If `event_date` is omitted, storage tries `_extract_event_date(content, document_date)`.
- `temporal_search()` already queries by `event_date`.
- `format_context()` already knows about both `document_date` and `event_date`.

Recommended V3-T composition:
1. Keep raw session storage unchanged.
2. For extracted facts, preserve the session date as `document_date`.
3. Under `MEMDIO_EVENTDATE_V3=1`, ensure each fact gets its own resolved `event_date` when possible.
4. Prefer fact-level event resolution over whole-session event extraction, because a single session can mention multiple differently dated events.
5. Leave answer-time formatting to prefer `event_date` whenever present.

That means the benchmark path does not need a new schema. It needs more reliable population of fields that already exist.

## Implementation shape to aim for later

This is analysis only, but the cleanest later implementation is:
- have extraction emit a richer per-fact representation internally, such as `(fact_text, event_date?)`
- immediately store each fact with:
  - `document_date=session_date`
  - `event_date=resolved_event_date`

Two credible ways to get there:

### Path 1 — prompt-assisted structured extraction

- Ask the extractor to output one line per fact with an optional resolved event date field.
- Example internal representation:
  - `event_date=2023-05-13 | The user visited the museum.`
  - `event_date=UNKNOWN | The user planned another visit.`

Pros:
- Best chance of tying the correct date to the correct fact.

Cons:
- Requires parser changes and stronger prompt discipline.

### Path 2 — deterministic post-resolution over extracted facts

- Keep fact text extraction free-form.
- Run `_extract_event_date(fact_text, session_date)` on each extracted fact before storing it.

Pros:
- Lower implementation risk.
- Reuses the existing date resolver.

Cons:
- Relative dates that disappear during normalization can no longer be resolved.
- If the extracted fact loses phrases like "last Tuesday", the resolver has nothing to work with.

Recommendation:
- Prefer **Path 1** for V3-T because event-date fidelity is the whole point.
- Keep deterministic post-resolution as a fallback for absolute dates and simple relative phrases.

## Prompt sketch

Prompt intent:
- preserve event-time phrases during extraction
- resolve them against the session date when clear
- keep output tied to one fact at a time

Sketch:

```text
Extract atomic FACTS about the USER from the conversation below.

For each fact, also determine the EVENT DATE when the fact clearly refers to a specific event.
- Use the conversation date as the anchor for relative phrases like "yesterday", "last Tuesday", or "two weeks ago".
- Keep the session date separate from the event date.
- If the fact describes something that happened on the conversation date, event_date equals the conversation date.
- If the event date is unclear, mark it UNKNOWN rather than guessing.
- Do not invent dates or categories not supported by the conversation.
```

Output format for later implementation should be structured enough to parse reliably, for example one record per line with explicit `event_date=...`.

## Risks and guards

### Risk: multiple event dates in one session

Impact:
- Whole-session timestamp extraction picks only one "prominent" date and contaminates unrelated facts.

Guard:
- Resolve dates per extracted fact, not per session.

### Risk: normalization erases temporal cues

Impact:
- If the extractor rewrites "last Tuesday" into a timeless fact, later deterministic resolution cannot recover it.

Guard:
- Require the extraction prompt to preserve event-time language or output an explicit event-date field directly.

### Risk: bad relative-date resolution

Impact:
- Wrong anchor arithmetic silently creates incorrect chronology.

Guard:
- Start with the existing `_extract_event_date()` logic as the deterministic baseline.
- Validate on hand-picked failures involving weekday references and relative intervals.

### Risk: extra complexity overlaps with extraction V3

Impact:
- Two concurrent changes to extraction can interfere.

Guard:
- Compose via flags: `MEMDIO_EXTRACT_V3` for richer facts, `MEMDIO_EVENTDATE_V3` for temporal resolution.
- Validate temporal date resolution on top of the best available extraction branch, not against stale extraction behavior.

## Validation plan

Primary rule:
- **Seed 123 first.** Same honesty rule as V3 extraction.

Plan:
1. Implement V3-T behind `MEMDIO_EVENTDATE_V3=1` only.
2. Run seed-123 n=48 with champion flags plus extraction V3 if that branch is the active candidate.
3. Focus on temporal slice accuracy, currently 75% on both small seeds and 74.4% on full500.
4. Inspect converted examples manually to confirm the win came from corrected event chronology, not retrieval drift.
5. Specifically test known shapes:
   - weekday-relative events ("last Tuesday")
   - ordering across multiple visits
   - same-session multiple events
6. If seed-123 improves cleanly, rerun seed 42 for symmetry.

Success criteria:
- Temporal accuracy improves without regressing KU or SSU/SSA.
- Retrieved facts show meaningful `event_date` population rather than just `document_date`.
- Converted cases are explainable by corrected chronology.

## Bottom line

Temporal is no longer a retrieval-only problem. The pipeline already has a place to store both session time and event time; the benchmark path simply needs to populate `event_date` at the **fact level** instead of relying on session dates and answer-time guessing. The next honest step is a flag-gated **V3-T ingest-time event-date design** that composes with extraction V3 and uses answer-time repair only as a fallback.
