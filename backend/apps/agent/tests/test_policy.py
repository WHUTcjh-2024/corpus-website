from django.test import SimpleTestCase

from apps.agent.models import AgentRunMode
from apps.agent.policy import AgentPolicyError, plan_run, skill_from_plan
from apps.corpora.models import CorpusLanguage, CorpusType


class CorpusStub:
    def __init__(self, *, corpus_type, language):
        self.corpus_type = corpus_type
        self.language = language


class AgentPolicyTests(SimpleTestCase):
    def test_retrieval_plan_uses_only_the_parallel_search_tool(self):
        plan = plan_run(
            corpus=CorpusStub(corpus_type=CorpusType.ALIGNED_TSV, language=CorpusLanguage.ZH_EN),
            mode=AgentRunMode.RETRIEVE,
            query="reform",
            language="en",
            max_results=3,
        )

        self.assertEqual(plan["skill"], "corpus_retrieval@v1")
        self.assertEqual(plan["steps"][0]["tool"], "search_parallel")
        self.assertEqual(plan["steps"][0]["input"]["search_side"], "en")
        self.assertEqual(skill_from_plan(plan).allowed_tools, frozenset({"search_kwic", "search_parallel"}))

    def test_quality_review_rejects_mono_corpus(self):
        with self.assertRaises(AgentPolicyError):
            plan_run(
                corpus=CorpusStub(corpus_type=CorpusType.RAW_ZH, language=CorpusLanguage.ZH),
                mode=AgentRunMode.QUALITY_REVIEW,
                query="",
                language=None,
                max_results=5,
            )

    def test_rag_plan_uses_only_the_grounded_hybrid_retrieval_tool(self):
        plan = plan_run(
            corpus=CorpusStub(corpus_type=CorpusType.RAW_EN, language=CorpusLanguage.EN),
            mode=AgentRunMode.RAG,
            query="reliable task delivery",
            language="en",
            max_results=4,
        )

        self.assertEqual(plan["skill"], "grounded_hybrid_rag@v1")
        self.assertEqual(plan["steps"][0]["tool"], "search_rag")
        self.assertEqual(plan["steps"][0]["input"]["language"], "en")
        self.assertEqual(skill_from_plan(plan).allowed_tools, frozenset({"search_rag"}))

    def test_export_plan_is_an_explicit_handoff_not_a_write_tool(self):
        plan = plan_run(
            corpus=CorpusStub(corpus_type=CorpusType.RAW_EN, language=CorpusLanguage.EN),
            mode=AgentRunMode.EXPORT,
            query="policy",
            language="en",
            max_results=5,
        )

        self.assertEqual(plan["skill"], "corpus_export_handoff@v1")
        self.assertEqual([step["tool"] for step in plan["steps"]], ["search_kwic", "prepare_export"])
        self.assertEqual(plan["steps"][1]["input"]["kind"], "kwic")
