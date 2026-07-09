# V3 extraction & EVENTDATE_V3 — seed-123 failure analysis (v3x123, vt123, ctrl123)

Status: analysis only (no code, no merge). Branch: `worker2_v3ext_failure_analysis`.
Covers all three seed-123 runs, all local in `benchmarks/results/`:
- `v3x123` — V3 extraction (`MEMDIO_EXTRACT_V3=1`), branch `worker_v3_extraction` @ `416093a`
- `vt123` — EVENTDATE_V3 (`MEMDIO_EVENTDATE_V3=1`, EXTRACT_V3 unset), branch `worker_v3_temporal_impl` @ `4eaf3c3`
- `ctrl123` — A/A control: exact champion flags, **no V3 flags**, on `4eaf3c3` (same base as vt123)

## FINAL VERDICTS (per ctrl123 control)

1. **V3 extraction FAILED seed-123.** pref 37.5 (vs control 62.5, −25pp), KU 75 (vs 87.5,
   −12.5pp). Control reproduces the champion-era pref/KU rates with no V3 flag, so the
   damage is attributable to `EXTRACT_V3`. temporal and MS unchanged. **Do not merge.**
2. **EVENTDATE_V3 FAILED seed-123.** temporal 50 (vs control 75, −25pp), pref 25 (vs 62.5,
   −37.5pp). Control (same branch, no flag) proves the damage is attributable to
   `MEMDIO_EVENTDATE_V3`. KU and MS unchanged. **Do not merge.**
3. **`8e91e7d9` is PFFF (permanently flipped since 2026-07-06)** — genuine model/env drift,
   not a V3 effect. It fails in champ-era, control, v3x123, and vt123 alike. Today's true
   same-day baseline MS is **25 (1/8)**, not the Jul-6 50. **All future comparisons need a
   same-day control; never compare against the Jul-6 77.1% alone.**

## Per-type panel (seed-123, n=48, 8/type)

| Type | champ(22940205, Jul-6) | ctrl123 (no flag) | v3x123 (EXTRACT_V3) | vt123 (EVENTDATE_V3) |
|---|---|---|---|---|
| single-session-preference | 62.5 | 62.5 | 37.5 | 25.0 |
| knowledge-update | 87.5 | 87.5 | 75.0 | 87.5 |
| single-session-user | 87.5 | 87.5 | 87.5 | 87.5 |
| single-session-assistant | 100 | 100 | 100 | 100 |
| temporal-reasoning | 75.0 | 75.0 | 75.0 | 50.0 |
| multi-session | 50.0 | 25.0* | 50.0 | 50.0 |

\* ctrl123 MS=25 reflects the `8e91e7d9` permanent drift (PFFF); champ's 50 had it passing.

## Flip panel (PASS/FAIL across runs)

| QID | type | champ | ctrl | v3x | vt | meaning |
|---|---|---|---|---|---|---|
| `8e91e7d9` | MS | P | F | F | F | **PFFF — permanent drift**, not V3 |
| `bb7c3b45` | MS | P | F | P | P | run-to-run variance (PFPP) |
| `gpt4_5501fe77` | MS | F | F | P | P | run-to-run variance (FFPP) |
| `32260d93` | pref | P | P | **F** | **F** | fails ONLY under a V3 flag |
| `d24813b1` | pref | P | P | **F** | **F** | fails ONLY under a V3 flag |
| `38146c39` | pref | P | P | P | **F** | fails ONLY under EVENTDATE_V3 |
| `bbf86515` | temporal | P | P | P | **F** | fails ONLY under EVENTDATE_V3 |
| `gpt4_1d80365e` | temporal | P | P | P | **F** | fails ONLY under EVENTDATE_V3 |

**Critical correction to the earlier draft:** the two preference flips (`32260d93`,
`d24813b1`) were initially attributed to judge variance. The control refutes this —
they PASS in control (no flag) and FAIL only when a V3 flag is on. The preference
drop is a **real V3-flag effect**, not measurement noise.

## Mechanism — preference damage (both flags)

For `32260d93` and `d24813b1`, `num_memories_found` is **exactly 20 in all four runs**
(champ, ctrl, v3x, vt). Retrieval *volume* is identical, yet the answer degrades only
under V3 flags. Therefore the damage is **compositional**: the 20 retrieved memories are
a different *set*.

