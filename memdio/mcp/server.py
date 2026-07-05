"""MCP server for memdio — exposes memory tools via stdio transport."""

import logging
import os
import sys
import time

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from memdio.core.validators import ValidationError

# Log to stderr so it doesn't corrupt stdio JSON transport
logging.basicConfig(
    level=getattr(logging, os.environ.get("MEMDIO_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("memdio.mcp")

server = Server("memdio")

_storage_instance = None


def _redact_arguments(arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        return arguments
    redacted = dict(arguments)
    if "content" in redacted:
        redacted["content"] = "<redacted>"
    return redacted


def _get_storage():
    global _storage_instance
    if _storage_instance is None:
        t0 = time.perf_counter()
        from memdio.core.storage import StorageManager
        _storage_instance = StorageManager()
        logger.info("StorageManager initialized in %.3fs", time.perf_counter() - t0)
    return _storage_instance


@server.list_tools()
async def list_tools() -> list[Tool]:
    logger.debug("list_tools called")
    return [
        Tool(
            name="store_memory",
            description="Encode text into a FLAC audio memory file. Returns the memory ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The text content to store as audio memory",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Optional comma-separated tags",
                    },
                    "document_date": {
                        "type": "string",
                        "description": "When the conversation occurred (e.g. 2025-03-22)",
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="retrieve_memory",
            description="Decode and retrieve a memory by its UUID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to retrieve",
                    },
                },
                "required": ["memory_id"],
            },
        ),
        Tool(
            name="search_memories",
            description="Search memories by content substring match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_memories",
            description="List all stored memories with their IDs and content previews.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="delete_memory",
            description="Delete a memory by its UUID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "UUID of the memory to delete",
                    },
                },
                "required": ["memory_id"],
            },
        ),
        Tool(
            name="semantic_search",
            description="Search memories by semantic similarity using embeddings.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="recall",
            description="Query-routed hybrid retrieval — the best-performing recall pipeline. No LLM needed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query to recall memories for",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 10)",
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    logger.info(">>> TOOL CALL: %s | args: %s", name, _redact_arguments(arguments))
    t0 = time.perf_counter()

    try:
        storage = _get_storage()

        if name == "store_memory":
            mem_id = storage.store(
                arguments["content"], arguments.get("tags"),
                document_date=arguments.get("document_date"),
            )
            result = [TextContent(type="text", text=f"Memory stored with ID: {mem_id}")]

        elif name == "retrieve_memory":
            content = storage.retrieve(arguments["memory_id"])
            result = [TextContent(type="text", text=content)]

        elif name == "search_memories":
            results = storage.search(arguments["query"])
            if not results:
                result = [TextContent(type="text", text="No memories found.")]
            else:
                lines = [f"Found {len(results)} memory(s):"]
                for r in results:
                    lines.append(f"  {r['id']}: {r['content'][:80]}...")
                result = [TextContent(type="text", text="\n".join(lines))]

        elif name == "list_memories":
            results = storage.list_all()
            if not results:
                result = [TextContent(type="text", text="No memories stored.")]
            else:
                lines = [f"Total: {len(results)} memory(s)"]
                for r in results:
                    lines.append(f"  {r['id']}: {r['content'][:80]}...")
                result = [TextContent(type="text", text="\n".join(lines))]

        elif name == "delete_memory":
            deleted = storage.delete(arguments["memory_id"])
            if deleted is False:
                result = [TextContent(type="text", text=f"Memory {arguments['memory_id']} not found.")]
            else:
                result = [TextContent(type="text", text=f"Memory {arguments['memory_id']} deleted.")]

        elif name == "semantic_search":
            top_k = arguments.get("top_k", 5)
            results = storage.semantic_search(arguments["query"], top_k=top_k)
            if not results:
                result = [TextContent(type="text", text="No memories found.")]
            else:
                lines = [f"Found {len(results)} result(s):"]
                for r in results:
                    lines.append(f"  [{r['score']:.3f}] {r['id']}: {r['content'][:80]}...")
                result = [TextContent(type="text", text="\n".join(lines))]

        elif name == "recall":
            top_k = arguments.get("top_k", 10)
            results = storage.recall(arguments["query"], top_k=top_k)
            if not results:
                result = [TextContent(type="text", text="No memories found.")]
            else:
                lines = [f"Found {len(results)} result(s):"]
                for r in results:
                    lines.append(f"  {r['id']}: {r['content'][:80]}...")
                result = [TextContent(type="text", text="\n".join(lines))]

        else:
            result = [TextContent(type="text", text=f"Unknown tool: {name}")]

    except ValidationError as e:
        result = [TextContent(type="text", text=f"Validation error: {e}")]
    except KeyError as e:
        result = [TextContent(type="text", text=f"Not found: {e}")]
    except Exception:
        logger.exception("Error in tool %s", name)
        result = [TextContent(type="text", text="An internal error occurred. Check server logs.")]

    elapsed = time.perf_counter() - t0
    logger.info("<<< TOOL DONE: %s | %.3fs | response_chars: %d", name, elapsed, len(result[0].text))
    return result


async def run_mcp_server():
    """Run the MCP server on stdio."""
    logger.info("=== memdio MCP server starting ===")
    async with stdio_server() as (read_stream, write_stream):
        logger.info("=== stdio transport ready ===")
        await server.run(read_stream, write_stream, server.create_initialization_options())
