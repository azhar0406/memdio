# V3-P preference design — ingest-time preference profile store

Status: design draft (review PENDING). Implementation NOT started.
Branch: `worker2_v3_preference_design`. Validated by: inspector review, then user confirm before merge.
Gating flag: `MEMDIO_PREF_V3=1`.

## Context

- `single-session-preference` is our **weakest type**: **53.3%** on full-500 (n=30),
  vs frontier 96–100% (OMEGA 100%, Mastra 100%, ByteRover 96.7%).
- Frontier playbook (from `sota_research.md`, task-type playbook): preference fix =
  **preference extraction at ingest + advice-question detection + "MUST follow stated
  preferences" reader branch**. We already have the advice-question detector
  (`_PREFERENCE_PATTERNS` in `benchmarks/longmemeval/answer.py`); we are missing the
  ingest-time extraction and the reader branch.
- The full-500 run also showed preference is our single biggest per-question ceiling
  gap: +43pp to frontier is worth **~1.3 absolute pp** on the overall 500-Q number
  (30/500 × 43pp) — the cheapest absolute lever after multi-session (59.4%) and
  temporal (74.4%) extraction work.

## Failure-shape forensics (from `failure_analysis_66422f77.md`)

Of the preference failures in the champion run, two distinct mechanisms — both
happen **even though the relevant preference text was already retrieved**:

| QID | Verdict | What happened |
|---|---|---|
| `1c0ddc50` | C. PREFERENCE-STYLE | Preferences retrieved (rank 8) but answer was generic commute advice, ignoring the stated branch-out/history-science preference. |
| `75832dbd` | B. SYNTHESIS-MISS | Deep-learning-in-medical-imaging interest retrieved (rank 9); model **abstained**. |
| `8a2466db` | B. SYNTHESIS-MISS | Premiere Pro / Lumetri / Curves interests retrieved (rank 2); model **abstained**. |
| `fca70973` | B. SYNTHESIS-MISS | Full theme-park preference frame retrieved (rank 4); model **abstained**. |

Takeaways:
1. **Retrieval is not the blocker.** The preferences are in the top-9 retrieved
   sessions. The problem is *salience* — stated preferences are buried inside long
   raw sessions and the answerer under-weights them versus its "not enough info"
   prior.
2. **Two sub-failures**: (a) generic/non-tailored advice (PREFERENCE-STYLE),
   (b) outright abstinence despite present preferences (SYNTHESIS-MISS).
   Both are cured by **surfacing a clean, consolidated preference profile prominently
   at answer time and forcing the reader to use it**.

## Guiding constraints (hard)

- **Flag-gated**: `MEMDIO_PREF_V3=1` is the only switch. Off ⇒ byte-identical
  behavior to the 74.4% champion run.
- **No champion-prompt edits**: `ANSWER_PROMPT`, `PREFERENCE_PROMPT`,
  `AGGREGATION_PROMPT`, `TEMPORAL_PROMPT` are frozen artifacts of the official
  74.4% run. We **add** a new `PREFERENCE_PROMPT_V3` and select it only under the
  flag — exactly the additive, flag-gated pattern used by `EXTRACT_PROMPT_V3`
  (commit `416093a`, branch `worker_v3_extraction`). This is an addition, not an edit.
- **No regression to other types**: the profile is injected **only** for
  preference-classified questions and only when the flag is set. Aggregation,
  temporal, detail, and all other ingest paths are untouched.
- **Honest (no question-conditioning)**: the profile is derived purely from the
  user's *past stated* preferences at ingest/recall time. It is never conditioned
  on the specific question text. It is a standing user-profile artifact, not a
  query-specific hint. Every profile line must trace to a stored memory.

## Design

### A. Ingest-time preference extraction (additive, flag-gated)

In `benchmarks/longmemeval/extract.py`, add `PREF_EXTRACT_PROMPT` and select it when
`MEMDIO_PREF_V3 == "1"` in the existing `extract_facts()` — mirroring
`EXTRACT_PROMPT_V3` selection.

Each extracted preference becomes its own memory stored with tag **`"preference"`**
(reusing `StorageManager.remember(..., tags="preference")`, the same mechanism that
tags `"fact"` and `"session"` in `memdio/core/storage.py:737`). Atomic, self-contained,
dated like facts.

`PREF_EXTRACT_PROMPT` rules (derived from the failure shapes above):
- Extract the user's **stated preferences, interests, constraints, and current
  setup**, with specifics (exact brands, genres, tools, activities, named topics).
- Capture both **explicit** ("I like X") and **revealed** preferences (the user asked
  for resources on Y, attended Z, is building W) — this directly recovers the
  `75832dbd` / `8a2466db` / `fca70973` synthesis-miss cases where the interest was
  revealed through action, not a literal "I like".
- Prefer **normalized, category-bearing wording**:
  "The user prefers history and science podcasts for commute listening and wants to
  branch out beyond true crime and self-improvement." (the `1c0ddc50` fix)
- Distinguish a **durable standing preference** from a one-off task request.
- Keep any date/context needed to scope the preference.
- `NONE` fallback when nothing durable is stated.
- Do **not** invent preferences not explicitly supported by the session.

### B. Answer-time profile injection (additive, flag-gated)

In `benchmarks/longmemeval/answer.py` `_build_answer_prompt()`:
- When `MEMDIO_PREF_V3 == "1"` **and** `classify_question(question) == "preference"`,
  select a new `PREFERENCE_PROMPT_V3` and prepend a `## User preference profile`
  block built from recalled `preference`-tagged memories.
- The profile block is retrieved via a **targeted preference recall** (a `recall()`
  variant filtered to `tags == "preference"`, analogous to the existing
  `fact_only` filter at `storage.py:764`) — *not* from the raw question context, so
  it stays clean and prominent rather than buried.