Preference routes through the `detail` path (raw+fact hybrid, `final_k = top_k = 20`,
`recall()` `fact_only = False`). When V3 memories are present:
- **V3 extraction** adds many normalized `fact` memories; Reciprocal-Rank Fusion over the
  denser pool displaces the raw session that carried the user's specific preference, so
  the top-20 loses that evidence → less-tailored / abstaining answer.
- **EVENTDATE_V3** re-ranks memories by `event_date`; the re-ordered fusion similarly
  displaces the preference-evidence session.

This is the same "denser index dilutes ranking" phenomenon already seen on the
`fact_only` path for KU/MS — **now shown to also hit the detail route**. The detail
route was previously assumed immune (it was, for volume), but composition still shifts.

## Mechanism — KU damage (extraction)

`7401057b` (KU): nmem 67 → 53 under v3x123. The Hilton 5/30 superseding update memory
dropped out of the `fact_only` top-k (fetch `top_k*3=90` facts, `final_k=30`). The
denser V3 fact pool pushed the update fact below the cutoff. Overall retrieval volume
was 0.90× champion. Classic ranking dilution under a larger fact index.

## Mechanism — temporal damage (EVENTDATE_V3) — H1 CONFIRMED

Comparing temporal flips between ctrl123 (PASS) and vt123 (FAIL):

- `bbf86515`: ctrl dates **Rack Fest = June 18** → 4 days (correct, PASS). vt resolves
  **Rack Fest = June 28** → 14 days (wrong, FAIL). The event-date resolver wrote a
  **wrong event date**, and the temporal answer prompt used it.
- `gpt4_1d80365e`: ctrl had the May 15 / May 17 Yosemite dates and answered 3 days
  (PASS). vt **abstained** ("don't have enough information") with a slightly larger
  context — the event-date resolution produced dates that didn't align, so the model
  concluded the info was absent.

**Root cause:** the ingest-time event-date resolver emits *incorrect* event dates for a
non-trivial fraction of facts/events. Those wrong dates then drive temporal answers and
chronological formatting. This is a **resolution-accuracy failure**, not a retrieval
volume problem. (Inspector's `UNKNOWN→None` fallback theory is REFUTED — `storage.py:444`
fallback fires on explicit `None` too; the bug is wrong *resolved* dates, not missing
ones.)

## H2 — max_tokens=700 extractor truncation (UNVERIFIED)

Candidate contributor to the extraction KU/fact misses: if a long session's facts exceed
the 700-token extractor budget, the superseding update fact can be truncated away →
missed in `fact_only` recall. Not directly measured here (would require re-extracting
and length-profiling). Flagged for the extraction rework: verify facts-per-session
output length stays within budget, especially for long sessions containing late updates.

## Methodology caveat

`v3x123` has **no same-branch no-flag control** (we did not re-run `worker_v3_extraction`
with `EXTRACT_V3` off). Its verdict rests on: (a) control on `4eaf3c3` (no flags)
reproduces pref 62.5 / KU 87.5 today, and (b) champ-era baseline also shows 62.5 / 87.5,
and (c) `v3x123` is the only run with `EXTRACT_V3` on. Attribution to the `EXTRACT_V3`
prompt is therefore strong but *indirect* (cross-branch). `vt123` is cleanly isolated
(same branch as control, only the flag differs), so its verdict is direct.

## Recommendations

- **Abandon both V3 branches as-is** (unmerged; champion 74.4% stack untouched).
- EVENTDATE_V3's blocker is event-date **accuracy**, not ranking — fixing it requires a
  more reliable resolver (or human-verified dates), not a retrieval tweak. Low EV until
  resolver quality is proven.
- V3 extraction's damage is ranking dilution under a denser index. A future attempt must
  (a) raise the `fact_only` fetch/return cap, (b) add recency bias so superseding updates
  survive, (c) quota-protect raw sessions, and (d) verify extractor truncation (H2). And
  it must re-validate with a **same-day control**, since pref/KU are now proven sensitive.
- PREF_V3 remains the live lever; its A/A-control discipline (run_id discipline) is now
  validated as essential. Its own implementation blocker (semantic_search tags leak) is
  fixed separately on `worker_v3_pref_impl`.
- **Never compare a V3 run to the Jul-6 77.1% alone** — `8e91e7d9` drift means the
  same-day control is mandatory.
