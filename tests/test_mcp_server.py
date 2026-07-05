import pytest

from memdio.mcp.server import call_tool, list_tools


@pytest.fixture(autouse=True)
def mock_storage(tmp_path, monkeypatch):
    import memdio.mcp.server as srv
    from memdio.core.storage import StorageManager
    storage = StorageManager(base_path=str(tmp_path / "memdio"))

    monkeypatch.setattr(srv, "_storage_instance", storage)
    yield storage
    storage.close()
    monkeypatch.setattr(srv, "_storage_instance", None)


@pytest.mark.asyncio
async def test_list_tools():
    tools = await list_tools()
    names = [t.name for t in tools]
    assert "store_memory" in names
    assert "retrieve_memory" in names
    assert "search_memories" in names
    assert "list_memories" in names
    assert "delete_memory" in names


@pytest.mark.asyncio
async def test_store_and_retrieve():
    result = await call_tool("store_memory", {"content": "MCP test"})
    assert "Memory stored with ID:" in result[0].text

    mem_id = result[0].text.split(": ")[1].strip()
    result = await call_tool("retrieve_memory", {"memory_id": mem_id})
    assert result[0].text == "MCP test"


@pytest.mark.asyncio
async def test_search():
    await call_tool("store_memory", {"content": "unique searchable phrase"})
    result = await call_tool("search_memories", {"query": "searchable"})
    assert "unique searchable phrase" in result[0].text


@pytest.mark.asyncio
async def test_recall():
    await call_tool("store_memory", {"content": "the quarterly budget review is on Friday"})
    result = await call_tool("recall", {"query": "when is the budget review"})
    assert "budget review" in result[0].text


@pytest.mark.asyncio
async def test_list():
    await call_tool("store_memory", {"content": "list test"})
    result = await call_tool("list_memories", {})
    assert "list test" in result[0].text


@pytest.mark.asyncio
async def test_delete():
    result = await call_tool("store_memory", {"content": "to delete"})
    mem_id = result[0].text.split(": ")[1].strip()
    result = await call_tool("delete_memory", {"memory_id": mem_id})
    assert "deleted" in result[0].text


@pytest.mark.asyncio
async def test_invalid_id():
    result = await call_tool("retrieve_memory", {"memory_id": "bad"})
    assert "Validation error" in result[0].text


@pytest.mark.asyncio
async def test_not_found():
    result = await call_tool(
        "retrieve_memory",
        {"memory_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert "Not found" in result[0].text
