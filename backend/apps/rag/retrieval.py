from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from django.conf import settings
from django.utils import timezone

from apps.processing.text import normalize_token, token_matches

from .providers import EmbeddingProvider, EmbeddingProviderError
from .vector_store import (
    MilvusVectorStore,
    MilvusVectorStoreError,
    VectorRecord,
    VectorStore,
)


RAG_SCHEMA_VERSION = 2
_MAX_JSONL_LINE_BYTES = 256 * 1024


class RagIndexUnavailable(RuntimeError):
    """Raised when a RAG index is absent, stale, or cannot be trusted."""


class RagQueryError(ValueError):
    """Raised when a semantic retrieval query exceeds the runtime contract."""


@dataclass(frozen=True, slots=True)
class RagChunk:
    id: str
    text: str
    language: str
    document_id: str
    source_filename: str
    kind: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RagHit:
    citation_id: str
    chunk_id: str
    text: str
    language: str
    document_id: str
    source_filename: str
    kind: str
    semantic_score: float
    lexical_score: float
    fused_score: float
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    query: str
    hits: tuple[RagHit, ...]
    total: int
    embedding_model: str
    vector_dimension: int


@dataclass(frozen=True, slots=True)
class RagIndexBuildResult:
    chunk_manifest_sha256: str
    embedding_model: str
    vector_dimension: int
    chunk_count: int
    vector_count: int
    artifact_path: str
    collection_name: str


