"""Multi-tenant REST API for memdio SaaS.

Each user gets isolated storage in their own folder.
Authentication via API key in Authorization header.
"""

import logging
import os
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from memdio.api.auth import verify_api_key
from memdio.core.storage import StorageManager

logger = logging.getLogger(__name__)

DATA_ROOT = os.environ.get("MEMDIO_DATA_ROOT", "/data/memdio")

app = FastAPI(
    title="memdio API",
    description="AI memory encoded in sound — multi-tenant SaaS",
    version="0.2.0",
)


# --- Storage per user (cached) ---

@lru_cache(maxsize=128)
def _get_user_storage(user_id: str) -> StorageManager:
    user_path = os.path.join(DATA_ROOT, "users", user_id)
    return StorageManager(base_path=user_path)


# --- Auth dependency ---

async def get_current_user(authorization: str = Header(...)) -> str:
    """Extract and verify API key from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    api_key = authorization[7:]
    user_id = verify_api_key(api_key)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user_id


async def get_storage(user_id: str = Depends(get_current_user)) -> StorageManager:
    """Get the user's isolated storage instance."""
    return _get_user_storage(user_id)


# --- Models ---

class StoreRequest(BaseModel):
    content: str
    tags: str | None = None
    document_date: str | None = None


class MemoryResponse(BaseModel):
    id: str
    content: str


class StatsResponse(BaseModel):
    total_memories: int
    total_content_bytes: int
    total_flac_bytes: int


# --- Endpoints ---

@app.get("/")
async def root():
    return {"service": "memdio", "version": "0.2.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/memories", response_model=MemoryResponse)
async def store_memory(
    req: StoreRequest,
    storage: StorageManager = Depends(get_storage),
):
    """Store a new memory."""
    try:
        mem_id = storage.store(req.content, req.tags, document_date=req.document_date)
        return MemoryResponse(id=mem_id, content=req.content)
    except Exception:
        logger.exception("Error storing memory")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memories")
async def list_memories(
    query: str = "",
    storage: StorageManager = Depends(get_storage),
):
    """List or search memories (FTS5 word search)."""
    try:
        if query:
            results = storage.search(query)
        else:
            results = storage.list_all()
        return {"results": results}
    except Exception:
        logger.exception("Error listing memories")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memories/semantic")
async def semantic_search(
    query: str = Query(..., description="Search query"),
    top_k: int = Query(5, description="Number of results"),
    storage: StorageManager = Depends(get_storage),
):
    """Semantic search using embeddings (cosine similarity)."""
    try:
        results = storage.semantic_search(query, top_k=top_k)
        return {"results": results}
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception:
        logger.exception("Error in semantic search")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memories/temporal")
async def temporal_search_endpoint(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    top_k: int = Query(10, description="Number of results"),
    storage: StorageManager = Depends(get_storage),
):
    """Search memories by event date range."""
    try:
        results = storage.temporal_search(start_date, end_date, top_k=top_k)
        return {"results": results}
    except Exception:
        logger.exception("Error in temporal search")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/memories/{mem_id}")
async def retrieve_memory(
    mem_id: str,
    storage: StorageManager = Depends(get_storage),
):
    """Retrieve a memory by ID (decodes from audio)."""
    try:
        content = storage.retrieve(mem_id)
        return MemoryResponse(id=mem_id, content=content)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    except Exception:
        logger.exception("Error retrieving memory")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/memories/{mem_id}")
async def delete_memory(
    mem_id: str,
    storage: StorageManager = Depends(get_storage),
):
    """Delete a memory."""
    try:
        deleted = storage.delete(mem_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {"status": "deleted", "id": mem_id}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error deleting memory")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/stats", response_model=StatsResponse)
async def get_stats(
    storage: StorageManager = Depends(get_storage),
):
    """Get storage statistics for the current user."""
    s = storage.stats()
    return StatsResponse(
        total_memories=s["total_memories"],
        total_content_bytes=s["total_content_bytes"],
        total_flac_bytes=s["total_flac_bytes"],
    )


@app.post("/memories/{mem_id}/export")
async def export_memory(
    mem_id: str,
    format: str = "flac",
    storage: StorageManager = Depends(get_storage),
):
    """Export a memory as a downloadable audio file."""
    from fastapi.responses import FileResponse

    try:
        if format == "flac":
            path = storage.export_flac(mem_id)
        elif format == "wav":
            path = storage.export_wav(mem_id)
        else:
            raise HTTPException(status_code=400, detail="Format must be 'flac' or 'wav'")
        return FileResponse(path, media_type=f"audio/{format}", filename=f"{mem_id}.{format}")
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
