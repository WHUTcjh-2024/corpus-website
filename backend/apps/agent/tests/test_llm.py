from unittest.mock import patch

from django.test import SimpleTestCase

from apps.agent.llm import summarize_grounded_evidence


class GroundedSummaryTests(SimpleTestCase):
    def test_valid_model_summary_records_usage_and_cost_without_fallback(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": '{"answer":"The policy requires approval.","citation_ids":["rag:1"]}'
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25},
        }
        with self.settings(
            AGENT_MODEL_ENABLED=True,
            AGENT_MODEL_BASE_URL="https://model.example/v1",
            AGENT_MODEL_API_KEY="test-key",
            AGENT_MODEL_NAME="test-chat-model",
            AGENT_MODEL_INPUT_USD_PER_1M=2.0,
            AGENT_MODEL_OUTPUT_USD_PER_1M=8.0,
        ), patch("apps.agent.llm._invoke", return_value=response):
            result = summarize_grounded_evidence(
                mode="rag",
                evidence=[{"citation_id": "rag:1", "text": "Policy approval evidence."}],
            )

        self.assertFalse(result.usage["fallback"])
        self.assertEqual(result.estimated_cost_usd, 0.0004)
        self.assertIn("rag:1", result.answer)
