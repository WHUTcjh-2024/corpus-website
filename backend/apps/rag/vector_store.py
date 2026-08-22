from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings


class MilvusVectorStoreError(RuntimeError):
    """Raised when a vector-store operation violates the RAG contract."""


class MilvusVectorStoreUnavailable(MilvusVectorStoreError):
    """Raised for a transient Milvus connectivity or service failure."""


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    text_sha256: str
    language: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    chunk_id: str
    text_sha256: str
    score: float


class VectorStore(Protocol):
    def recreate_collection(self, *, collection_name: str, dimension: int) -> None:
        """Create a clean, versioned vector collection with its ANN index."""

    def insert(self, *, collection_name: str, records: list[VectorRecord]) -> None:
        """Write a bounded batch of immutable vector records."""

    def finalize(self, *, collection_name: str) -> None:
        """Make all inserts searchable before publishing the manifest."""

    def count(self, *, collection_name: str) -> int:
        """Return the number of persisted vector records in a collection."""

    def search(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        languages: tuple[str, ...],
        limit: int,
    ) -> list[VectorSearchHit]:
        """Return server-filtered dense candidates in descending similarity order."""

    def drop_collection(self, *, collection_name: str) -> None:
        """Remove an unpublished, failed collection version."""

    def ping(self) -> None:
        """Verify that Milvus can answer a control-plane request."""


