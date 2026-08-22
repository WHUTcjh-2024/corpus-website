from __future__ import annotations

import math

from apps.rag.vector_store import VectorRecord, VectorSearchHit


class InMemoryMilvusStore:
    """Contract double for retrieval tests; production always uses PyMilvus."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, VectorRecord]] = {}
        self.dimensions: dict[str, int] = {}

    def recreate_collection(self, *, collection_name: str, dimension: int) -> None:
        self.collections[collection_name] = {}
        self.dimensions[collection_name] = dimension

    def insert(self, *, collection_name: str, records: list[VectorRecord]) -> None:
        records_by_id = self.collections[collection_name]
        for record in records:
            if len(record.embedding) != self.dimensions[collection_name]:
                raise ValueError("Unexpected vector dimension.")
            records_by_id[record.chunk_id] = record

    def finalize(self, *, collection_name: str) -> None:
        if collection_name not in self.collections:
            raise ValueError("Unknown collection.")

    def count(self, *, collection_name: str) -> int:
        return len(self.collections[collection_name])

    def search(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        languages: tuple[str, ...],
        limit: int,
    ) -> list[VectorSearchHit]:
        records = self.collections[collection_name].values()
        hits = [
            VectorSearchHit(
                chunk_id=record.chunk_id,
                text_sha256=record.text_sha256,
                score=_cosine(query_vector, record.embedding),
            )
            for record in records
            if record.language in languages
        ]
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))[:limit]

    def drop_collection(self, *, collection_name: str) -> None:
        self.collections.pop(collection_name, None)
        self.dimensions.pop(collection_name, None)

    def ping(self) -> None:
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0
