# V4: bounded incidental-instance extraction with protected retrieval

Status: proposal only; no implementation or benchmark result. Code references are
against `89eb95e` (docs-only delta since `64301d5`, line numbers unchanged); the
`feat_question_types` runner changes (`38e8784`) are already integrated into main.
All candidate behavior is enabled only by
`MEMDIO_EXTRACT_V4=1`; the unset path must remain byte-identical under fixed inputs
and mocked model responses. The 74.4% full-500 champion remains the baseline.

## Goal and evidence

Multi-session (MS) is 133/500 questions at 59.4%, roughly 79/133 correct, and is the
largest near-term opportunity. A net 11 additional MS passes yields +8.27pp MS and
+2.2pp overall if every other label is unchanged; this is a target, not a forecast.

V3 extraction failed seed-123: preference 62.5 -> 37.5 and knowledge-update (KU)
87.5 -> 75. The existing [V3 analysis](v3_extraction_failure_analysis.md) attributes
this to denser facts displacing useful evidence. Its attribution caveat matters:
EXTRACT_V3 lacked a same-branch no-flag control. Identical retrieved counts do not
by themselves prove identical evidence or a specific ranking mechanism. V4 must
record retrieved IDs, source sessions, ranks, and context rather than infer them
from `num_memories_found` or a later stochastic replay.

Known development probes from [historical analysis](failure_analysis_66422f77.md):

| Probe | Actual type / observed failure | V4 obligation |
|---|---|---|
| `d682f1a2` | MS: missing Domino's/Uber Eats sessions | Preserve grounded incidental use of named services with category wording |
| `gpt4_59c863d7` | MS: kit evidence — **PASSED in full500** | Regression guard, not a gain target: preserve distinct acquisitions/builds without conflating repeat mentions |
| `69fee5aa` | **KU**, not MS: prior 37 coins and later acquisition were both retrieved, but answer stayed 37 | Preserve update semantics and both anchors; retrieval improvement alone cannot establish a fix |

These are previously inspected development examples, not held-out proof. Some
were already helped by V2 query expansion/multi-window retrieval. No question ID,
gold answer, or benchmark-specific list of brands belongs in the runtime prompt.

## 1. Flag boundary and actual call paths

The benchmark calls `ingest_question -> hybrid_search -> format_context`, not
`StorageManager.recall()`. Implement and test the two retrieval entry points
explicitly; a core-only change would not affect benchmark results.

- `benchmarks/longmemeval/extract.py:extract_facts`: select a V4 prompt and bounded
  extraction budget only inside the flag branch. Keep `MEMDIO_EXTRACT=1` as the
  existing extraction prerequisite. V4 is an additional opt-in, not implicit ingest.
- `benchmarks/longmemeval/search.py:hybrid_search` and
  `memdio/core/storage.py:recall`: select the V4 retrieval policy inside the same
  flag branch. Existing rerank modes and limits remain untouched when unset.
- `benchmarks/longmemeval/ingest.py`: V4-only provenance sidecar and extraction
  telemetry; keep public fact strings and `tags='fact'` / `tags='session'` intact.
  The sidecar maps fact/raw memory IDs to source session ordinal and text hash.
- Proposed helpers/constants may live in a new core V4 retrieval module imported
  lazily only when enabled. Do not mutate the generic RRF default or schemas for
  ordinary callers. `remember()` extraction is a separate path; this proposal
  does not silently replace its production prompt.
- All V3 extraction, preference, and event-date flags are unset in both arms.
  No V3 implementation branch is a prerequisite.

Byte-identical means the old prompt, API parameters, parsing, search calls,
returned memory dicts/order, and formatted context are identical in deterministic
regression fixtures; it cannot mean identical independently sampled LLM outputs.
Test unset and `=0`, including empty retrievals and semantic-search fallback.
V4 diagnostics are absent when unset. An external measurement harness can observe
the unchanged A/A API responses without changing the production control path.

## 2. Raise candidate budgets without starving raw evidence

Proposed frozen first-candidate constants (values to validate, not tuned results):

