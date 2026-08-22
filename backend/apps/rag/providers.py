from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class EmbeddingProviderError(RuntimeError):
    """Raised when a configured embedding provider cannot return safe vectors."""


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one finite, non-empty vector for each supplied text."""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleEmbeddingProvider:
    """Minimal audited client for OpenAI-compatible ``/embeddings`` gateways."""

    base_url: str
    api_key: str
    model_name: str
    timeout_seconds: float

    @classmethod
    def from_settings(cls) -> "OpenAICompatibleEmbeddingProvider":
        if not (
            settings.RAG_EMBEDDING_BASE_URL
            and settings.RAG_EMBEDDING_API_KEY
            and settings.RAG_EMBEDDING_MODEL
        ):
            raise EmbeddingProviderError("The RAG embedding provider is not configured.")
        return cls(
            base_url=settings.RAG_EMBEDDING_BASE_URL,
            api_key=settings.RAG_EMBEDDING_API_KEY,
            model_name=settings.RAG_EMBEDDING_MODEL,
            timeout_seconds=settings.RAG_EMBEDDING_TIMEOUT_SECONDS,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > settings.RAG_EMBEDDING_BATCH_SIZE:
            raise EmbeddingProviderError("Embedding batch exceeds the configured maximum.")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingProviderError("Embedding input must contain non-empty strings.")
        payload = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float",
        }
        request = Request(
            self.base_url.rstrip("/") + "/embeddings",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = response.read(settings.RAG_EMBEDDING_RESPONSE_MAX_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise EmbeddingProviderError("The embedding provider is unavailable.") from exc
        if len(body) > settings.RAG_EMBEDDING_RESPONSE_MAX_BYTES:
            raise EmbeddingProviderError("The embedding response exceeds the size limit.")
        try:
            decoded = json.loads(body.decode("utf-8"))
            items = decoded["data"]
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise EmbeddingProviderError("The embedding response has an invalid schema.") from exc
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbeddingProviderError("The embedding response count does not match its input.")
        vectors: list[list[float]] = []
        for expected_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("index") != expected_index:
                raise EmbeddingProviderError("The embedding response order is invalid.")
            raw_vector = item.get("embedding")
            if not isinstance(raw_vector, list) or not raw_vector:
                raise EmbeddingProviderError("The embedding response contains an empty vector.")
            try:
                vector = [float(value) for value in raw_vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError("The embedding response contains a non-numeric vector.") from exc
            if len(vector) > settings.RAG_EMBEDDING_MAX_DIMENSION or not all(
                math.isfinite(value) for value in vector
            ):
                raise EmbeddingProviderError("The embedding response contains an unsafe vector.")
            vectors.append(vector)
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise EmbeddingProviderError("The embedding response has inconsistent vector dimensions.")
        return vectors
