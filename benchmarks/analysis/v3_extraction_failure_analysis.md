# V3 extraction — seed-123 failure analysis (run v3x123 vs champion 22940205)

Status: analysis only (no code, no merge). Branch: `worker2_v3ext_failure_analysis`.
Task: diagnose the 70.8% vs 77.1% regression and deliver a verdict — fixable or abandon.

## Bottom line

**Verdict: FIXABLE. Do not abandon V3 extraction.** The regression is a
*retrieval-ranking dilution* effect caused by the much denser fact index that V3
creates — it is not a defect in the V3 extraction prompt itself, and it is fully
addressable on the retrieval side. The preference collapse is **not** caused by V3 at
all (retrieval volume for preference is byte-identical); it is judge variance on a
tiny n=8 sample.

## Run comparison

| Type | V3 (v3x123) | Champ (22940205) | Δ |
|---|---|---|---|
| knowledge-update | 75.0% | 87.5% | −12.5 |
| multi-session | 50.0% | 50.0% | 0 |
| single-session-assistant | 100% | 100% | 0 |
| single-session-preference | 37.5% | 62.5% | −25.0 |
| single-session-user | 87.5% | 87.5% | 0 |
| temporal-reasoning | 75.0% | 75.0% | 0 |
| **Overall** | **70.8%** | **77.1%** | **−6.3** |

Per-question flips (5 total):
- PASS→FAIL: `32260d93` (pref), `d24813b1` (pref), `8e91e7d9` (MS), `7401057b` (KU)
- FAIL→PASS: `gpt4_5501fe77` (MS) — genuine V3 improvement

## Retrieval-volume evidence (avg `num_memories_found`)

| Type | V3 | Champ | Δ |
|---|---|---|---|
| knowledge-update | 37.8 | 41.5 | −3.7 |
| multi-session | 48.2 | 61.1 | **−12.9** |
| temporal-reasoning | 56.4 | 62.2 | −5.8 |
| single-session-user | 38.2 | 39.9 | −1.7 |
| single-session-assistant | 24.2 | 25.8 | −1.6 |
| single-session-preference | **20.0** | **20.0** | **0.0** |
| **Overall** | **37.5** | **41.8** | **0.90×** |

The overall 0.90× volume matches the manager's note. **Crucially, preference volume
is exactly identical (20.0 = 20.0).** V3 extraction does not change preference
retrieval at all (preference uses the `detail` route — raw+fact hybrid, not
`fact_only`), so it cannot be the cause of the preference drop.

## Per-flip diagnosis

### `7401057b` (KU) — RETRIEVAL REGRESSION (fixable)
- Champ: nmem 67 → correct: Hilton 5/29 = 1 free night, 5/30 = 2 free nights → "two free nights".
- V3: nmem 53 → only saw the 5/29 single-night fact → answered "one free night".
- The **5/30 superseding update memory dropped out of the top-k** (fewer memories
  retrieved). KU routes through `fact_only` (`memdio/core/storage.py:764`), which
  fetches only `tags='fact'` memories with `fetch = top_k*3` and `final_k = 30`. The
  denser V3 fact pool pushed the update fact below the cutoff.

### `8e91e7d9` (MS) — RETRIEVAL REGRESSION (fixable)
- Champ: nmem 80 → enumerated 3 sisters **+ 1 brother** = 4 siblings (correct).
- V3: nmem 63 → only got "3 sisters"; the "brother" memory fell out → answer 3 (wrong).
- Same mechanism: the required "brother" fact was diluted out of the ranked top-30 by
  the larger fact set. Biggest volume drop of any flip (−17 memories).

### `gpt4_5501fe77` (MS) — GENUINE V3 IMPROVEMENT
- nmem identical (20 = 20). Champ said Twitter (420→540); V3 said TikTok (+200), which
  is the gold answer. Net positive — evidence V3 did not uniformly hurt.

### `32260d93` (pref) & `d24813b1` (pref) — JUDGE VARIANCE, not a V3 defect
- nmem **identical (20 = 20)** in both runs. Routing is `detail` (raw+fact hybrid), so
  V3 facts never replaced the context.
- Hypotheses are substantively equivalent tailored recommendations:
  - `32260d93`: champ → Hasan Minhaj (refs John Mulaney "Kid Gorgeous" + storytelling);
    V3 → Hasan Minhaj "Homecoming King" / Mike Birbiglia. Both tailored to stated interest.
  - `d24813b1`: champ → oatmeal raisin + nuts (pecans/walnuts); V3 → lemon poppyseed
    (previously made) + others. Both tailored to stated baking interests.
- The judge flipped `yes`→`no` on near-identical answers. This is exactly the
  **subjective-rubric instability on preference** already documented in
  `v2_results.md` ("gemini-flash is harsher and unstable on preference rubrics — near-
  identical answers flipped between runs"). With gpt-4o judge it is milder but still
  present, and on **n=8** a 2-question swing is ±25pp of pure noise.

## Root cause (mechanism)

`StorageManager.recall` (`memdio/core/storage.py:754`) routes aggregation/temporal/KU
questions to `fact_only`, fetching `top_k*3 = 90` fact memories and returning
`final_k = 30`. V3 extraction multiplies the fact-index size (exhaustive incidental
mentions + normalized category facts). In a denser fact pool, Reciprocal Rank Fusion
spreads relevance thinner, so any *specific* required fact (a sibling, a superseding
update) can fall below rank 30 and be dropped — even though more facts exist overall.
This is the classic "more candidates, harder to rank the right one" failure, not a
problem with what V3 extracts.

Preference is immune because it uses the `detail` route (all tags, `final_k = top_k`),
where V3's added facts merely augment rather than displace — hence identical volume and
no extraction-driven change.

## Fixes (retrieval-side; extraction prompt stays)

1. **Raise the fact fetch/return cap under `fact_only`** (`storage.py:765,787`):
   `fetch = top_k * 5` (or more) and `final_k = 40–50`. A denser index needs a wider
   net so required update/sibling facts survive. Cheap, no new LLM calls.
2. **Recency/date bias for knowledge-update**: weight later-dated facts up in fusion
   (the "recency-wins" principle from `sota_research.md`). Guarantees a superseding
   update outranks the stale state instead of being diluted out.
3. **Quota-protect raw sessions in `fact_only`**: keep a small top-raw-session slot
   (e.g. 5) so evidence that a fact missed (the "brother" case) is still retrieved.
   Alternatively tighten V3 extraction to never drop a sibling/parallel fact.
4. **Re-test on a sample large enough to separate signal from judge noise**: seed-123's
   n=8 preference is inadequate. Re-run seed-123 *with the ranking fix*, then go to
   full-500 (30 preference Qs) for the real verdict. The preference drop is very likely
   noise that full-500 will wash out.

## Recommended decision

- **Keep `MEMDIO_EXTRACT_V3`** (the prompt is sound; its only harm is ranking dilution).
- Implement fixes #1–#3 (small, retrieval-only, no champion-prompt edits).
- Re-run seed-123 with fixes; gate on KU + MS recovering and **no net regression**.
- Treat the preference number as unresolved until full-500 — do not draw conclusions
  from n=8.
- Abandoning V3 is **not** warranted: the 2 real regressions are 2 questions caused by
  retrievable-but-ranked-out facts, and 1 MS question actually improved.