| Constant | V4 value | Application |
|---|---:|---|
| `V4_FACT_FETCH_MULTIPLIER` | 6 | `recall()` fetch and `hybrid_search()` fetch_k |
| `V4_FACT_FETCH_MIN` | 120 | Fact-only candidate floor |
| `V4_FACT_FETCH_MAX` | 240 | Bound per-channel fact candidate target |
| `V4_FACT_FINAL_K` | 60 | Fact-only fused return budget, previously 30 |
| `V4_RAW_FLOOR_RATIO` | 0.5 | Detail route raw-session minimum; 10 at top_k=20 |
| `V4_EXHAUSTIVE_FACT_MAX` | 60 | Aggregate fact/related-fact budget |
| `V4_EXHAUSTIVE_RAW_MAX` | 20 | Protected raw budget within total 80 |

Use `fetch = min(240, max(120, top_k * 6))` on fact-only routes. Existing code is
`fetch=top_k*3`, `final_k=30` in `storage.py:765,787` and
`fetch_k=top_k*3`, `final_k=30` in `search.py:121,182`. The champion top_k is 20,
so its nominal fetch is **60**, not 90; 90 applies only to a top_k of 30. Detail
candidate pools must also overfetch (up to 120 per channel) to supply raw quotas.

Three downstream constraints must be addressed, rather than merely changing 30:

1. `StorageManager.search()` has SQL `LIMIT 100` **before** Python filtering.
   Add a V4-private, parameterized FTS candidate query with exact tag predicate and
   requested limit; keep public `search()` defaults untouched. Raw/fact pools are
   queried separately so extra facts cannot consume the raw candidate quota.
2. `semantic_search()` returns **no tags** at this baseline. Resolve tags for its
   IDs with a batched metadata lookup inside the V4 adapter. Never treat an unknown
   tag as a raw session. Avoid the earlier public semantic-search tags-contract
   regression. Adaptively overfetch vector candidates (120, 240, 480, max 960)
   until the requested tagged pools are filled or exhausted; log shortfalls and
   scan cost. This is bounded best-effort recall, not a guarantee of raw recall.
3. `hybrid_search()` eventually slices exhaustive output to 80 after adding facts
   before raw, and non-exhaustive output to `top_k+5`. A V4 path must carry the
   intended final budget all the way through: 60 facts plus up to 20 raw for
   exhaustive routes, at most 60 on non-exhaustive fact-only routes, and top_k
   primary detail results plus at most five related results. Reserve raw slots
   **before** slicing; relation additions count against the same budgets.

Leave champion `MEMDIO_EXHAUSTIVE_MAX=80`, `MEMDIO_EXHAUSTIVE_RAW=20`, and
`MEMDIO_MEMCHARS=4000` unchanged for the first pair. V4's 60/20 allocation is
capped by these existing limits if explicitly lowered. Report count and character
budget usage by channel and route. More candidates need not mean more final text.

## 3. Bounded recency in fusion, not unconditional newest-first

Core `recall()` always uses RRF. The champion benchmark uses **MEMDIO_RERANK=0**
(FTS then semantic), so modifying RRF alone would be inert there. Under V4 only,
explicitly select this protected RRF policy in both entry points and log the
effective policy; the control still executes its original `RERANK=0` branch.
Do not flip the environment variable for both arms. This is a bundled retrieval
treatment and should be described as such, not a prompt-only comparison.

Keep `reciprocal_rank_fusion(rankings, k=60)` unchanged. In the V4 caller, retain
its `(id, score)` pairs; apply the following **before** final_k slicing:

`score_v4(d) = score_rrf(d) * (1 + 0.10 * recency_percentile(d))`

Apply the bonus only to query-relevant facts with explicit update/current-total
language (e.g. 'now have', 'bought another', 'updated to') and at least one
non-stopword query/entity match. Rank valid `document_date` values within that
eligible set to obtain a percentile in [0,1]; equal dates share a percentile,
single eligible candidate gets zero, unknown/unparseable dates get zero.
Parse LongMemEval slash/date-time formats to comparable timestamps; do not compare
raw mixed-format strings. Never use ingest `created_at` as evidence of recency,
or an LLM-resolved V3 event date. Preserve original RRF order on ties.

The multiplicative bonus is capped at 10%: recency cannot promote unrelated
global newest records. Preserve the unboosted top 30 facts, union in the boosted
top 60, then fill/cap to 60 by boosted order while retaining those protected 30.
Thus old totals needed to combine with a later acquisition are not discarded merely
for being old. Unknown dates remain valid evidence; recency is not supersession.
Do not change `is_current` or infer contradiction relations from the bonus.

