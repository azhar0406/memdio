import pytest
from fastapi.testclient import TestClient

from memdio.api import auth, server
from memdio.api.server import app, get_storage


class FakeStorage:
    def semantic_search(self, query, top_k=5):
        return []

    def retrieve(self, mem_id):
        raise AssertionError("semantic route was shadowed by dynamic memory route")


def test_semantic_route_not_shadowed():
    app.dependency_overrides[get_storage] = lambda: FakeStorage()
    try:
        response = TestClient(app).get("/memories/semantic", params={"query": "hello"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"results": []}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """TestClient wired to real auth + per-user storage in a temp dir."""
    monkeypatch.setattr(auth, "KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.setattr(server, "DATA_ROOT", str(tmp_path / "data"))
    server._get_user_storage.cache_clear()
    keys = {"alice": auth.create_api_key("alice"), "bob": auth.create_api_key("bob")}
    yield TestClient(app), keys
    server._get_user_storage.cache_clear()


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


def test_missing_auth_header_rejected(api):
    client, _ = api
    resp = client.get("/memories")
    # Header(...) is required -> request is rejected before reaching the handler
    assert resp.status_code in (401, 422)


def test_non_bearer_header_401(api):
    client, _ = api
    resp = client.get("/memories", headers={"Authorization": "Token abc"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid authorization header"


def test_invalid_key_401(api):
    client, _ = api
    resp = client.get("/memories", headers=_auth("memdio_definitely_wrong"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


def test_valid_key_store_and_list(api):
    client, keys = api
    stored = client.post("/memories", json={"content": "hello alice"}, headers=_auth(keys["alice"]))
    assert stored.status_code == 200
    assert stored.json()["content"] == "hello alice"

    listed = client.get("/memories", headers=_auth(keys["alice"]))
    assert listed.status_code == 200
    contents = [r["content"] for r in listed.json()["results"]]
    assert "hello alice" in contents


def test_per_user_isolation(api):
    client, keys = api
    client.post("/memories", json={"content": "alice private"}, headers=_auth(keys["alice"]))

    # Bob must not see Alice's memory
    bob_list = client.get("/memories", headers=_auth(keys["bob"]))
    assert bob_list.status_code == 200
    assert bob_list.json()["results"] == []

    # Alice still sees exactly her own
    alice_list = client.get("/memories", headers=_auth(keys["alice"]))
    assert [r["content"] for r in alice_list.json()["results"]] == ["alice private"]


def test_recall_returns_stored_content(api):
    client, keys = api
    stored = client.post(
        "/memories",
        json={"content": "alice remembers the blue umbrella"},
        headers=_auth(keys["alice"]),
    )
    assert stored.status_code == 200

    resp = client.get("/recall", params={"query": "blue umbrella"}, headers=_auth(keys["alice"]))
    assert resp.status_code == 200
    contents = [r["content"] for r in resp.json()["results"]]
    assert "alice remembers the blue umbrella" in contents


def test_recall_requires_auth(api):
    client, _ = api
    # No auth header at all -> rejected before handler.
    missing = client.get("/recall", params={"query": "anything"})
    assert missing.status_code in (401, 422)

    # Invalid key -> 401.
    bad = client.get(
        "/recall",
        params={"query": "anything"},
        headers=_auth("memdio_definitely_wrong"),
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Invalid API key"


def test_user_id_comes_from_key_not_request(api):
    """A client cannot choose its user_id — it is resolved server-side from the key hash."""
    client, keys = api
    # A bogus user_id-looking field in the body must not influence identity/storage.
    resp = client.post(
        "/memories",
        json={"content": "x", "user_id": "../../etc"},
        headers=_auth(keys["alice"]),
    )
    # user_id in the body is ignored (not part of StoreRequest); request still succeeds as alice.
    assert resp.status_code == 200
