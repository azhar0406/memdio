# Using memdio with Claude Desktop

## Claude Desktop Setup

1. Install memdio:
   ```bash
   pip install -e .
   ```

2. Configure Claude Desktop by adding this to `~/Library/Application Support/Claude/claude_desktop_config.json`:
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

3. Restart Claude Desktop. It will launch the MCP server on demand when you invoke memdio tools.

## Available MCP Tools

Claude can use these tools once the MCP server is configured:

- `store_memory`
- `retrieve_memory`
- `search_memories`
- `semantic_search`
- `list_memories`
- `delete_memory`

## REST API Setup

memdio also exposes a local REST API:

1. Start the server:
   ```bash
   memdio serve
   ```

2. Create an API key for a user:
   ```bash
   memdio create-key alice
   ```

3. Use the returned key as a Bearer token on every request.

The REST API listens on `http://localhost:8000`.

## REST API Examples

Create a memory:

```bash
curl -X POST http://localhost:8000/memories \
  -H "Authorization: Bearer memdio_xxxx" \
  -H "Content-Type: application/json" \
  -d '{"content": "This is a test memory", "tags": "test,example"}'
```

Retrieve a memory:

```bash
curl http://localhost:8000/memories/<memory-id> \
  -H "Authorization: Bearer memdio_xxxx"
```

Search memories:

```bash
curl "http://localhost:8000/memories?query=test" \
  -H "Authorization: Bearer memdio_xxxx"
```

## Storage Model

memdio stores all user data in a per-user SQLite database rooted at `DATA_ROOT`:

```text
DATA_ROOT/
  users/
    alice/
      index.db
    bob/
      index.db
```

Each user's `index.db` contains their encoded FLAC blobs plus the search indexes and metadata. There is no shared `~/memdio/*.flac` or global `~/memdio/index.db` layout in the current implementation.