Record whether a missed update entered the candidate pool, survived the final
set, and remained inside formatted context. If it was never extracted, the ranker
cannot recover it; if both anchors were already present (`69fee5aa`), a remaining
failure is reader synthesis and must not be attributed to extraction recall.

## 4. Quota-protect raw sessions on the detail route

For top_k=20, reserve ten raw sessions and allow the remaining ten slots to be
filled by the best remaining raw/fact results. Compute raw-only RRF ranks from
tag-resolved FTS and semantic raw pools, ignoring fact ranks; raw candidate retrieval
itself must use the separated FTS and adaptive semantic mechanism above.

Take the best `ceil(top_k/2)` raw IDs first, then fill remaining slots from the
combined ranking excluding selected IDs. If fewer raw sessions exist, use all of
them and backfill from facts; log quota shortfall. Preserve deterministic combined
rank order for the selected set. Related results cannot evict reserved raw IDs.
An ordering query may classify as detail while triggering exhaustive retrieval:
carry the protected raw set into that 60/20 allocator rather than dropping it.

This protects evidence diversity, not preference correctness. Check actual
preference source-session coverage and formatted windows on all 30 questions.
Ten irrelevant raw sessions are not success; the guard run is decisive.

## 5. Incidental-instance prompt without exhaustive entity noise

Start from the existing durable-user-fact and specific-assistant-advice rules.
Replace V3's extract-every-mention policy with this proposed addition:

> Preserve an incidental mention when it explicitly says the user actually used,
> acquired, built, visited, or completed a named thing, even if it is not the main
> topic. Emit one concise fact per distinct evidenced event/state. Include the
> category noun when supported by the text (delivery service, model kit, coin),
> the named instance, quantity, action/status, and any stated date. Distinguish an
> explicit current total from a later addition. Preserve uncertainty and whether
> an item was merely recommended, considered, already owned, or actually acquired.
> Do not turn recommendations or hypothetical examples into user actions. Do not
> infer use from a brand name alone. Collapse paraphrases of the same event within
> this conversation; do not merge explicitly separate purchases or visits. Do not
> emit extra synonym/category-only facts or facts about every named entity.
> Prioritize explicit totals/updates and actual user events over assistant advice
> when the output budget is tight. Keep facts grounded in the conversation only.

Keep one fact per line and the current parser. One category-bearing fact should
serve lexical search and answer evidence together; do not expand each fact into
multiple searchable aliases. Preserve event identity/quantity, not just item name:
two purchases of the same kit are not necessarily duplicates. Do not generate new
calendar dates or resolve ambiguous relative dates in this experiment.

Probe expectations (assert only against the actual source text): delivery-service
use becomes countable even in an aside; distinct kit acquisitions retain model
and action status; the coin case retains both the earlier count and later added
coin, without inventing a new total. No gold-derived examples are inserted into
the prompt. Evaluate fact/source precision on a blinded sample as well as recall.

## 6. H2: measure extractor truncation before choosing a larger cap

At `extract.py:50`, OpenAI Responses uses `max_output_tokens=700`; at lines 53-58,
Chat Completions uses `max_tokens=700`. Both currently return only parsed facts,
discarding finish/usage metadata. H2 remains **unverified**.

On the Mac mini, wrap the existing `extract_facts` client calls with an external
measurement harness using `format_session`, the exact existing prompt/model, and
700-token requests. First enumerate all selected haystack session occurrences and
unique `(session text hash, date)` pairs without API calls. Measure a reproducible
pilot of 40 unique sessions spanning input-length quartiles (10 each), plus the
known development probe sessions; report these strata separately. Then collect
the same telemetry across the full control/variant runs. Do not estimate incidence
from only failure examples or only long sessions.

For each actual request/attempt log run ID, question ID, source ordinal/hash,
prompt hash, model/provider, input chars/tokens, output tokens, parsed fact count,
latency, error, and:

- Responses: `status`, `incomplete_details.reason`, `usage.output_tokens`.
- Chat: `choices[0].finish_reason`, `usage.completion_tokens`.
- Confirmed cap hit: provider reports `max_output_tokens`/`length`; separately
  count outputs at >=95% of 700 as near-cap, not proven truncation. Missing usage
  is unknown, not zero/not-truncated. Count distinct sessions with any cap hit,
  final-attempt cap hits, and attempts separately so retries do not inflate rates.