`PREFERENCE_PROMPT_V3` differs from the frozen `PREFERENCE_PROMPT` only by:
- referencing the consolidated `## User preference profile` block, and
- a hard **"you MUST tailor every recommendation to the profile; do not give generic
  advice; do not abstain while the profile contains on-topic preferences"** directive.

This is the "MUST follow stated preferences" reader branch the SOTA research calls for.

> Note on honesty: the profile block is labeled as derived from the user's stored
> memories, and the prompt still grounds recommendations in those memories. Injecting
> a precomputed standing profile is **not** question-conditioning — the profile is
> identical regardless of which advice question is asked.

### C. Why this shape (not the alternatives)

- **Answer-time-only extraction** (re-derive preferences from retrieved context each
  question): cheaper per ingest but re-pays extraction every query, still buried in
  noisy context, and doesn't fix salience. Rejected — lower ceiling, repeats cost.
- **Single merged profile document updated at ingest**: clean to inject, but needs a
  merge/dedup step and risks the overwrite anti-pattern (ByteRover found layering
  beats deletion). Discrete `preference` memories reuse the proven fact-store and
  stay retrieval-ranked; consolidation happens cheaply at answer time. **Chosen.**
- **Edit the champion `PREFERENCE_PROMPT`**: forbidden by the frozen-artifact rule.
  Additive `PREFERENCE_PROMPT_V3` under the flag is the compliant path.

## Cost / latency estimate

Same cost shape as `MEMDIO_EXTRACT_V3` (one extra extractor pass per session).

- **Ingest**: +1 LLM call/session for preference extraction when flag is on.
  Full ingest ≈ 23,865 sessions ⇒ ≈ +23,865 extractor calls, ~1.1–1.25× tokens vs
  baseline extractor. No second pass required (single prompt emits both facts and
  preferences when both flags are on — see composition note below).
- **Answer**: preference questions are only ~30/500 (6%). For those, one targeted
  `preference`-tag recall (already indexed, no extra LLM call) + deterministic profile
  block assembly. **+0 LLM calls** if the block is assembled deterministically;
  optional +1 cheap distill per preference question (~+30 calls/run, negligible).
- **Expected cost multiplier**: **≈ 1.1–1.25× ingest tokens**, answer-time cost
  negligible. Comparable to the V3 extraction change already staged on the mini.

### Composition with `MEMDIO_EXTRACT_V3`

If both flags are set, a single extractor prompt can emit facts **and** preferences in
one pass (two clearly-delimited sections), holding the ingest cost at ~1.1–1.25× rather
than 2×. This is the recommended config for the validation run. The two prompts
(`EXTRACT_PROMPT_V3`, `PREF_EXTRACT_PROMPT`) can be concatenated into one combined
prompt gated by `MEMDIO_EXTRACT_V3 == "1" and MEMDIO_PREF_V3 == "1"`.

## Implementation sketch (for later; not part of this design task)

1. `extract.py`: add `PREF_EXTRACT_PROMPT`; in `extract_facts()` add
   `use_pref = os.getenv("MEMDIO_PREF_V3") == "1"`; when set, emit preferences and
   store each with `tags="preference"` via `remember()`.
2. `storage.py` / recall: add a `preference_only` retrieval filter (mirror `fact_only`
   at `storage.py:764`).
3. `answer.py`: add `PREFERENCE_PROMPT_V3`; in `_build_answer_prompt()` select it and
   prepend the profile block when flag + preference class.
4. `tests/`: add tests asserting (a) flag UNSET ⇒ identical to current behavior
   (no preference memories, `PREFERENCE_PROMPT` used), (b) flag SET ⇒ preferences
   extracted, profile block present, `PREFERENCE_PROMPT_V3` selected. Capture actual
   prompt content like the V3 extraction tests (`tests/test_extract.py`).

## Validation plan

Per the manager's run plan: seed-123 first, then full-500 only if it holds.

1. **seed-123 (n=48, 8/task-type, held-out)** on the Mac mini:
   `MEMDIO_EXTRACT=1 MEMDIO_EXTRACT_V3=1 MEMDIO_PREF_V3=1 MEMDIO_ROUTE=1
   MEMDIO_TOPK=20 MEMDIO_PROMPT_V2=1 MEMDIO_EXHAUSTIVE=1 MEMDIO_MULTIWINDOW=1
   MEMDIO_QUERYEXPAND=1`, judge `gpt-4o`, workers 8.
   - **Primary metric**: `single-session-preference` accuracy on seed-123. Baseline
     expectation from champion: ~62.5% (seed-42) — target **≥ 85%** on n=48 to be
     credible; frontier is 96–100%.
   - **Guard metric**: every other type (user, assistant, knowledge-update, temporal,
     multi-session) must **not regress > 1pp** vs the same-flag-config run without
     `MEMDIO_PREF_V3`. This proves the no-regression constraint.
2. **Gate**: if preference ≥ ~85% AND no type regresses > 1pp → proceed.
3. **full-500 re-run** with the same flags if the gate holds (manager's plan:
   "full-500 re-run if it holds"). Confirm overall + per-type, especially preference
   reaching toward frontier and overall gaining the ~1.3pp ceiling.
4. **Inspector review** of this design; **user confirm before merge**.

## Success criteria

- `single-session-preference` ≥ 85% on seed-123 (n=48) and materially higher than
  53.3% on full-500, with **zero regression** on the other five types.
- `MEMDIO_PREF_V3` unset ⇒ behavior byte-identical to the 74.4% champion run
  (verified by tests + a control seed-123 run).
- Honest: profile is grounded in stored memories, never question-conditioned.
