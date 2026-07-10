# Failure Analysis: champion run 66422f77

Run: `benchmarks/results/66422f77_openai_gpt-4o.json`  
Dataset: `benchmarks/data/longmemeval_s_cleaned.json`

Method notes:
- Replayed the benchmark retrieval path using `ingest_question -> hybrid_search -> format_context`.
- The checkpoint/results files store only `num_memories_found`, not retrieved IDs or context, so retrieval was reproduced locally.
- Full semantic replay was required; with semantic retrieval enabled, at least one answer session surfaced for every failed question. Several multi-session/temporal failures still missed some required answer sessions.

## Summary Counts

| Root cause | Count |
|---|---:|
| A. RETRIEVAL-MISS | 5 |
| B. SYNTHESIS-MISS | 4 |
| C. PREFERENCE-STYLE | 1 |
| D. TEMPORAL | 3 |
| E. JUDGE-QUIRK | 0 |

## Classification Table

| Question ID | Type | Verdict | Evidence in retrieved context? | 2-line explanation |
|---|---|---|---|---|
| `1c0ddc50` | single-session-preference | C. PREFERENCE-STYLE | Yes, answer session rank 8. | The retrieved session says the user wants commute listening, wants to branch out beyond true crime/self-improvement, and is interested in history/science podcasts. The hypothesis gave broad commute activities and generic podcast/audiobook advice instead of adapting to those preferences. |
| `75832dbd` | single-session-preference | B. SYNTHESIS-MISS | Yes, answer session rank 9. | The retrieved session contains deep learning for medical image analysis, explainable AI, and requests for relevant papers/articles. The model abstained despite enough context to recommend AI-in-healthcare publications/conferences. |
| `8a2466db` | single-session-preference | B. SYNTHESIS-MISS | Yes, answer session rank 2. | The retrieved session clearly centers on Adobe Premiere Pro advanced settings, Lumetri Color, Curves, and related learning needs. The model abstained instead of recommending Premiere-specific advanced resources. |
| `fca70973` | single-session-preference | B. SYNTHESIS-MISS | Yes, answer session rank 4. | The retrieved session includes Disneyland, Knott's Berry Farm, Six Flags, Universal Studios, thrill rides, unique food, nighttime shows, and Halloween/VIP dining interests. The model abstained even though the preference profile was directly present. |
| `46a3abf7` | multi-session | A. RETRIEVAL-MISS | Partial: only one of three answer sessions surfaced in replay. | The answer requires combining the community tank, the 5-gallon betta tank, and the 20-gallon Amazonia tank; replay surfaced only the community-tank answer session. The hypothesis counted two tanks and missed one needed evidence session/fact. |
| `bc149d6b` | multi-session | B. SYNTHESIS-MISS | Yes, both answer sessions ranks 1 and 2. | The retrieved context contains a 50-pound layer feed purchase and a 20-pound organic scratch grains purchase. The model used only the 50-pound batch and failed the simple aggregation to 70 pounds. |
| `d682f1a2` | multi-session | A. RETRIEVAL-MISS | Partial: Fresh Fusion surfaced; Domino's and Uber Eats did not. | The gold count needs three services: Domino's Pizza, Uber Eats, and Fresh Fusion. Retrieved context only exposed Fresh Fusion, so the model could not count all three. |
| `gpt4_59c863d7` | multi-session | A. RETRIEVAL-MISS | Partial: B-29/Camaro surfaced; F-15, Spitfire, and some kit sessions were missing. | The gold answer requires five kits: Revell F-15, Tamiya Spitfire, Tiger I, B-29, and Camaro. The hypothesis named only three, consistent with missing required kit sessions/facts from the retrieved context. |
| `69fee5aa` | knowledge-update | D. TEMPORAL | Yes, both answer sessions ranks 1 and 3. | Earlier context had 37 pre-1920 American coins; later context adds a 1915-S Barber quarter. The model failed to apply the later update and answered the stale count instead of 38. |
| `8fb83627` | knowledge-update | D. TEMPORAL | Yes, both answer sessions ranks 4 and 6. | Earlier context says the user finished the third National Geographic issue and was on the fourth; later context says they have finished five issues. The model double-counted stale and current states, answering eight instead of the latest count of five. |
| `gpt4_385a5000` | temporal-reasoning | D. TEMPORAL | Yes, both answer sessions ranks 1 and 6. | The context says tomatoes were started indoors since February 20 and marigolds were started from seeds that arrived March 3. The hypothesis contradicted itself and did not give the correct final comparison: tomatoes first. |
| `gpt4_f420262c` | temporal-reasoning | A. RETRIEVAL-MISS | Partial: Delta and American sessions surfaced; JetBlue and United sessions did not. | The gold ordering requires JetBlue, Delta, United, then American Airlines. Retrieved context lacked the JetBlue and United flight evidence, so the model abstained. |
| `gpt4_7abb270c` | temporal-reasoning | A. RETRIEVAL-MISS | Partial: four of six answer sessions surfaced. | The gold ordering requires six museum visits; replay surfaced Science Museum, Museum of Contemporary Art, Modern Art Museum, and Natural History Museum, but missed Metropolitan Museum of Art and Museum of History. The model abstained because the retrieved context was incomplete for the full ordering. |

