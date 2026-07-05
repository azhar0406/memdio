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

Evaluated on [LongMemEval](https://github.com/xiaowu0162/LongMemEval) — 500 questions across 6 task types. All models via OpenRouter, context window 2,000 chars per memory, Top-K 10.

### Overall Results

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