For confirmed hits, re-extract the same pilot session once with 1400 tokens;
compare late-source fact coverage, malformed last lines, and new unsupported facts.
A changed output alone does not prove truncation caused a benchmark miss. Review
source-grounded missing updates and whether the larger output restores them.

First V4 candidate: 700 initial output tokens; **only when the provider confirms
truncation**, retry once at 1400 under the V4 flag, replacing rather than appending
the truncated extraction. If still truncated, retain parseable complete lines,
record unresolved truncation, and rely on the protected raw source. No unbounded
retry or silent fact-count chopping. Freeze this policy before the full pair.
The control remains the original 700-token behavior. Gate-off tests verify exact
API arguments; diagnostics record added calls and index-growth distribution.

## 7. Same-day validation protocol and explicit gates

All LLM work runs through the benchmark owner over SSH on the Mac mini
(`qoneqttesting@100.71.241.65`). Local unit tests/retrieval fixtures are fine.
Use the integrated runner with `--question-types` and `--run-id`; no `--limit` or
`--stratified` in the decisive runs. Question types are exact dataset values, not
the query classifier's aggregation/temporal/detail labels.

Freeze one code SHA, dataset hash, dependency versions, prompt hash, providers,
models, and complete champion environment. Use **gpt-4o answer and official gpt-4o
judge** explicitly: the OpenAI judge default is gpt-4o-mini and must be overridden.
Use the same extractor/model/provider in all arms; reproduce the recorded champion
provider combination rather than silently substitute one. Existing OpenRouter
configuration permits gpt-4o answer/judge with Gemini Flash extraction:

```sh
export MEMDIO_EXTRACT=1 MEMDIO_EXTRACT_MODEL=google/gemini-2.5-flash
export MEMDIO_ROUTE=1 MEMDIO_TOPK=20 MEMDIO_MEMCHARS=4000 MEMDIO_RERANK=0
export MEMDIO_PROMPT_V2=1 MEMDIO_EXHAUSTIVE=1 MEMDIO_MULTIWINDOW=1 MEMDIO_QUERYEXPAND=1
unset MEMDIO_EXTRACT_V3 MEMDIO_EVENTDATE_V3 MEMDIO_PREF_V3 MEMDIO_EXTRACT_V4
python -m benchmarks.longmemeval.run --provider openrouter --model openai/gpt-4o \
  --judge-model openai/gpt-4o --question-types multi-session --run-id v4ms_a1 --workers 8
# Repeat unchanged as v4ms_a2 (A/A); then add only MEMDIO_EXTRACT_V4=1 as v4ms_b.
```

Use campaign-unique run IDs rather than reusing these illustrative names: the
runner loads existing checkpoints for any matching run ID, including `--run-id`.
Verify exact question-ID equality and 133 records in each MS arm. Run one job at
a time; `--workers<=8` is mandatory. Recommend **8** workers — proven on the July
full-500 run and today's three n=30 runs without incident (the one OOM was at 10).
`ingest_question()` already runs eight extraction threads per question, so
`--workers 8` can mean up to 64 concurrent extraction calls; treat that as an
observation, not a constraint. Timing basis: 30 questions finished in ~8 minutes
at workers=8, so a 133-question MS arm is ~36 minutes — the same-day A1/A2/B
protocol is feasible at 8 and infeasible serial.

1. Same-day A1/A2 control replicates and B variant, all 133 MS questions. Predeclare
   A2 as comparator, A1 estimates drift; also require the MS improvement against
   A1 so a weak control cannot manufacture a pass. Interleave matched question
   batches in execution if practical, preserving each complete arm and ID set.
2. Same-day A1/A2/B guards using
   `--question-types single-session-preference,knowledge-update`, unique run IDs,
   all 30 preference + 78 KU. Compare **per type**, not the pooled 108-question rate.
  3. If those pass, run A1/A2/B on the remaining 70 user + 56 assistant + 133 temporal
     questions using the same type filter. If this cannot be completed same-day,
     run fresh same-day controls for that stage. The initial guard does not certify
     the other three types. Stop early on a failed gate rather than tune on the run.

**A/A flip-floor rule (learned from the n=30 preference campaign, 2026-09-10):** every
validation must start with an A/A pair to measure the flip floor before judging the
variant. In the preference n=30 run, two identical controls differed by only 2/30 flips
(`32260d93` P→F, `afdc33df` F→P), while the `MEMDIO_PREF_V3` intervention produced 12
flips versus control — six times the A/A floor. A per-question flip count at or below the
A/A floor cannot be attributed to the treatment. The A2 arm is therefore not merely a
drift check; it is the mandatory measurement of the flip floor against which the
variant's flips are compared before any PASS/FAIL verdict. Report the A/A flip count and
the variant flip count side by side in every validation summary.

