import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.rag.retrieval import HybridRagIndex, RagIndexUnavailable

from .fakes import InMemoryMilvusStore


class FakeEmbeddingProvider:
    model_name = "test-embedding-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        text = text.casefold()
        return [
            1.0 if "governance" in text or "policy" in text else 0.1,
            1.0 if "concurrency" in text or "queue" in text else 0.1,
            1.0,
        ]


class HybridRagIndexTests(SimpleTestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.corpus_id = "corpus-1"
        processed = self.root / "processed" / self.corpus_id
        processed.mkdir(parents=True)
        chunks = [
            {
                "id": "paragraph:policy:1",
                "text": "Policy governance requires citations and approval.",
                "language": "en",
                "document_id": "document-policy",
                "source_filename": "policy.txt",
                "kind": "paragraph",
                "metadata": {"paragraph_id": "policy"},
            },
            {
                "id": "paragraph:queue:1",
                "text": "Queue concurrency protects asynchronous worker capacity.",
                "language": "en",
                "document_id": "document-queue",
                "source_filename": "queue.txt",
                "kind": "paragraph",
                "metadata": {"paragraph_id": "queue"},
            },
            {
                "id": "parallel:1:1",
                "text": "[ZH] 语料治理需要证据。 [EN] Corpus governance needs evidence.",
                "language": "zh_en",
                "document_id": "parallel-1",
                "source_filename": "parallel.tsv",
                "kind": "parallel_pair",
                "metadata": {"pair_id": "pair-1"},
            },
        ]
        (processed / "rag_chunks.jsonl").write_text(
            "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
            encoding="utf-8",
        )
        self.store = InMemoryMilvusStore()
        self.index = HybridRagIndex(
            data_root=self.root,
            corpus_id=self.corpus_id,
            vector_store=self.store,
        )
        self.provider = FakeEmbeddingProvider()

    def test_builds_versioned_milvus_hnsw_collection_and_hybrid_retrieves_cited_chunks(self):
        result = self.index.build(provider=self.provider)
        searched = self.index.search(
            query="governance policy",
            provider=self.provider,
            max_results=2,
            language="en",
        )

        self.assertEqual(result.chunk_count, 3)
        self.assertEqual(result.vector_dimension, 3)
        self.assertTrue(result.collection_name.startswith("rag_"))
        self.assertTrue(result.artifact_path.startswith("milvus://default/rag_"))
        self.assertEqual(searched.embedding_model, "test-embedding-v1")
        self.assertEqual(searched.total, 3)
        self.assertEqual(searched.hits[0].citation_id, "rag:paragraph:policy:1")
        self.assertGreater(searched.hits[0].semantic_score, 0)
        self.assertGreater(searched.hits[0].lexical_score, 0)
        self.assertTrue((self.root / "indexes" / self.corpus_id / "rag_index.json").is_file())

    def test_rejects_a_vector_index_after_its_chunk_manifest_changes(self):
        self.index.build(provider=self.provider)
        chunk_path = self.root / "processed" / self.corpus_id / "rag_chunks.jsonl"
        chunk_path.write_text(chunk_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with self.assertRaises(RagIndexUnavailable):
            self.index.search(
                query="governance",
                provider=self.provider,
                max_results=3,
            )
