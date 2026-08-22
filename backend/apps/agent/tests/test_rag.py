import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.agent.models import AgentRunMode, AgentRunStatus
from apps.agent.services import create_agent_run, execute_agent_run
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.rag.models import RagIndex, RagIndexStatus
from apps.rag.retrieval import HybridRagIndex
from apps.rag.tests.fakes import InMemoryMilvusStore


class FakeEmbeddingProvider:
    model_name = "test-embedding-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if "governance" in text.casefold() else 0.1, 1.0] for text in texts]


class AgentRagRunTests(TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.vector_store = InMemoryMilvusStore()
        self.user = get_user_model().objects.create_user("rag-user", password="safe-password")
        UserProfile.objects.create(
            user=self.user,
            full_name="RAG User",
            organization="Test Lab",
            email="rag@example.test",
            role=UserRole.JUNIOR,
            use_purpose="RAG workflow tests",
            application_reason="verify grounded retrieval",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="RAG corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        processed = self.root / "processed" / str(self.corpus.pk)
        processed.mkdir(parents=True)
        (processed / "rag_chunks.jsonl").write_text(
            json.dumps(
                {
                    "id": "paragraph:1:1",
                    "text": "Governance requires cited evidence and explicit approval.",
                    "language": "en",
                    "document_id": "document-1",
                    "source_filename": "governance.txt",
                    "kind": "paragraph",
                    "metadata": {"paragraph_id": "paragraph-1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = HybridRagIndex(
            data_root=self.root,
            corpus_id=str(self.corpus.pk),
            vector_store=self.vector_store,
        ).build(provider=FakeEmbeddingProvider())
        RagIndex.objects.create(
            corpus=self.corpus,
            status=RagIndexStatus.READY,
            chunk_manifest_sha256=result.chunk_manifest_sha256,
            embedding_model=result.embedding_model,
            vector_dimension=result.vector_dimension,
            chunk_count=result.chunk_count,
            vector_count=result.vector_count,
            artifact_path=result.artifact_path,
        )

    def test_agent_executes_grounded_hybrid_rag_and_persists_citations(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RAG,
            query="governance",
            language="en",
            max_results=3,
            idempotency_key="grounded-rag-run",
        )
        with self.settings(DATA_ROOT=self.root), patch(
            "apps.agent.tools.OpenAICompatibleEmbeddingProvider.from_settings",
            return_value=FakeEmbeddingProvider(),
        ), patch(
            "apps.rag.retrieval.MilvusVectorStore.from_settings",
            return_value=self.vector_store,
        ):
            outcome = execute_agent_run(str(run.pk))

        run.refresh_from_db()
        self.assertEqual(outcome["status"], AgentRunStatus.SUCCEEDED)
        self.assertEqual(run.status, AgentRunStatus.SUCCEEDED)
        self.assertEqual(run.skill, "grounded_hybrid_rag@v1")
        self.assertEqual(run.evidence[0]["citation_id"], "rag:paragraph:1:1")
        self.assertIn("rag:paragraph:1:1", run.answer)
