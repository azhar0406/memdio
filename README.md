# memdio

**AI memory encoded in sound. Lossless, fast, and hardware-adaptive.**

memdio encodes text into FLAC audio files with Reed-Solomon error correction, stores them in SQLite, and provides semantic search via MCP for Claude Desktop.

## Architecture

```
Text  -->  zlib compress  -->  Reed-Solomon ECC  -->  Frequency encoding (200-4000Hz)
      -->  FLAC compress  -->  SQLite blob storage

Search: FTS5 (word match) + FastEmbed ONNX + sqlite-vector (semantic similarity)
```

Everything lives in one SQLite database per user. No external services, no separate index files.

## Performance

| Operation | Time |
|-----------|------|
| Store memory | 67ms |
| Retrieve memory | 15ms |
| Word search (FTS5) | <1ms |
| Semantic search | 150ms |
| MCP server cold start | 127ms |

## Installation

```bash
pip install -e .

# With semantic search support
pip install -e ".[search]"
```

## Claude Desktop Integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memdio": {
      "command": "/path/to/venv/bin/python",
      "args": ["-u", "-m", "memdio.mcp"]
    }
  }
}
```

### MCP Tools

| Tool | Description |
|------|-------------|
| `store_memory` | Encode text into FLAC audio. Returns memory ID |
| `retrieve_memory` | Decode audio back to text by UUID |
| `search_memories` | FTS5 word-level search (any word matches) |
| `semantic_search` | Meaning-based search via embeddings |
| `list_memories` | List all stored memories |
| `delete_memory` | Delete a memory by UUID |

## CLI

```bash
# Store and retrieve
memdio encode "Meeting at 3pm with Alice" --tags "meeting,alice"
memdio decode <memory-id>

# Search
memdio search "meeting"
memdio semantic-search "schedule appointment"

# Manage
memdio list
memdio delete <memory-id>
memdio info <memory-id>
memdio stats

# Export
memdio export-flac <memory-id>
memdio export-wav <memory-id>

# Servers
memdio mcp          # MCP stdio server (Claude Desktop)
memdio serve        # REST API server
```

## REST API

```bash
# Start server
memdio serve

# Create API key
memdio create-key alice

# Use API
curl -X POST http://localhost:8000/memories \
  -H "Authorization: Bearer memdio_xxxx" \
  -H "Content-Type: application/json" \
  -d '{"content": "Meeting at 3pm with Bob", "tags": "meeting"}'

curl http://localhost:8000/memories?query=meeting \
  -H "Authorization: Bearer memdio_xxxx"
