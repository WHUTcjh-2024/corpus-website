from django.test import SimpleTestCase

from apps.rag.evaluation import evaluate_retrieval_cases
from apps.rag.retrieval import RagHit, RagSearchResult


class RagEvaluationTests(SimpleTestCase):
    def test_computes_recall_and_mrr_from_stable_citations(self):
        result = evaluate_retrieval_cases(
            cases=[
                {
                    "id": "governance",
                    "query": "governance",
                    "expected_citation_ids": ["rag:paragraph:1"],
                },
                {
                    "id": "queue",
                    "query": "queue",
                    "expected_citation_ids": ["rag:paragraph:2"],
                },
            ],
            top_k=2,
            retrieve=lambda query, language, top_k: RagSearchResult(
                query=query,
                total=2,
                embedding_model="test",
                vector_dimension=3,
                hits=(
                    RagHit(
                        citation_id="rag:paragraph:1" if query == "governance" else "rag:other",
                        chunk_id="paragraph:1",
                        text="evidence",
                        language="en",
                        document_id="document",
                        source_filename="source.txt",
                        kind="paragraph",
                        semantic_score=1.0,
                        lexical_score=1.0,
                        fused_score=1.0,
                        metadata={},
                    ),
                    RagHit(
                        citation_id="rag:other" if query == "governance" else "rag:paragraph:2",
                        chunk_id="paragraph:2",
                        text="evidence",
                        language="en",
                        document_id="document",
                        source_filename="source.txt",
                        kind="paragraph",
                        semantic_score=0.5,
                        lexical_score=0.5,
                        fused_score=0.5,
                        metadata={},
                    ),
                ),
            ),
        )

        self.assertEqual(result["summary"]["recall_at_k"], 1.0)
        self.assertEqual(result["summary"]["mrr"], 0.75)
        self.assertEqual(result["summary"]["hit_rate"], 1.0)
