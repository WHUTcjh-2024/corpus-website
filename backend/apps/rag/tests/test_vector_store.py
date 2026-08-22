from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.rag.vector_store import MilvusVectorStore, VectorRecord


class _Schema:
    def __init__(self) -> None:
        self.fields: list[dict] = []

    def add_field(self, **kwargs) -> None:
        self.fields.append(kwargs)


class _Indexes:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add_index(self, **kwargs) -> None:
        self.items.append(kwargs)


class _MilvusClientSpy:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.schema = _Schema()
        self.indexes = _Indexes()
        self.inserted: list[dict] = []
        self.search_response = [[
            {
                "id": "paragraph:1:1",
                "distance": 0.91,
                "entity": {"text_sha256": "a" * 64},
            }
        ]]

    def has_collection(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def drop_collection(self, *, collection_name: str) -> None:
        self.collections.discard(collection_name)

    def create_schema(self, **kwargs):
        self.schema_options = kwargs
        return self.schema

    def prepare_index_params(self):
        return self.indexes

    def create_collection(self, *, collection_name: str, schema, index_params, **kwargs) -> None:
        self.collections.add(collection_name)
        self.created = {"name": collection_name, "schema": schema, "indexes": index_params, **kwargs}

    def insert(self, *, collection_name: str, data, **kwargs) -> None:
        self.inserted.extend(data)

    def flush(self, *, collection_name: str, **kwargs) -> None:
        self.flushed = collection_name

    def load_collection(self, *, collection_name: str, **kwargs) -> None:
        self.loaded = collection_name

    def search(self, **kwargs):
        self.search_request = kwargs
        return self.search_response

    def query(self, **kwargs):
        self.count_request = kwargs
        return [{"count(*)": len(self.inserted)}]

    def list_collections(self, **kwargs):
        return list(self.collections)


class MilvusVectorStoreTests(SimpleTestCase):
    def setUp(self) -> None:
        self.spy = _MilvusClientSpy()
        self.store = MilvusVectorStore(
            uri="http://milvus.test:19530",
            token="",
            database="default",
            timeout_seconds=5,
            hnsw_m=16,
            hnsw_ef_construction=200,
            hnsw_ef_search=64,
        )

    def test_creates_hnsw_collection_and_uses_server_side_language_filter(self):
        with patch("apps.rag.vector_store.MilvusVectorStore._client", return_value=self.spy):
            self.store.recreate_collection(collection_name="rag_abc", dimension=3)
            self.store.insert(
                collection_name="rag_abc",
                records=[
                    VectorRecord(
                        chunk_id="paragraph:1:1",
                        text_sha256="a" * 64,
                        language="en",
                        embedding=[0.1, 0.2, 0.3],
                    )
                ],
            )
            self.store.finalize(collection_name="rag_abc")
            self.assertEqual(self.store.count(collection_name="rag_abc"), 1)
            hits = self.store.search(
                collection_name="rag_abc",
                query_vector=[0.1, 0.2, 0.3],
                languages=("en", "zh_en"),
                limit=10,
            )

        self.assertEqual(self.spy.schema.fields[-1]["dim"], 3)
        self.assertIn(
            {"field_name": "embedding", "index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}},
            self.spy.indexes.items,
        )
        self.assertEqual(self.spy.search_request["filter"], 'language in ["en", "zh_en"]')
        self.assertEqual(self.spy.search_request["search_params"], {"metric_type": "COSINE", "params": {"ef": 64}})
        self.assertEqual(hits[0].chunk_id, "paragraph:1:1")
        self.assertEqual(hits[0].score, 0.91)