@dataclass(frozen=True, slots=True)
class MilvusVectorStore:
    """Small audited adapter around PyMilvus' stable ``MilvusClient`` API.

    The collection is deliberately versioned per corpus manifest.  It contains
    only derived vectors, source hashes and a language filter field; canonical
    chunk text continues to live in the processed artifact owned by processing.
    """

    uri: str
    token: str
    database: str
    timeout_seconds: float
    hnsw_m: int
    hnsw_ef_construction: int
    hnsw_ef_search: int

    @classmethod
    def from_settings(cls) -> "MilvusVectorStore":
        if not settings.RAG_MILVUS_URI:
            raise MilvusVectorStoreUnavailable("RAG_MILVUS_URI is not configured.")
        return cls(
            uri=settings.RAG_MILVUS_URI,
            token=settings.RAG_MILVUS_TOKEN,
            database=settings.RAG_MILVUS_DATABASE,
            timeout_seconds=settings.RAG_MILVUS_TIMEOUT_SECONDS,
            hnsw_m=settings.RAG_MILVUS_HNSW_M,
            hnsw_ef_construction=settings.RAG_MILVUS_HNSW_EF_CONSTRUCTION,
            hnsw_ef_search=settings.RAG_MILVUS_HNSW_EF_SEARCH,
        )

    def recreate_collection(self, *, collection_name: str, dimension: int) -> None:
        client = self._client()
        try:
            if client.has_collection(collection_name=collection_name):
                client.drop_collection(collection_name=collection_name)
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="chunk_id",
                datatype=self._data_type("VARCHAR"),
                is_primary=True,
                max_length=180,
            )
            schema.add_field(
                field_name="text_sha256",
                datatype=self._data_type("VARCHAR"),
                max_length=64,
            )
            schema.add_field(
                field_name="language",
                datatype=self._data_type("VARCHAR"),
                max_length=8,
            )
            schema.add_field(
                field_name="embedding",
                datatype=self._data_type("FLOAT_VECTOR"),
                dim=dimension,
            )
            indexes = client.prepare_index_params()
            indexes.add_index(
                field_name="embedding",
                index_type="HNSW",
                metric_type="COSINE",
                params={
                    "M": self.hnsw_m,
                    "efConstruction": self.hnsw_ef_construction,
                },
            )
            indexes.add_index(field_name="language", index_type="INVERTED")
            client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=indexes,
                consistency_level="Strong",
            )
        except Exception as exc:
            raise self._operation_error("Milvus could not create the RAG collection.", exc) from exc

    def insert(self, *, collection_name: str, records: list[VectorRecord]) -> None:
        if not records:
            return
        try:
            self._client().insert(
                collection_name=collection_name,
                data=[
                    {
                        "chunk_id": record.chunk_id,
                        "text_sha256": record.text_sha256,
                        "language": record.language,
                        "embedding": record.embedding,
                    }
                    for record in records
                ],
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise self._operation_error("Milvus could not insert the RAG vector batch.", exc) from exc

    def finalize(self, *, collection_name: str) -> None:
        try:
            client = self._client()
            client.flush(collection_name=collection_name, timeout=self.timeout_seconds)
            client.load_collection(collection_name=collection_name, timeout=self.timeout_seconds)
        except Exception as exc:
            raise self._operation_error("Milvus could not finalize the RAG collection.", exc) from exc

    def count(self, *, collection_name: str) -> int:
        try:
            response = self._client().query(
                collection_name=collection_name,
                filter="",
                output_fields=["count(*)"],
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise self._operation_error("Milvus could not count the RAG collection.", exc) from exc
        if (
            not isinstance(response, list)
            or len(response) != 1
            or not isinstance(response[0], dict)
            or not isinstance(response[0].get("count(*)"), int)
        ):
            raise MilvusVectorStoreError("Milvus returned an invalid collection count.")
        return response[0]["count(*)"]

    def search(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        languages: tuple[str, ...],
        limit: int,
    ) -> list[VectorSearchHit]:
        if not languages:
            return []
        language_filter = "language in [" + ", ".join(f'\"{language}\"' for language in languages) + "]"
        try:
            response = self._client().search(
                collection_name=collection_name,
                data=[query_vector],
                anns_field="embedding",
                filter=language_filter,
                limit=limit,
                output_fields=["text_sha256"],
                search_params={
                    "metric_type": "COSINE",
                    "params": {"ef": self.hnsw_ef_search},
                },
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise self._operation_error("Milvus could not search the RAG collection.", exc) from exc
        if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], list):
            raise MilvusVectorStoreError("Milvus returned an invalid search response.")
        hits: list[VectorSearchHit] = []
        for item in response[0]:
            if not isinstance(item, dict):
                raise MilvusVectorStoreError("Milvus returned an invalid search hit.")
            entity = item.get("entity", {})
            chunk_id = item.get("id", item.get("chunk_id"))
            text_sha256 = entity.get("text_sha256") if isinstance(entity, dict) else None
            score = item.get("distance")
            if (
                not isinstance(chunk_id, str)
                or not isinstance(text_sha256, str)
                or len(text_sha256) != 64
                or not isinstance(score, (int, float))
            ):
                raise MilvusVectorStoreError("Milvus returned an unsafe search hit.")
            hits.append(
                VectorSearchHit(
                    chunk_id=chunk_id,
                    text_sha256=text_sha256,
                    score=float(score),
                )
            )
        return hits

    def drop_collection(self, *, collection_name: str) -> None:
        try:
            client = self._client()
            if client.has_collection(collection_name=collection_name):
                client.drop_collection(collection_name=collection_name)
        except Exception as exc:
            raise self._operation_error("Milvus could not clean up the failed RAG collection.", exc) from exc

    def ping(self) -> None:
        try:
            self._client().list_collections(timeout=self.timeout_seconds)
        except Exception as exc:
            raise self._operation_error("Milvus is unavailable.", exc) from exc

    def _client(self):
        try:
            from pymilvus import MilvusClient

            return MilvusClient(
                uri=self.uri,
                token=self.token or None,
                db_name=self.database,
                timeout=self.timeout_seconds,
            )
        except ImportError as exc:
            raise MilvusVectorStoreUnavailable("PyMilvus is not installed.") from exc
        except Exception as exc:
            raise self._operation_error("Milvus connection initialization failed.", exc) from exc

    @staticmethod
    def _data_type(name: str):
        try:
            from pymilvus import DataType

            return getattr(DataType, name)
        except (ImportError, AttributeError) as exc:
            raise MilvusVectorStoreError("The installed PyMilvus client is incompatible.") from exc

    @staticmethod
    def _operation_error(message: str, exc: Exception) -> MilvusVectorStoreError:
        text = str(exc).casefold()
        if any(marker in text for marker in ("timeout", "unavailable", "connection", "refused")):
            return MilvusVectorStoreUnavailable(message)
        return MilvusVectorStoreError(message)
