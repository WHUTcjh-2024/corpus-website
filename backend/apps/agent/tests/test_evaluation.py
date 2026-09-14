from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import CommandError, OutputWrapper
from django.test import SimpleTestCase

from apps.agent.evaluation import EvaluationCaseResult, _percentile, evaluate_cases
from apps.agent.management.commands.evaluate_corpus_agent import Command
from apps.agent.policy import AgentPolicyError


class AgentEvaluationTests(SimpleTestCase):
    def test_requires_cases_and_computes_summary(self) -> None:
        with self.assertRaisesMessage(ValueError, "At least one"):
            evaluate_cases([])

        cases = [
            {
                "id": "policy-pass",
                "kind": "policy",
                "corpus": {"corpus_type": "raw_en", "language": "en"},
                "mode": "search",
                "query": "farmer",
                "expected": {"skill": "kwic", "tools": ["kwic_search"]},
            },
            {"id": "unsupported", "kind": "unknown"},
        ]
        plan = {"skill": "kwic", "steps": [{"tool": "kwic_search"}]}
        with patch("apps.agent.evaluation.plan_run", return_value=plan):
            report = evaluate_cases(cases)

        self.assertEqual(report["summary"]["cases"], 2)
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["pass_rate"], 0.5)
        self.assertEqual(report["cases"][1]["error_type"], "ValueError")
        self.assertEqual(len(report["cases"][0]["input_fingerprint"]), 16)

    def test_policy_case_records_expected_rejection_and_wrong_plan(self) -> None:
        rejected_case = {
            "id": "rejected",
            "kind": "policy",
            "corpus": {"corpus_type": "raw_zh", "language": "zh"},
            "mode": "search",
            "expected": {"reject": True},
        }
        with patch(
            "apps.agent.evaluation.plan_run",
            side_effect=AgentPolicyError("denied"),
        ):
            report = evaluate_cases([rejected_case])
        self.assertEqual(report["summary"]["pass_rate"], 1.0)

        wrong_case = {
            **rejected_case,
            "id": "wrong-plan",
            "expected": {"reject": False, "skill": "kwic", "tools": ["kwic_search"]},
        }
        plan = {"skill": "other", "steps": [{"tool": "create_export"}]}
        with patch("apps.agent.evaluation.plan_run", return_value=plan):
            report = evaluate_cases([wrong_case])
        self.assertEqual(report["summary"]["pass_rate"], 0.0)
        self.assertFalse(report["cases"][0]["checks"]["no_direct_write"])

    def test_grounding_case_checks_citations_fallback_and_cost(self) -> None:
        case = {
            "id": "grounding",
            "kind": "grounding",
            "mode": "summary",
            "evidence": [{"citation_id": "C1"}, {"citation_id": "C2"}],
        }
        result = SimpleNamespace(
            usage={"fallback": True},
            answer="Evidence [C1] and [C2]",
            estimated_cost_usd=0.0,
        )
        with patch(
            "apps.agent.evaluation.summarize_grounded_evidence", return_value=result
        ):
            report = evaluate_cases([case])
        self.assertEqual(report["summary"]["policy_check_rate"], 1.0)

    def test_result_serialization_and_percentile_edges(self) -> None:
        result = EvaluationCaseResult(
            case_id="case",
            passed=False,
            latency_ms=1.23456,
            checks={},
            fingerprint="fingerprint",
            error_type="ValueError",
        )
        self.assertEqual(result.to_dict()["latency_ms"], 1.235)
        self.assertEqual(result.to_dict()["error_type"], "ValueError")
        self.assertEqual(_percentile([], 0.95), 0.0)
        self.assertEqual(_percentile([1.0, 2.0, 3.0], 0.95), 2.0)


class AgentEvaluationCommandTests(SimpleTestCase):
    def command(self) -> Command:
        command = Command()
        command.stdout = OutputWrapper(StringIO())
        return command

    def test_rejects_invalid_gate_missing_file_and_non_array_json(self) -> None:
        with self.assertRaisesMessage(CommandError, "between 0 and 1"):
            self.command().handle(cases="missing.json", report=None, min_pass_rate=1.1)
        with self.assertRaisesMessage(CommandError, "Unable to read"):
            self.command().handle(cases="missing.json", report=None, min_pass_rate=1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = Path(temp_dir) / "cases.json"
            case_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "JSON array"):
                self.command().handle(
                    cases=str(case_path), report=None, min_pass_rate=1.0
                )

    def test_writes_report_and_enforces_pass_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = Path(temp_dir) / "cases.json"
            report_path = Path(temp_dir) / "reports" / "result.json"
            case_path.write_text(json.dumps([{"id": "case"}]), encoding="utf-8")
            report = {"summary": {"pass_rate": 1.0}, "cases": []}
            with patch(
                "apps.agent.management.commands.evaluate_corpus_agent.evaluate_cases",
                return_value=report,
            ):
                command = self.command()
                command.handle(
                    cases=str(case_path),
                    report=str(report_path),
                    min_pass_rate=1.0,
                )
            self.assertEqual(
                json.loads(report_path.read_text())["summary"]["pass_rate"], 1.0
            )
            self.assertIn('"pass_rate": 1.0', command.stdout._out.getvalue())

            report["summary"]["pass_rate"] = 0.5
            with patch(
                "apps.agent.management.commands.evaluate_corpus_agent.evaluate_cases",
                return_value=report,
            ):
                with self.assertRaisesMessage(CommandError, "gate failed"):
                    self.command().handle(
                        cases=str(case_path),
                        report=None,
                        min_pass_rate=1.0,
                    )
