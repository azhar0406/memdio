import typer
from typing import Optional

app = typer.Typer()


def _get_storage():
    from memdio.core.storage import StorageManager
    return StorageManager()


@app.command()
def encode(
    text: str = typer.Argument(..., help="Text to encode to audio memory"),
    tags: Optional[str] = typer.Option(None, "-t", "--tags", help="Tags for the memory"),
):
    """Encode text to audio memory"""
    try:
        mem_id = _get_storage().store(text, tags)
        typer.echo(f"Memory stored with ID: {mem_id}")
    except Exception as e:
        typer.echo(f"Error encoding memory: {e}")
        raise typer.Exit(code=1)


@app.command()
def decode(
    mem_id: str = typer.Argument(..., help="Memory ID to decode"),
):
    """Decode audio memory to text"""
    try:
        content = _get_storage().retrieve(mem_id)
        typer.echo(f"Content: {content}")
    except KeyError:
        typer.echo(f"Memory {mem_id} not found")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error decoding memory: {e}")
        raise typer.Exit(code=1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
):
    """Search memories"""
    try:
        results = _get_storage().search(query)
        if not results:
            typer.echo("No memories found")
            return

        typer.echo(f"Found {len(results)} memory(s):")
        for result in results:
            typer.echo(f"  {result['id']}: {result['content'][:50]}...")
    except Exception as e:
        typer.echo(f"Error searching memories: {e}")
        raise typer.Exit(code=1)


@app.command(name="tag-search")
def tag_search(
    tag: str = typer.Argument(..., help="Tag to search for"),
):
    """Search memories by tag"""
    try:
        results = _get_storage().search_by_tag(tag)
        if not results:
            typer.echo(f"No memories with tag '{tag}'")
            return
        typer.echo(f"Found {len(results)} memory(s) with tag '{tag}':")
        for r in results:
            typer.echo(f"  {r['id']}: {r['content'][:50]}... [tags: {r.get('tags', '')}]")
    except Exception as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)


@app.command(name="list")
def list_memories():
    """List all memories"""
    try:
        results = _get_storage().list_all()
        if not results:
            typer.echo("No memories found")
            return

        typer.echo(f"Found {len(results)} memory(s):")
        for result in results:
            typer.echo(f"  {result['id']}: {result['content'][:50]}...")
    except Exception as e:
        typer.echo(f"Error listing memories: {e}")
        raise typer.Exit(code=1)


@app.command()
def delete(
    mem_id: str = typer.Argument(..., help="Memory ID to delete"),
):
    """Delete a memory"""
    try:
        deleted = _get_storage().delete(mem_id)
        if not deleted:
            raise KeyError(mem_id)
        typer.echo(f"Memory {mem_id} deleted")
    except KeyError:
        typer.echo(f"Memory {mem_id} not found")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error deleting memory: {e}")
        raise typer.Exit(code=1)


@app.command()
def info(
    mem_id: str = typer.Argument(..., help="Memory ID to inspect"),
):
    """Show metadata for a memory"""
    try:
        data = _get_storage().get_info(mem_id)
        for key, value in data.items():
            typer.echo(f"  {key}: {value}")
    except KeyError:
        typer.echo(f"Memory {mem_id} not found")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)


@app.command()
def stats():
    """Show storage statistics"""
    try:
        data = _get_storage().stats()
        for key, value in data.items():
            typer.echo(f"  {key}: {value}")
    except Exception as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)


@app.command(name="export-flac")
def export_flac(
    mem_id: str = typer.Argument(..., help="Memory ID to export"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Export a memory as a FLAC file (lossless compressed)"""
    try:
        path = _get_storage().export_flac(mem_id, output)
        typer.echo(f"Exported to: {path}")
    except KeyError:
        typer.echo(f"Memory {mem_id} not found")
        raise typer.Exit(code=1)


@app.command(name="export-wav")
def export_wav(
    mem_id: str = typer.Argument(..., help="Memory ID to export"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
):
    """Export a memory as a WAV file (lossless uncompressed)"""
    try:
        path = _get_storage().export_wav(mem_id, output)
        typer.echo(f"Exported to: {path}")
    except KeyError:
        typer.echo(f"Memory {mem_id} not found")
        raise typer.Exit(code=1)


@app.command(name="semantic-search")
def semantic_search(
    query: str = typer.Argument(..., help="Semantic search query"),
    top_k: int = typer.Option(5, "-k", "--top-k", help="Number of results"),
):
    """Search memories by semantic similarity"""
    try:
        results = _get_storage().semantic_search(query, top_k=top_k)
        if not results:
            typer.echo("No memories found")
            return
        typer.echo(f"Found {len(results)} result(s):")
        for r in results:
            typer.echo(f"  [{r['score']:.3f}] {r['id']}: {r['content'][:50]}...")
    except RuntimeError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(code=1)


@app.command()
def mcp():
    """Start MCP server for Claude Desktop (stdio transport)"""
    import asyncio
    import sys
    from memdio.mcp.server import run_mcp_server
    # No stdout output — MCP uses stdio for JSON protocol
    print("Starting MCP server (stdio transport)...", file=sys.stderr)
    asyncio.run(run_mcp_server())


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(8000, help="Port to bind"),
):
    """Start REST API server"""
    import uvicorn
    from memdio.api.server import app as api_app
    typer.echo(f"Starting memdio API on {host}:{port}")
    uvicorn.run(api_app, host=host, port=port, log_level="info")


@app.command(name="create-key")
def create_key(
    user_id: str = typer.Argument(..., help="User ID to create key for"),
):
    """Create an API key for a user"""
    from memdio.api.auth import create_api_key
    key = create_api_key(user_id)
    typer.echo(f"API key for {user_id}: {key}")
    typer.echo("Save this key — it won't be shown again.")


if __name__ == "__main__":
    app()