class HybridRagIndex:
    """Versioned Milvus HNSW retrieval with application-layer BM25 + RRF.

    Corpus processing owns the canonical ``rag_chunks.jsonl`` artifact. Milvus
    contains only vectors, source hashes and a scalar language field. The
    manifest binds a single immutable chunk version to a versioned Milvus
    collection, so a failed rebuild never serves partial or mismatched context.
    """

    def __init__(
        self,
        *,
        data_root: Path,
        corpus_id: str,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.corpus_id = str(corpus_id)
        self.processed_dir = self.data_root / "processed" / self.corpus_id
        self.index_dir = self.data_root / "indexes" / self.corpus_id
        self.chunk_path = self.processed_dir / "rag_chunks.jsonl"
        self.metadata_path = self.index_dir / "rag_index.json"
        self.vector_store = vector_store or MilvusVectorStore.from_settings()

    def build(self, *, provider: EmbeddingProvider) -> RagIndexBuildResult:
        chunks = self._load_chunks()
        if not chunks:
            raise RagIndexUnavailable("The corpus has no RAG chunks to embed.")
        if len(chunks) > settings.RAG_MAX_CHUNKS:
            raise RagIndexUnavailable("The corpus exceeds the configured RAG chunk limit.")
        manifest = _sha256_file(self.chunk_path)
        collection_name = _collection_name(
            corpus_id=self.corpus_id,
            manifest=manifest,
            embedding_model=provider.model_name,
        )
        dimension = 0
        vector_count = 0
        collection_created = False
        try:
            for batch in _batches(chunks, settings.RAG_EMBEDDING_BATCH_SIZE):
                embeddings = _embed(provider, [chunk.text for chunk in batch])
                if len(embeddings) != len(batch):
                    raise EmbeddingProviderError("The embedding provider returned an incomplete batch.")
                records: list[VectorRecord] = []
                for chunk, embedding in zip(batch, embeddings, strict=True):
                    vector = _validated_vector(embedding, expected_dimension=dimension)
                    dimension = len(vector)
                    records.append(
                        VectorRecord(
                            chunk_id=chunk.id,
                            text_sha256=_sha256_text(chunk.text),
                            language=chunk.language,
                            embedding=vector,
                        )
                    )
                if not collection_created:
                    self.vector_store.recreate_collection(
                        collection_name=collection_name,
                        dimension=dimension,
                    )
                    collection_created = True
                self.vector_store.insert(collection_name=collection_name, records=records)
                vector_count += len(records)
            if not dimension:
                raise EmbeddingProviderError("The embedding provider returned no vector dimension.")
            self.vector_store.finalize(collection_name=collection_name)
            if self.vector_store.count(collection_name=collection_name) != vector_count:
                raise RagIndexUnavailable("Milvus did not persist the complete RAG vector collection.")
        except Exception:
            if collection_created:
                try:
                    self.vector_store.drop_collection(collection_name=collection_name)
                except MilvusVectorStoreError:
                    # The versioned collection is unpublished, so a later rebuild
                    # can safely replace it even when this best-effort cleanup fails.
                    pass
            raise

        metadata = {
            "schema_version": RAG_SCHEMA_VERSION,
            "vector_backend": "milvus",
            "corpus_id": self.corpus_id,
            "chunk_manifest_sha256": manifest,
            "embedding_model": provider.model_name,
            "vector_dimension": dimension,
            "chunk_count": len(chunks),
            "vector_count": vector_count,
            "collection_name": collection_name,
            "hnsw": {
                "metric": "COSINE",
                "m": settings.RAG_MILVUS_HNSW_M,
                "ef_construction": settings.RAG_MILVUS_HNSW_EF_CONSTRUCTION,
                "ef_search": settings.RAG_MILVUS_HNSW_EF_SEARCH,
            },
            "built_at": timezone.now().isoformat(),
        }
        self.index_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.metadata_path, metadata)
        return RagIndexBuildResult(
            chunk_manifest_sha256=manifest,
            embedding_model=provider.model_name,
            vector_dimension=dimension,
            chunk_count=len(chunks),
            vector_count=vector_count,
            artifact_path=f"milvus://{settings.RAG_MILVUS_DATABASE}/{collection_name}",
            collection_name=collection_name,
        )

    def search(
        self,
        *,
        query: str,
        provider: EmbeddingProvider,
        max_results: int,
        language: str | None = None,
    ) -> RagSearchResult:
        query = " ".join(query.split())
        if not query or len(query) > 200:
            raise RagQueryError("query must contain 1 to 200 characters.")
        if not 1 <= max_results <= 10:
            raise RagQueryError("max_results must be between 1 and 10.")
        if language not in {None, "", "zh", "en"}:
            raise RagQueryError("language must be zh or en.")

        metadata = self._load_metadata()
        chunks = self._load_chunks()
        if _sha256_file(self.chunk_path) != metadata["chunk_manifest_sha256"]:
            raise RagIndexUnavailable("The RAG index is stale for this corpus version.")
        if len(chunks) != metadata["chunk_count"]:
            raise RagIndexUnavailable("The RAG chunk count does not match its manifest.")
        try:
            if self.vector_store.count(collection_name=metadata["collection_name"]) != metadata["vector_count"]:
                raise RagIndexUnavailable("The Milvus RAG collection count does not match its manifest.")
        except MilvusVectorStoreError as exc:
            raise RagIndexUnavailable("The Milvus RAG collection is unavailable.") from exc
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        if len(chunks_by_id) != len(chunks):
            raise RagIndexUnavailable("The RAG chunk manifest contains duplicate chunk IDs.")

        query_embeddings = _embed(provider, [query])
        if len(query_embeddings) != 1:
            raise EmbeddingProviderError("The embedding provider returned an invalid query vector.")
        query_vector = _validated_vector(
            query_embeddings[0], expected_dimension=metadata["vector_dimension"]
        )
        candidates = [
            chunk for chunk in chunks if not language or chunk.language in {language, "zh_en"}
        ]
        if not candidates:
            return RagSearchResult(
                query=query,
                hits=(),
                total=0,
                embedding_model=metadata["embedding_model"],
                vector_dimension=metadata["vector_dimension"],
            )
        languages = (language, "zh_en") if language else ("zh", "en", "zh_en")
        dense_limit = min(
            len(candidates),
            max(max_results, max_results * settings.RAG_MILVUS_CANDIDATE_MULTIPLIER),
        )
        try:
            dense_hits = self.vector_store.search(
                collection_name=metadata["collection_name"],
                query_vector=query_vector,
                languages=languages,
                limit=dense_limit,
            )
        except MilvusVectorStoreError as exc:
            raise RagIndexUnavailable("The Milvus RAG collection is unavailable.") from exc
        semantic_scores: dict[str, float] = {}
        candidate_ids = {chunk.id for chunk in candidates}
        for hit in dense_hits:
            chunk = chunks_by_id.get(hit.chunk_id)
            if (
                chunk is None
                or hit.chunk_id not in candidate_ids
                or hit.text_sha256 != _sha256_text(chunk.text)
            ):
                raise RagIndexUnavailable("Milvus does not match the canonical RAG manifest.")
            semantic_scores[hit.chunk_id] = hit.score
        lexical_scores = {
            chunk_id: score
            for chunk_id, score in _bm25_scores(query, candidates).items()
            if score > 0
        }
        semantic_ranks = _ranks(semantic_scores)
        lexical_ranks = _ranks(lexical_scores)
        result_ids = set(semantic_ranks) | set(lexical_ranks)
        fused_scores = {
            chunk_id: _reciprocal_rank_fusion(
                semantic_ranks.get(chunk_id), lexical_ranks.get(chunk_id)
            )
            for chunk_id in result_ids
        }
        ordered = sorted(
            (chunks_by_id[chunk_id] for chunk_id in result_ids),
            key=lambda chunk: (
                -fused_scores[chunk.id],
                -semantic_scores.get(chunk.id, float("-inf")),
                -lexical_scores.get(chunk.id, 0.0),
                chunk.id,
            ),
        )
        hits = tuple(
            RagHit(
                citation_id=f"rag:{chunk.id}",
                chunk_id=chunk.id,
                text=chunk.text,
                language=chunk.language,
                document_id=chunk.document_id,
                source_filename=chunk.source_filename,
                kind=chunk.kind,
                semantic_score=round(semantic_scores.get(chunk.id, 0.0), 6),
                lexical_score=round(lexical_scores.get(chunk.id, 0.0), 6),
                fused_score=round(fused_scores[chunk.id], 8),
                metadata=chunk.metadata,
            )
            for chunk in ordered[:max_results]
        )
        return RagSearchResult(
            query=query,
            hits=hits,
            total=len(candidates),
            embedding_model=metadata["embedding_model"],
            vector_dimension=metadata["vector_dimension"],
        )

    def _load_metadata(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RagIndexUnavailable("The RAG index metadata is unavailable.") from exc
        if not isinstance(payload, dict):
            raise RagIndexUnavailable("The RAG index metadata is invalid.")
        required = {
            "schema_version": int,
            "vector_backend": str,
            "corpus_id": str,
            "chunk_manifest_sha256": str,
            "embedding_model": str,
            "vector_dimension": int,
            "chunk_count": int,
            "vector_count": int,
            "collection_name": str,
        }
        if (
            any(not isinstance(payload.get(name), expected) for name, expected in required.items())
            or payload["schema_version"] != RAG_SCHEMA_VERSION
            or payload["vector_backend"] != "milvus"
            or payload["corpus_id"] != self.corpus_id
            or len(payload["chunk_manifest_sha256"]) != 64
            or not payload["embedding_model"]
            or not 1 <= payload["vector_dimension"] <= settings.RAG_EMBEDDING_MAX_DIMENSION
            or not 1 <= payload["chunk_count"] <= settings.RAG_MAX_CHUNKS
            or payload["vector_count"] != payload["chunk_count"]
            or not _is_safe_collection_name(payload["collection_name"])
        ):
            raise RagIndexUnavailable("The RAG index metadata is incompatible.")
        return payload

    def _load_chunks(self) -> list[RagChunk]:
        rows = _read_jsonl(self.chunk_path, label="RAG chunk manifest")
        chunks: list[RagChunk] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise RagIndexUnavailable("The RAG chunk manifest contains an invalid row.")
            identifier = row.get("id")
            text = row.get("text")
            language = row.get("language")
            document_id = row.get("document_id")
            source_filename = row.get("source_filename")
            kind = row.get("kind")
            metadata = row.get("metadata", {})
            if (
                not all(
                    isinstance(value, str) and value
                    for value in (identifier, text, language, document_id, source_filename, kind)
                )
                or len(identifier) > 180
                or len(text) > settings.RAG_CHUNK_MAX_CHARACTERS
                or language not in {"zh", "en", "zh_en"}
                or not isinstance(metadata, dict)
                or identifier in seen_ids
            ):
                raise RagIndexUnavailable("The RAG chunk manifest contains an unsafe row.")
            seen_ids.add(identifier)
            chunks.append(
                RagChunk(
                    id=identifier,
                    text=text,
                    language=language,
                    document_id=document_id,
                    source_filename=source_filename,
                    kind=kind,
                    metadata={
                        str(key)[:80]: _bounded_metadata(value)
                        for key, value in list(metadata.items())[:20]
                    },
                )
            )
        return chunks


def _embed(provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    try:
        return provider.embed(texts)
    except EmbeddingProviderError:
        raise
    except Exception as exc:
        raise EmbeddingProviderError("The embedding provider failed unexpectedly.") from exc


def _read_jsonl(path: Path, *, label: str) -> list[Any]:
    try:
        with path.open("rb") as handle:
            rows: list[Any] = []
            for line_number, raw in enumerate(handle, start=1):
                if len(raw) > _MAX_JSONL_LINE_BYTES:
                    raise RagIndexUnavailable(f"{label} line {line_number} exceeds the size limit.")
                if not raw.strip():
                    continue
                rows.append(json.loads(raw.decode("utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RagIndexUnavailable(f"{label} is unavailable.") from exc
    return rows


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".rag-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_vector(value: Any, *, expected_dimension: int) -> list[float]:
    if not isinstance(value, list) or not value:
        raise EmbeddingProviderError("A vector must be a non-empty list.")
    if len(value) > settings.RAG_EMBEDDING_MAX_DIMENSION:
        raise EmbeddingProviderError("A vector exceeds the configured dimension limit.")
    try:
        vector = [float(component) for component in value]
    except (TypeError, ValueError) as exc:
        raise EmbeddingProviderError("A vector contains a non-numeric component.") from exc
    if not all(math.isfinite(component) for component in vector):
        raise EmbeddingProviderError("A vector contains a non-finite component.")
    if expected_dimension and len(vector) != expected_dimension:
        raise EmbeddingProviderError("A vector dimension does not match its index.")
    if not any(component != 0 for component in vector):
        raise EmbeddingProviderError("A vector must not be all zeroes.")
    return vector


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise RagIndexUnavailable("The RAG chunk manifest is unavailable.") from exc
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _collection_name(*, corpus_id: str, manifest: str, embedding_model: str) -> str:
    entropy = hashlib.sha256(f"{corpus_id}:{manifest}:{embedding_model}".encode("utf-8")).hexdigest()
    return f"{settings.RAG_MILVUS_COLLECTION_PREFIX}{entropy[:32]}"


def _is_safe_collection_name(value: str) -> bool:
    prefix = settings.RAG_MILVUS_COLLECTION_PREFIX
    suffix = value.removeprefix(prefix)
    return (
        value.startswith(prefix)
        and len(suffix) == 32
        and all(char in "0123456789abcdef" for char in suffix)
    )


def _batches(items: list[RagChunk], size: int) -> Iterable[list[RagChunk]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _terms(value: str, language: str) -> list[str]:
    languages = ("zh", "en") if language == "zh_en" else (language,)
    terms: list[str] = []
    for current_language in languages:
        for match in token_matches(value, current_language):
            token = normalize_token(match.group(), current_language).strip()
            if len(token) > 1 and token.isalnum():
                terms.append(token)
    return terms


def _bm25_scores(query: str, chunks: list[RagChunk]) -> dict[str, float]:
    query_terms = _terms(query, "zh_en")
    if not query_terms:
        return {chunk.id: 0.0 for chunk in chunks}
    corpus_terms = {chunk.id: _terms(chunk.text, chunk.language) for chunk in chunks}
    document_frequency = Counter(term for terms in corpus_terms.values() for term in set(terms))
    total_documents = len(chunks)
    average_length = sum(len(terms) for terms in corpus_terms.values()) / total_documents
    scores: dict[str, float] = {}
    for chunk in chunks:
        terms = corpus_terms[chunk.id]
        frequency = Counter(terms)
        denominator_length = len(terms) / average_length if average_length else 1.0
        score = 0.0
        for term in set(query_terms):
            count = frequency.get(term, 0)
            if not count:
                continue
            idf = math.log(
                1
                + (total_documents - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            score += idf * (count * 2.0) / (count + 1.2 * (0.25 + 0.75 * denominator_length))
        scores[chunk.id] = score
    return scores


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    return {identifier: position for position, identifier in enumerate(ordered, start=1)}


def _reciprocal_rank_fusion(semantic_rank: int | None, lexical_rank: int | None) -> float:
    return sum(1 / (60 + rank) for rank in (semantic_rank, lexical_rank) if rank is not None)


def _bounded_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        return str(value)[:200]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _bounded_metadata(item, depth=depth + 1)
            for key, item in list(value.items())[:10]
        }
    if isinstance(value, list):
        return [_bounded_metadata(item, depth=depth + 1) for item in value[:10]]
    if isinstance(value, str):
        return value[:300]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]
