from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from apps.rag.retrieval import HybridRagIndex
from apps.rag.vector_store import MilvusVectorStore


class _DeterministicEmbeddingProvider:
    model_name = "ci-deterministic-embedding-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                1.0 if "governance" in text.casefold() else 0.1,
                1.0 if "queue" in text.casefold() else 0.1,
                1.0,
            ]
            for text in texts
        ]


@skipUnless(
    os.getenv("RAG_MILVUS_INTEGRATION_URI"),
    "Milvus integration checks run only where a standalone service is provisioned.",
)
class MilvusHybridRagIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(
            RAG_MILVUS_URI=os.environ["RAG_MILVUS_INTEGRATION_URI"],
            RAG_MILVUS_TOKEN="",
            RAG_MILVUS_DATABASE="default",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.store = MilvusVectorStore.from_settings()
        self.store.ping()
        self.corpus_id = f"ci-{uuid4()}"
        processed = Path(self.directory.name) / "processed" / self.corpus_id
        processed.mkdir(parents=True)
        (processed / "rag_chunks.jsonl").write_text(
            "".join(
                json.dumps(row) + "\n"
                for row in (
                    {
                        "id": "paragraph:governance:1",
                        "text": "Governance requires cited evidence and approval.",
                        "language": "en",
                        "document_id": "governance",
                        "source_filename": "governance.txt",
                        "kind": "paragraph",
                        "metadata": {},
                    },
                    {
                        "id": "paragraph:queue:1",
                        "text": "A durable queue protects asynchronous worker capacity.",
                        "language": "en",
                        "document_id": "queue",
                        "source_filename": "queue.txt",
                        "kind": "paragraph",
                        "metadata": {},
                    },
                )
            ),
            encoding="utf-8",
        )
        self.index = HybridRagIndex(
            data_root=Path(self.directory.name),
            corpus_id=self.corpus_id,
            vector_store=self.store,
        )

    def test_builds_hnsw_collection_and_searches_through_real_milvus(self):
        result = self.index.build(provider=_DeterministicEmbeddingProvider())
        self.addCleanup(self.store.drop_collection, collection_name=result.collection_name)

        searched = self.index.search(
            query="governance approval",
            provider=_DeterministicEmbeddingProvider(),
            max_results=2,
            language="en",
        )

        self.assertEqual(result.vector_count, 2)
        self.assertEqual(searched.hits[0].citation_id, "rag:paragraph:governance:1")
        self.assertGreater(searched.hits[0].semantic_score, 0.9)