Acceptance gates, using exact counts before rounding:

- MS: variant >= control **+8 percentage points**, against both A1 and A2. At 133
  questions this requires at least **11 net additional passes** versus each.
- Every other type: variant >= control **-5 percentage points**, against both
  same-day controls. Maximum net losses: preference 1/30, KU 3/78, user 3/70,
  assistant 2/56, temporal 6/133. All types must be measured before acceptance.
  A preference net loss of 1/30 that does not exceed the A1/A2 spread is
  **inconclusive — rerun** per the instability rule, not FAIL.
- A/A spread exceeding 5pp in a type flags instability: report inconclusive and
  collect a fresh matched replicate; do not pick the lower control after seeing B.
- Zero unresolved API-error records, unique complete matched question IDs,
  non-abstention/abstention breakdown, and flag/prompt provenance verified.
  Infrastructure failures require a clean documented matched rerun, not silent
  exclusion of failed questions or dilution of the denominator.
- Report win/loss pairs, paired uncertainty (e.g. bootstrap interval and McNemar
  discordant counts), fact density/precision, source recall, context lengths,
  truncation, latency, and cost. The numerical gate is an engineering acceptance
  rule, not a claim of statistical significance or generalization beyond this set.

If the bundle fails, diagnose with frozen offline retrieval fixtures and a new
predeclared ablation campaign (bounded extraction versus retrieval policy). Do not
keep selecting prompts against these known labels. Any accepted code stays opt-in
until manager approval; publish a new full-500 number only from complete results.

## 8. Cost/time planning (estimates, not observed V4 measurements)

The V2 planning note estimates a full 500-question run at **$25-40 / 2-3 hours**.
That is a rough historical planning anchor, not verified current pricing or a
guarantee: session counts, provider choice, retries, and mini OOMs dominate cost.

Linear reference estimates for **three arms** (A1/A2/B):

| Stage | Question executions | Historical-rate dollars | Historical-rate hours |
|---|---:|---:|---:|
| MS all 133 | 399 | $20-32 | 1.6-2.4 |
| Preference + KU guards | 324 | $16-26 | 1.3-1.9 |
| Remaining-type guards | 777 | $39-62 | 3.1-4.7 |
| Complete acceptance campaign | 1,500 | $75-120 | 6-9 |

Allow 1.5x financial and 2x elapsed-time headroom initially: **$54-87 / 6-9 hours**
for MS + first guards; **$113-180 / 12-18 hours** for all stages, excluding a fresh
day's repeated controls if required. Serial workers may exceed even that estimate;
do not call a multi-day comparison same-day. The pilot determines a realistic plan.

Pilot extraction cost must be estimated from measured tokens, not 40 question
runs: for session j, `cost_j = input_tokens_j * input_price/1e6 +
output_tokens_j * output_price/1e6`, summing all attempts including cap retries.
Use current provider prices recorded at execution and pilot p50/p95 latency.
Add answer, judge, query-expansion usage and local embedding/FLAC time separately.
Project using actual session occurrences for the 241-question first-stage set;
report unique-session counts only as a possible future cache opportunity. The
current runner re-extracts per question, so assuming a shared cache understates cost.
For initial pilot planning allow roughly $1-5 and 15-45 minutes, explicitly replace
this placeholder with token/latency projection before launching the complete arms.

## 9. Implementation review checklist

- Gate-off fixtures prove exact prompt/API/search/context parity for both entry
  points. No global change to semantic-search returned keys or generic RRF.
- Fixture with >100 FTS hits proves V4 actually reaches deeper fact candidates;
  tagless semantic records and fact-heavy raw starvation are covered.
- Newer relevant update survives without evicting old count anchors; unknown
  dates and repeated/tied dates are deterministic. No new event-date inference.
- Detail raw quota survives relation expansion and exhaustive final slicing.
- Recorded cap-hit responses exercise one retry, second cap, unknown usage, and
  failure paths. No test performs an LLM call.
- Matched all-type validation meets the gates above; report negative results
  with the same provenance as positive ones. This design is not a merge verdict.
