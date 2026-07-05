"""Lightweight MCP stdio server — minimal imports for fast cold start.

Handles JSON-RPC directly over stdin/stdout without the heavy mcp SDK
(which pulls in pydantic, jsonschema, etc. adding ~1.3s startup).

IMPORTANT: Uses sys.stdin.buffer.readline() instead of iterating sys.stdin
to avoid Python's read-ahead buffer which causes messages to get stuck.
"""
import json
import logging
import os
import sys
import time

logging.basicConfig(
    level=getattr(logging, os.environ.get("MEMDIO_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("memdio.mcp")

TOOLS = [
    {
        "name": "store_memory",
        "description": "Encode text into a FLAC audio memory file. Returns the memory ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The text content to store as audio memory"},
                "tags": {"type": "string", "description": "Optional comma-separated tags"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "retrieve_memory",
        "description": "Decode and retrieve a memory by its UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "UUID of the memory to retrieve"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "search_memories",
        "description": "Search memories by content substring match.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_memories",
        "description": "List all stored memories with their IDs and content previews.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_memory",
        "description": "Delete a memory by its UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "UUID of the memory to delete"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "semantic_search",
        "description": "Search memories by semantic similarity using embeddings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_by_tag",
        "description": "Search memories by tag. Finds memories with a specific tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to search for (e.g. 'meeting', 'wedding')"},
            },
            "required": ["tag"],
        },
    },
    {
        "name": "recall",
        "description": "Query-routed hybrid retrieval — the best-performing recall pipeline. No LLM needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query to recall memories for"},
                "top_k": {"type": "integer", "description": "Number of results (default 10)"},
            },
            "required": ["query"],
        },
    },
]

_storage = None


def _redact_arguments(arguments):
    if not isinstance(arguments, dict):
        return arguments
    redacted = dict(arguments)
    if "content" in redacted:
        redacted["content"] = "<redacted>"
    return redacted


def _get_storage():
    global _storage
    if _storage is None:
        t0 = time.perf_counter()
        from memdio.core.storage import StorageManager
        _storage = StorageManager()
        # Pre-import heavy libs so first store_memory isn't slow
        import numpy, scipy, soundfile  # noqa: F401
        from memdio.core.multichannel import MultiChannelEncoder  # noqa: F401
        logger.info("Storage init: %.3fs", time.perf_counter() - t0)
    return _storage


def handle_tool_call(name, arguments):
    """Execute a tool and return (text, is_error)."""
    try:
        storage = _get_storage()

        if name == "store_memory":
            mem_id = storage.store(arguments["content"], arguments.get("tags"))
            return f"Memory stored with ID: {mem_id}", False

        elif name == "retrieve_memory":
            return storage.retrieve(arguments["memory_id"]), False

        elif name == "search_memories":
            results = storage.search(arguments["query"])
            if not results:
                return "No memories found.", False
            lines = [f"Found {len(results)} memory(s):"]
            for r in results:
                lines.append(f"  {r['id']}: {r['content'][:80]}...")
            return "\n".join(lines), False

        elif name == "list_memories":
            results = storage.list_all()
            if not results:
                return "No memories stored.", False
            lines = [f"Total: {len(results)} memory(s)"]
            for r in results:
                lines.append(f"  {r['id']}: {r['content'][:80]}...")
            return "\n".join(lines), False

        elif name == "delete_memory":
            deleted = storage.delete(arguments["memory_id"])
            if deleted is False:
                return f"Memory {arguments['memory_id']} not found.", False
            return f"Memory {arguments['memory_id']} deleted.", False

        elif name == "semantic_search":
            top_k = arguments.get("top_k", 5)
            results = storage.semantic_search(arguments["query"], top_k=top_k)
            if not results:
                return "No memories found.", False
            lines = [f"Found {len(results)} result(s):"]
            for r in results:
                lines.append(f"  [{r['score']:.3f}] {r['id']}: {r['content'][:80]}...")
            return "\n".join(lines), False

        elif name == "search_by_tag":
            results = storage.search_by_tag(arguments["tag"])
            if not results:
                return f"No memories with tag '{arguments['tag']}'.", False
            lines = [f"Found {len(results)} memory(s) tagged '{arguments['tag']}':"]
            for r in results:
                lines.append(f"  {r['id']}: {r['content'][:80]}... [tags: {r.get('tags', '')}]")
            return "\n".join(lines), False

        elif name == "recall":
            top_k = arguments.get("top_k", 10)
            results = storage.recall(arguments["query"], top_k=top_k)
            if not results:
                return "No memories found.", False
            lines = [f"Found {len(results)} result(s):"]
            for r in results:
                lines.append(f"  {r['id']}: {r['content'][:80]}...")
            return "\n".join(lines), False

        else:
            return f"Unknown tool: {name}", True

    except Exception as e:
        logger.exception("Error in tool %s", name)
        return str(e), True


def send(msg):
    """Write a JSON-RPC message directly to fd 1 — zero buffering."""
    data = json.dumps(msg) + "\n"
    os.write(1, data.encode("utf-8"))


def handle_message(msg):
    """Process one JSON-RPC message and return a response."""
    method = msg.get("method")
    msg_id = msg.get("id")

    # Notifications (no id) — no response needed
    if msg_id is None:
        logger.debug("Notification: %s", method)
        return

    if method == "initialize":
        client_version = msg.get("params", {}).get("protocolVersion", "2024-11-05")
        logger.info("Client requested protocol version: %s", client_version)
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "memdio", "version": "0.2.0"},
            },
        })
        logger.info("Initialized with protocol version %s", client_version)

    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        })
        logger.debug("Listed %d tools", len(TOOLS))

    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        logger.info(">>> TOOL CALL: %s | args: %s", name, _redact_arguments(arguments))
        t0 = time.perf_counter()

        text, is_error = handle_tool_call(name, arguments)

        elapsed = time.perf_counter() - t0
        logger.info("<<< TOOL DONE: %s | %.3fs | response_chars: %d", name, elapsed, len(text))

        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            },
        })

    elif method == "ping":
        send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

    else:
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })


def _preload_libs():
    """Pre-import heavy libraries in background thread so first tool call is fast."""
    import threading

    def _load():
        t0 = time.perf_counter()
        import numpy, scipy, soundfile  # noqa: F401
        from memdio.core.multichannel import MultiChannelEncoder  # noqa: F401
        from memdio.core.ecc import ReedSolomonECC  # noqa: F401
        # Pre-load FastEmbed ONNX model so first semantic_search is fast
        try:
            from fastembed import TextEmbedding
            fe = TextEmbedding("BAAI/bge-small-en-v1.5")
            list(fe.embed(["warmup"]))  # trigger ONNX session init
        except Exception:
            pass  # optional dependency
        logger.info("Pre-loaded libs in %.3fs", time.perf_counter() - t0)

    threading.Thread(target=_load, daemon=True).start()


def main():
    logger.info("=== memdio MCP server starting (lightweight) ===")
    _preload_libs()

    # Read from raw binary buffer — avoids Python's TextIOWrapper read-ahead
    # buffer which can delay message delivery by holding data in an internal
    # buffer waiting for more bytes before yielding a line.
    stdin = sys.stdin.buffer

    while True:
        raw_line = stdin.readline()
        if not raw_line:
            logger.info("stdin closed, shutting down")
            break

        line = raw_line.decode("utf-8").strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
            handle_message(msg)
        except json.JSONDecodeError:
            logger.error("Invalid JSON: %s", line[:200])
        except Exception:
            logger.exception("Unhandled error processing message")


main()
