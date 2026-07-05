"""API key authentication for multi-tenant memdio SaaS."""

import hashlib
import json
import logging
import os
import secrets

logger = logging.getLogger(__name__)

KEYS_FILE = os.environ.get("MEMDIO_KEYS_FILE", "/data/memdio/api_keys.json")


def _load_keys() -> dict:
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_keys(keys: dict):
    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)
    os.chmod(KEYS_FILE, 0o600)


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def create_api_key(user_id: str) -> str:
    """Create a new API key for a user. Returns the raw key (only shown once)."""
    keys = _load_keys()
    raw_key = f"memdio_{secrets.token_urlsafe(32)}"
    keys[_hash_key(raw_key)] = {"user_id": user_id}
    _save_keys(keys)
    logger.info("Created API key for user %s", user_id)
    return raw_key


def verify_api_key(api_key: str) -> str | None:
    """Verify an API key. Returns user_id if valid, None if not."""
    keys = _load_keys()
    entry = keys.get(_hash_key(api_key))
    if entry:
        return entry["user_id"]
    return None


def revoke_api_key(api_key: str) -> bool:
    """Revoke an API key."""
    keys = _load_keys()
    h = _hash_key(api_key)
    if h in keys:
        del keys[h]
        _save_keys(keys)
        return True
    return False
