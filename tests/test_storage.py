import pytest
import numpy as np

from memdio.core.storage import StorageManager
from memdio.core.validators import ValidationError


class TestStorageManager:
    def test_default_embed_model_uses_384_dim_and_semantic_search_works(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMDIO_EMBED_MODEL", raising=False)

        class FakeEmbedder:
            def embed(self, texts):
                for text in texts:
                    vec = np.zeros(384, dtype=np.float32)
                    if "hello" in text.lower():
                        vec[0] = 1.0
                    elif "goodbye" in text.lower():
                        vec[1] = 1.0
                    else:
                        vec[2] = 1.0
                    yield vec

        monkeypatch.setattr(StorageManager, "_get_embedder", lambda self: FakeEmbedder())
        storage = StorageManager(base_path=str(tmp_path / "memdio"))

        try:
            assert storage._vector_dim == 384
            mem_id = storage.store("Hello semantic memdio")
            assert storage.retrieve(mem_id) == "Hello semantic memdio"

            if not storage._has_vector:
                pytest.skip("sqlite-vector extension not available in this environment")

            results = storage.semantic_search("hello", top_k=1)
            assert results[0]["id"] == mem_id
        finally:
            storage.close()

    def test_store_and_retrieve(self, storage):
        mem_id = storage.store("Hello, memdio!")
        content = storage.retrieve(mem_id)
        assert content == "Hello, memdio!"

    def test_store_with_tags(self, storage):
        mem_id = storage.store("tagged memory", tags="test,demo")
        info = storage.get_info(mem_id)
        assert info["tags"] == "test,demo"

    def test_retrieve_not_found(self, storage):
        with pytest.raises(KeyError):
            storage.retrieve("00000000-0000-0000-0000-000000000000")

    def test_search(self, storage):
        storage.store("alpha beta gamma")
        storage.store("delta epsilon")
        results = storage.search("beta")
        assert len(results) == 1
        assert "beta" in results[0]["content"]

    def test_list_all(self, storage):
        storage.store("first")
        storage.store("second")
        results = storage.list_all()
        assert len(results) == 2

    def test_delete(self, storage):
        mem_id = storage.store("to be deleted")
        result = storage.delete(mem_id)
        assert result is True
        with pytest.raises(KeyError):
            storage.retrieve(mem_id)

    def test_delete_not_found(self, storage):
        result = storage.delete("00000000-0000-0000-0000-000000000000")
        assert result is False

    def test_get_info(self, storage):
        mem_id = storage.store("info test")
        info = storage.get_info(mem_id)
        assert info["id"] == mem_id
        assert info["content_length"] == len("info test")
        assert info["encoder_version"] == 3
        assert info["flac_bytes"] > 0
        assert info["storage"] == "flac_blob"

    def test_stats(self, storage):
        storage.store("one")
        storage.store("two")
        s = storage.stats()
        assert s["total_memories"] == 2
        assert s["total_content_bytes"] > 0
        assert s["total_flac_bytes"] > 0

    def test_validation_bad_id(self, storage):
        with pytest.raises(ValidationError):
            storage.retrieve("bad-id")

    def test_validation_oversized_content(self, storage):
        with pytest.raises(ValidationError):
            storage.store("x" * 1_100_000)

    def test_semantic_search_raises_when_vector_unavailable(self, storage):
        # When the sqlite-vector extension is unavailable, semantic search must
        # fail loudly (intentional degraded state) rather than silently return [].
        storage._has_vector = False
        with pytest.raises(RuntimeError):
            storage.semantic_search("anything")

    def test_semantic_search_results_have_no_tags_key(self, monkeypatch, tmp_path):
        # Regression guard: semantic_search MUST NOT surface a 'tags' key. A leaked
        # tags column changed champion-path behaviour (benchmarks/longmemeval/search.py
        # drops tagless semantic hits for aggregation/temporal; the 74.4% run depends
        # on tagless semantic results). The contract: semantic hits are tagless.
        class FakeEmbedder:
            def embed(self, texts):
                for text in texts:
                    vec = np.zeros(384, dtype=np.float32)
                    if "hello" in text.lower():
                        vec[0] = 1.0
                    else:
                        vec[2] = 1.0
                    yield vec

        monkeypatch.setattr(StorageManager, "_get_embedder", lambda self: FakeEmbedder())
        storage = StorageManager(base_path=str(tmp_path / "memdio"))
        try:
            storage.store("Hello semantic memdio", tags="fact")
            if not storage._has_vector:
                pytest.skip("sqlite-vector extension not available in this environment")
            results = storage.semantic_search("hello", top_k=3)
            assert results, "expected at least one semantic result"
            for r in results:
                assert "tags" not in r, f"semantic_search leaked a 'tags' key: {r}"
        finally:
            storage.close()

    def test_recall_tag_filter_uses_batch_tag_lookup(self, monkeypatch, tmp_path):
        # When tag_filter is set (PREF_V3 profile retrieval), semantic candidates
        # must be tag-filtered via a batch DB lookup, not via a leaked 'tags' key.
        # The champion path (no tag_filter) must stay tagless and unchanged.
        class FakeEmbedder:
            def embed(self, texts):
                for text in texts:
                    vec = np.zeros(384, dtype=np.float32)
                    if "hello" in text.lower():
                        vec[0] = 1.0
                    elif "goodbye" in text.lower():
                        vec[1] = 1.0
                    else:
                        vec[2] = 1.0
                    yield vec

        monkeypatch.setattr(StorageManager, "_get_embedder", lambda self: FakeEmbedder())
        storage = StorageManager(base_path=str(tmp_path / "memdio"))
        try:
            pref_id = storage.store("hello preference memory", tags="preference")
            storage.store("goodbye fact memory", tags="fact")
            if not storage._has_vector:
                pytest.skip("sqlite-vector extension not available in this environment")
            results = storage.recall("hello", top_k=10, route=False, tags="preference")
            returned_ids = {r["id"] for r in results}
            assert pref_id in returned_ids
            # The fact-tagged memory must be excluded even though semantic search
            # ranks it near the query.
            assert all(
                storage.get_info(mid)["tags"] == "preference" for mid in returned_ids
            )
        finally:
            storage.close()