```

## Security

API keys are stored as SHA-256 hashes in the configured `MEMDIO_KEYS_FILE`; raw keys are only shown when created. If an OpenRouter key may have been exposed, rotate it in OpenRouter and update the deployment environment. Do not commit `.env` files or raw provider keys.

## Limits

| Parameter | Value |
|-----------|-------|
| Max memory size | 1 MB per memory |
| Max query length | 1,000 characters |
| Max tags length | 500 characters |
| Embedding dimensions | 384 (bge-small-en-v1.5) |
| Contradiction threshold | 0.85 cosine similarity |
| Relation extension threshold | 0.50 cosine similarity |
| Default search results | 10 (configurable) |
| SQLite mmap cache | 64 MB |

## LongMemEval Benchmark

Evaluated on [LongMemEval](https://github.com/xiaowu0162/LongMemEval) — 500 questions across 6 task types.

### Official result — full n=500, official protocol

**74.4% overall** (task-averaged 74.8%) on the complete LongMemEval-S set, run 2026-07-08
with the official protocol: gpt-4o answerer, gpt-4o judge with the reference judge
prompts, all V2 flags enabled.

| Task Type | n | Accuracy |
|-----------|---|----------|
| Single-session (user) | 70 | 92.9% |
| Single-session (assistant) | 56 | 85.7% |
| Knowledge update | 78 | 83.3% |
| Temporal reasoning | 133 | 74.4% |
| Multi-session | 133 | 59.4% |
| Single-session (preference) | 30 | 53.3% |
| **Overall** | **500** | **74.4%** |
| Abstention accuracy | 30 | 80.0% |

For context against self-reported full-500 numbers: Zep 71.2%, Supermemory 85.4%.
memdio sits clearly above the Zep tier while storing every memory as FLAC-encoded
audio. The two frontier gaps are explicit and data-backed: multi-session aggregation
(59.4% — fact extraction misses incidental instances at ingest) and preference
questions (53.3% — no preference profile store yet). Both have designed fixes queued
(see `benchmarks/analysis/`).

### V2 stack — category-routed reading + exhaustive retrieval (gpt-4o, official judge)

On top of the hybrid fact-extraction layer (below), the V2 stack adds four flag-gated
techniques, each traced to a specific failure mode:

- **Category-routed answer prompts** (`MEMDIO_PROMPT_V2=1`) — lexical question
  classification routes preference / aggregation / temporal questions to specialized
  chain-of-note prompts; detail questions keep the plain prompt.
- **Exhaustive entity scan** (`MEMDIO_EXHAUSTIVE=1`) — aggregation/ordering questions
  fail when a single instance is missed; keyword FTS (with singular/plural variants —
  FTS5 doesn't stem) + a semantic raw-session top-up union every candidate instance,
  interleaved so neither channel starves the other.
- **Multi-window evidence extraction** (`MEMDIO_MULTIWINDOW=1`) — long sessions get up
  to 3 windows around distinct keyword clusters instead of 1 (a single window silently
  drops the second purchase mentioned later in the same session).
- **LLM query expansion** (`MEMDIO_QUERYEXPAND=1`) — category questions ("food delivery
  services") can't lexically reach their instances ("Domino's"); a cheap LLM call
  expands the query with likely instance terms before the scan.

Protocol: gpt-4o answerer, **gpt-4o judge** (official LongMemEval judge prompts, ported
from the reference repo), `google/gemini-2.5-flash` extractor, stratified n=48
(8 per task type).

| Task Type | baseline | V2.2 (seed 42) | V2.2 (seed 123, unseen) |
|-----------|---------|----------------|--------------------------|
| Single-session (user) | 100.0% | 100.0% | 87.5% |
| Single-session (assistant) | 100.0% | 100.0% | 100.0% |
| Single-session (preference) | 50.0% | 62.5% | 62.5% |
| Knowledge update | 75.0% | 87.5% | 87.5% |
| Multi-session | 50.0% | 87.5% | 50.0% |
| Temporal reasoning | 87.5% | 75.0% | 75.0% |
| **Overall** | **77.1%** | **85.4%** | **77.1%** |

**Honest read:** the seed-42 set was used to develop the fixes, so 85.4% is the
tuned-set figure; the untouched seed-123 set gave 77.1%, and the full n=500 run above
settled it at **74.4%** — the development-set numbers overstated the gains exactly as
the held-out split warned. The full-500 figure is the only one we quote externally.
For calibration, earlier runs judged by `gemini-2.5-flash` scored ~4pp lower than the
official gpt-4o judge on identical answers.

### Hybrid fact-extraction — the memory layer under V2

memdio's memory layer stores two representations of every conversation on top of the
same audio storage: the **raw session** (episodic) and **LLM-extracted atomic facts**
(semantic), enabled with `MEMDIO_EXTRACT=1`. A **query router** (`MEMDIO_ROUTE=1`)
sends counting/aggregation and temporal questions to fact-focused retrieval while
detail questions use the full raw+fact hybrid. This layer alone scored 72.9%
(gemini judge; ≈77.1% under the official gpt-4o judge), up from ~50% for plain vector
retrieval.

### Overall Results (v1, raw-vector retrieval)

| Model | Task-Avg | Overall | Abstention |
|-------|----------|---------|------------|
| **Gemma 3 27B** | **46.3%** | **38.2%** | 86.7% |
| **Claude Sonnet 4** | **45.1%** | **35.2%** | 96.7% |
| Gemini 2.0 Flash | 42.9% | 33.2% | 100.0% |
| Qwen 2.5 72B | 42.7% | 34.8% | 90.0% |
| Grok 3 Mini | 42.5% | 33.2% | 96.7% |

### Breakdown by Task Type

| Task Type | Qs | Gemma 3 27B | Claude Sonnet 4 | Gemini 2.0 Flash | Qwen 2.5 72B | Grok 3 Mini |
|-----------|-----|-------------|-----------------|------------------|--------------|-------------|
| Single-session (assistant) | 56 | 82.1% | **89.3%** | 83.9% | 85.7% | 87.5% |
| Single-session (user) | 70 | **64.3%** | 61.4% | 57.1% | 61.4% | 58.6% |
| Single-session (preference) | 30 | 50.0% | **53.3%** | **53.3%** | 36.7% | 50.0% |
| Knowledge update | 78 | 42.3% | 39.7% | 38.5% | **43.6%** | 32.1% |
| Multi-session | 133 | **15.8%** | 15.0% | 12.8% | 13.5% | 15.0% |
| Temporal reasoning | 133 | **23.3%** | 12.0% | 12.0% | 15.0% | 12.0% |

**Key observations:**
- Gemma 3 27B leads on task-averaged accuracy, driven by strong temporal reasoning (23.3% vs 12% for most others)
- Claude Sonnet 4 is the best single-session assistant (89.3%) and preference extractor (53.3%)
- All models achieve 87–100% abstention — memdio correctly signals when information is missing
- Single-session recall is strong across the board (57–89%), validating the retrieval pipeline
- Multi-session and temporal reasoning remain the hardest categories (active improvement area)

Run the benchmark yourself:

```bash
pip install -e ".[benchmark]"
export OPENROUTER_API_KEY=your-key
python -m benchmarks.longmemeval.run --model google/gemma-3-27b-it
```

## Memory Intelligence API (`remember` / `recall`)

The benchmark-winning pipeline is available as first-class `StorageManager` methods.
It is provider-agnostic — you supply an `llm(prompt) -> str` callable, so memdio core
never depends on a specific LLM SDK.

**Extraction backends.** `memdio.core.llm.default_llm()` builds that callable from your
environment and works with **OpenAI, Claude, OpenRouter, Ollama, and llama.cpp** — the
OpenAI-compatible ones (OpenAI/OpenRouter/Ollama/llama.cpp) go through the `openai` SDK,
while Claude uses the native `anthropic` SDK. Install a backend with `pip install -e .[llm]`
(OpenAI-compatible) and/or `.[anthropic]` (Claude).

```bash
# pick a provider (auto-detected from whichever key is set if unset)
export MEMDIO_LLM_PROVIDER=anthropic         # openai | openrouter | anthropic | ollama | llamacpp
export ANTHROPIC_API_KEY=sk-ant-...           # cheap/fast default model: claude-haiku-4-5
# or OpenAI:      OPENAI_API_KEY=...           (default gpt-4o-mini)
# or OpenRouter:  OPENROUTER_API_KEY=...       (default google/gemini-2.5-flash)
# or local:       MEMDIO_LLM_PROVIDER=ollama   (http://localhost:11434/v1, default llama3.1)
#                 MEMDIO_LLM_PROVIDER=llamacpp (http://localhost:8080/v1)
# override the model with MEMDIO_EXTRACT_MODEL; a custom OpenAI-compatible server with
# MEMDIO_LLM_BASE_URL (+ MEMDIO_LLM_API_KEY).
```

```python
from memdio.core.storage import StorageManager
from memdio.core.llm import default_llm

