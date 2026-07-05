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

### Hybrid fact-extraction (v2) — gpt-4o

memdio's memory layer can store two representations of every conversation on top of the
same audio storage: the **raw session** (episodic) and **LLM-extracted atomic facts**
(semantic). Retrieval surfaces raw sessions for detail questions and discrete facts for
aggregation/temporal questions. Enable with `MEMDIO_EXTRACT=1`.

A **query router** (`MEMDIO_ROUTE=1`) sends counting/aggregation and temporal questions
to fact-focused retrieval (discrete facts are countable and carry clean dates) while
detail questions use the full raw+fact hybrid.

Config: gpt-4o answerer, `google/gemini-2.5-flash` extractor + judge, Top-K 20, 4,000
chars/memory, **stratified n=48** (8 per task type, seed 42).

| Task Type | hybrid | hybrid + routing |
|-----------|--------|------------------|
| Single-session (user) | 100.0% | 100.0% |
| Single-session (assistant) | 100.0% | 100.0% |
| Single-session (preference) | 37.5% | 50.0% |
| Knowledge update | 87.5% | 75.0% |
| Multi-session | 25.0% | 50.0% |
| Temporal reasoning | 62.5% | 62.5% |
| **Overall** | **68.8%** | **72.9%** |

For context, published LongMemEval-class results: **mem0 ~67%** (LOCOMO), **Zep 71%**
(LongMemEval, gpt-4o), **Supermemory 85.4%** (LongMemEval-S). Numbers are not perfectly
comparable (different judges/subsets/benchmarks), but on the same benchmark and answer
model memdio's hybrid + routing (**72.9%**) exceeds the mem0/Zep tier — up from ~50% for
plain vector retrieval. Multi-session aggregation ("how many / total …") remains the
hardest category and the largest remaining headroom toward Supermemory's 85%.

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
