import hashlib
import json
import stat

import pytest

from memdio.api import auth


@pytest.fixture(autouse=True)
def isolated_keys_file(monkeypatch, tmp_path):
    """Point the keys store at a temp file for every test."""
    keys_file = tmp_path / "api_keys.json"
    monkeypatch.setattr(auth, "KEYS_FILE", str(keys_file))
    return keys_file


def test_api_keys_file_created_with_owner_only_permissions(isolated_keys_file):
    api_key = auth.create_api_key("alice")

    assert stat.S_IMODE(isolated_keys_file.stat().st_mode) == 0o600
    raw = isolated_keys_file.read_text()
    assert api_key not in raw  # raw key never persisted
    assert list(json.loads(raw).values()) == [{"user_id": "alice"}]


def test_key_format_and_entropy():
    key = auth.create_api_key("bob")
    assert key.startswith("memdio_")
    # token_urlsafe(32) -> ~43 url-safe chars after the prefix
    assert len(key) > 40


def test_only_sha256_hash_is_stored(isolated_keys_file):
    key = auth.create_api_key("carol")
    stored = json.loads(isolated_keys_file.read_text())
    assert hashlib.sha256(key.encode()).hexdigest() in stored


def test_verify_valid_and_invalid_keys():
    key = auth.create_api_key("dave")
    assert auth.verify_api_key(key) == "dave"
    assert auth.verify_api_key("memdio_not_a_real_key") is None
    assert auth.verify_api_key("") is None


def test_revoke_key():
    key = auth.create_api_key("erin")
    assert auth.verify_api_key(key) == "erin"

    assert auth.revoke_api_key(key) is True
    assert auth.verify_api_key(key) is None
    # Revoking an already-revoked/unknown key is False, not an error
    assert auth.revoke_api_key(key) is False


def test_multiple_users_isolated_in_store(isolated_keys_file):
    k1 = auth.create_api_key("user-a")
    k2 = auth.create_api_key("user-b")
    assert auth.verify_api_key(k1) == "user-a"
    assert auth.verify_api_key(k2) == "user-b"
    assert len(json.loads(isolated_keys_file.read_text())) == 2