## Per-Question Notes

### `1c0ddc50`
Gold: commute activities should be podcast/audiobook oriented, especially history/science, and should avoid repeating true crime/self-improvement as the main direction.  
Hypothesis: broad commute productivity list. It mentions podcasts/audiobooks, but not the user's specific branch-out preference.  
Verdict: C. PREFERENCE-STYLE.

### `75832dbd`
Gold: recent research papers/articles/conferences for AI in healthcare, especially deep learning for medical image analysis.  
Hypothesis: "I don't have enough information." The answer session was retrieved at rank 9 and contained the relevant field and publication/article interest.  
Verdict: B. SYNTHESIS-MISS.

### `8a2466db`
Gold: Adobe Premiere Pro advanced-settings learning resources.  
Hypothesis: abstention. The answer session was retrieved at rank 2 and directly mentioned Premiere Pro, Lumetri, Curves, and advanced controls.  
Verdict: B. SYNTHESIS-MISS.

### `fca70973`
Gold: theme-park weekend suggestions tailored to thrill rides, special events, unique food, and nighttime shows, using prior LA-area park visits.  
Hypothesis: abstention. The retrieved answer session had the full preference frame.  
Verdict: B. SYNTHESIS-MISS.

### `46a3abf7`
Gold: 3 tanks.  
Hypothesis: 2 tanks. Retrieval replay surfaced only one of the three answer sessions by exact formatted-session matching, missing needed tank evidence.  
Verdict: A. RETRIEVAL-MISS.

### `bc149d6b`
Gold: 70 pounds.  
Hypothesis: 50 pounds. Both purchases were retrieved: 50 pounds layer feed and 20 pounds scratch grains.  
Verdict: B. SYNTHESIS-MISS.

### `d682f1a2`
Gold: 3 food delivery services.  
Hypothesis: only Fresh Fusion. Retrieval replay surfaced Fresh Fusion but not Domino's Pizza or Uber Eats answer sessions.  
Verdict: A. RETRIEVAL-MISS.

### `gpt4_59c863d7`
Gold: 5 model kits.  
Hypothesis: 3 model kits. Replay surfaced only a subset of the required kit evidence; missing sessions explain the omitted Revell F-15 and Tamiya Spitfire.  
Verdict: A. RETRIEVAL-MISS.

### `69fee5aa`
Gold: 38 pre-1920 American coins.  
Hypothesis: 37. The model saw the prior count and the later added 1915-S Barber quarter but did not update the count.  
Verdict: D. TEMPORAL.

### `8fb83627`
Gold: five National Geographic issues finished.  
Hypothesis: eight. The model treated an older progress state and the newer final state as additive instead of superseding.  
Verdict: D. TEMPORAL.

### `gpt4_385a5000`
Gold: tomatoes.  
Hypothesis: says marigolds first, then states tomatoes were started earlier on February 20. The relevant dates were retrieved, but comparison/answer selection failed.  
Verdict: D. TEMPORAL.

### `gpt4_f420262c`
Gold: JetBlue, Delta, United, American Airlines.  
Hypothesis: abstention. Retrieval replay missed the JetBlue and United sessions, so the full ordering was unavailable.  
Verdict: A. RETRIEVAL-MISS.

### `gpt4_7abb270c`
Gold: Science Museum, Museum of Contemporary Art, Metropolitan Museum of Art, Museum of History, Modern Art Museum, Natural History Museum.  
Hypothesis: abstention. Retrieval replay surfaced only four of six required museum-visit sessions.  
Verdict: A. RETRIEVAL-MISS.
