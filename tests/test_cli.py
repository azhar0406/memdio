import pytest
from unittest.mock import patch
from typer.testing import CliRunner
from memdio.cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def mock_storage(tmp_path):
    """Use tmp_path for all storage to avoid side effects."""
    with patch("memdio.cli.main._get_storage") as mock:
        from memdio.core.storage import StorageManager
        storage = StorageManager(base_path=str(tmp_path / "memdio"))
        mock.return_value = storage
        yield storage


def _encode(text, tags=None):
    args = ["encode", text]
    if tags:
        args += ["--tags", tags]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert "Memory stored with ID:" in result.stdout
    return result.stdout.split("Memory stored with ID:")[1].strip().splitlines()[0]


def test_encode_command():
    result = runner.invoke(app, ["encode", "test text"])
    assert result.exit_code == 0
    assert "Memory stored with ID:" in result.stdout


def test_encode_decode_roundtrip():
    mem_id = _encode("meeting at 3pm with Alice")
    result = runner.invoke(app, ["decode", mem_id])
    assert result.exit_code == 0
    assert "Content: meeting at 3pm with Alice" in result.stdout


def test_search_command_finds_content():
    _encode("unique searchable phrase about penguins")
    result = runner.invoke(app, ["search", "penguins"])
    assert result.exit_code == 0
    assert "Found 1 memory(s):" in result.stdout
    assert "penguins" in result.stdout


def test_list_command_shows_stored_memory():
    _encode("first note")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Found 1 memory(s):" in result.stdout
    assert "first note" in result.stdout


def test_info_command():
    mem_id = _encode("inspect me")
    result = runner.invoke(app, ["info", mem_id])
    assert result.exit_code == 0
    assert "encoder_version" in result.stdout


def test_stats_command():
    _encode("something")
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "total_memories" in result.stdout


def test_delete_command():
    mem_id = _encode("delete me")
    result = runner.invoke(app, ["delete", mem_id])
    assert result.exit_code == 0
    assert f"Memory {mem_id} deleted" in result.stdout
    # Now gone
    gone = runner.invoke(app, ["info", mem_id])
    assert gone.exit_code == 1
    assert "not found" in gone.stdout


def test_delete_not_found():
    result = runner.invoke(app, ["delete", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_decode_not_found():
    result = runner.invoke(app, ["decode", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 1


def test_create_key_command(monkeypatch, tmp_path):
    from memdio.api import auth
    monkeypatch.setattr(auth, "KEYS_FILE", str(tmp_path / "api_keys.json"))
    result = runner.invoke(app, ["create-key", "alice"])
    assert result.exit_code == 0
    assert "API key for alice: memdio_" in result.stdout
    assert "won't be shown again" in result.stdout