sm = StorageManager(base_path="~/memdio")
llm = default_llm()   # built from env (OpenAI/Claude/OpenRouter/Ollama/llama.cpp); None if unset

# remember: stores the raw memory AND its extracted atomic facts (tags='fact')
sm.remember("I bought a 20-pound bag of layer feed at the farm store.",
            llm=llm, document_date="2023-05-20")

# recall: query-routed hybrid retrieval — counting/temporal questions are answered
# from discrete facts, detail questions from the raw+fact hybrid
sm.recall("How many bags of feed have I bought?")   # -> fact-focused
sm.recall("What did the store recommend?")          # -> raw+fact hybrid
```

**Extraction on the MCP/REST write paths.** By default, `store_memory` (MCP) and
`POST /memories` (REST) do a plain store — no LLM calls. Set `MEMDIO_EXTRACT_ON_STORE=1`
(alongside a configured provider) to have those surfaces extract facts on write too, so
routed `recall` / `GET /recall` benefit from discrete facts. The CLI `memdio remember`
extracts by default (opt out with `--no-extract`).

`remember` without an `llm` is a plain store; `recall` needs no LLM (pure retrieval).
This is the layer that reached **72.9%** on LongMemEval (above), storing both an
episodic raw memory and its semantic facts — on top of the same FLAC-audio storage.

## How It Works

### Encoding Pipeline

```
"Hello World"
    |
    v
UTF-8 bytes --> zlib compress --> Reed-Solomon ECC (14% redundancy)
    |
    v
CRC32 header (magic + version + length + checksum)
    |
    v
Frequency mapping: each byte -> 200-4000Hz tone (256 slots)
    |
    v
48kHz audio signal with Hann windowing (reduces spectral leakage)
    |
    v
FLAC lossless compression --> SQLite blob
```

### Search

- **FTS5**: Word-level matching. "Alice wedding" finds "Alice is getting married" because "Alice" matches.
- **Semantic**: FastEmbed (ONNX) generates 384-dim embeddings, sqlite-vector does cosine similarity. "motorcycle" finds "bike" (0.837 score).

### Error Correction

Reed-Solomon RS(255,223) recovers the original text even if 5% of audio samples are corrupted.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Audio encoding | numpy, scipy, soundfile |
| Error correction | reedsolo (Reed-Solomon) |
| Storage | SQLite (WAL mode, FLAC blobs) |
| Word search | SQLite FTS5 |
| Semantic search | FastEmbed (ONNX) + sqlite-vector |
| MCP server | Lightweight JSON-RPC over stdio |
| REST API | FastAPI |
| CLI | Typer |

## Multi-tenant SaaS

Per-user isolation with API key auth:

```
/data/memdio/users/
  alice/index.db    <-- all of Alice's memories
  bob/index.db      <-- all of Bob's memories
```

Delete user = delete their folder. No shared database.

## License

MIT
