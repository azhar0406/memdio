> Historical planning doc — see README.md for current state.

# memdio - Phase 1 MVP Implementation Plan

## Goal
Create a minimal working version of memdio that works like basic-memory:
- Simple audio encoding (text → frequency mapping → FLAC)
- CLI interface with encode/decode commands
- File storage with metadata
- SQLite indexing for search
- MCP integration for Claude Desktop
- Installable with `uv tool install memdio`

## Core Components

### 1. Audio Encoding (Simple)
- Text → UTF-8 bytes → Simple frequency mapping (200-2000 Hz)
- Generate sine wave segments for each character
- Save as FLAC with soundfile

### 2. Storage System
- Store FLAC files in `~/memdio/memory/` 
- Metadata in SQLite: `~/memdio/index.db`
- Simple ID generation and lookup

### 3. CLI Interface
- `memdio encode "text"` → generates UUID.flac
- `memdio decode uuid` → retrieves and decodes
- `memdio search "query"` → searches metadata
- `memdio mcp` → starts MCP server

### 4. MCP Integration
- HTTP endpoints for Claude Desktop
- Simple REST API for encode/decode/search

## Implementation Steps

### Week 1: Core Audio System
- [ ] Simple encoder/decoder classes
- [ ] Text ↔ frequency mapping
- [ ] FLAC I/O with soundfile
- [ ] Basic CLI structure

### Week 2: Storage & Indexing
- [ ] File storage system
- [ ] SQLite metadata database
- [ ] CLI commands for encode/decode
- [ ] Basic search functionality

### Week 3: MCP Integration
- [ ] FastAPI MCP server
- [ ] REST endpoints for Claude
- [ ] Integration testing
- [ ] Documentation and examples

### Week 4: Polish & Release
- [ ] Error handling and validation
- [ ] Testing and bug fixes
- [ ] Installation and setup instructions
- [ ] Release v0.1.0

## Tech Stack (Minimal)
- Python 3.11+
- soundfile (FLAC I/O)
- numpy (signal processing)
- sqlite3 (indexing)
- fastapi (MCP server)
- typer (CLI)
- uvicorn (server)

## Future Roadmap (Post-MVP)
- Advanced error correction
- Hardware adaptation
- Semantic search
- Cloud storage
- Kubernetes deployment
- Enterprise features
