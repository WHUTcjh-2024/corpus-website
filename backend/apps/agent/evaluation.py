from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .llm import summarize_grounded_evidence
from .policy import AgentPolicyError, plan_run
from apps.corpora.models import CorpusLanguage, CorpusType


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    case_id: str
    passed: bool
    latency_ms: float
    checks: dict[str, bool]
    fingerprint: str
    error_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "case_id": self.case_id,
            "outcome": "passed" if self.passed else "failed",
            "latency_ms": round(self.latency_ms, 3),
            "checks": self.checks,
            "input_fingerprint": self.fingerprint,
        }
        if self.error_type:
            payload["error_type"] = self.error_type
        return payload


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        raise ValueError("At least one Agent evaluation case is required.")
    results = [_evaluate_case(case) for case in cases]
    passed = sum(result.passed for result in results)
    all_checks = [value for result in results for value in result.checks.values()]
    latency = sorted(result.latency_ms for result in results)
    return {
        "summary": {
            "cases": len(results),
            "passed": passed,
            "pass_rate": round(passed / len(results), 4),
            "policy_check_rate": round(sum(all_checks) / len(all_checks), 4) if all_checks else 0.0,
            "p95_latency_ms": round(_percentile(latency, 0.95), 3),
        },
        "cases": [result.to_dict() for result in results],
    }


def _evaluate_case(case: dict[str, Any]) -> EvaluationCaseResult:
    started = time.perf_counter()
    case_id = str(case.get("id", "invalid-case"))
    fingerprint = hashlib.sha256(
        json.dumps(case, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    try:
        kind = str(case["kind"])
        if kind == "policy":
            checks = _policy_case(case)
        elif kind == "grounding":
            checks = _grounding_case(case)
        else:
            raise ValueError(f"Unsupported Agent evaluation kind: {kind}")
        return EvaluationCaseResult(
            case_id=case_id,
            passed=all(checks.values()),
            latency_ms=(time.perf_counter() - started) * 1000,
            checks=checks,
            fingerprint=fingerprint,
        )
    except Exception as exc:
        return EvaluationCaseResult(
            case_id=case_id,
            passed=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            checks={"execution": False},
            fingerprint=fingerprint,
            error_type=type(exc).__name__,
        )


def _policy_case(case: dict[str, Any]) -> dict[str, bool]:
    corpus = _corpus_stub(case["corpus"])
    expected = dict(case["expected"])
    try:
        plan = plan_run(
            corpus=corpus,
            mode=str(case["mode"]),
            query=str(case.get("query", "")),
            language=case.get("language"),
            max_results=int(case.get("max_results", 5)),
        )
    except AgentPolicyError:
        return {"rejected": bool(expected.get("reject", False))}
    tools = [step["tool"] for step in plan["steps"]]
    return {
        "not_rejected": not bool(expected.get("reject", False)),
        "skill": plan["skill"] == expected["skill"],
        "tools": tools == expected["tools"],
        "no_direct_write": "create_export" not in tools,
    }


def _grounding_case(case: dict[str, Any]) -> dict[str, bool]:
    evidence = list(case["evidence"])
    result = summarize_grounded_evidence(mode=str(case["mode"]), evidence=evidence)
    citations = {str(item["citation_id"]) for item in evidence}
    return {
        "fallback_expected": bool(result.usage.get("fallback", False)),
        "all_citations_present": all(citation in result.answer for citation in citations),
        "no_model_cost_on_fallback": result.estimated_cost_usd == 0.0,
    }


def _corpus_stub(payload: dict[str, Any]):
    normalized = dict(payload)
    normalized["corpus_type"] = CorpusType(normalized["corpus_type"])
    normalized["language"] = CorpusLanguage(normalized["language"])
    return type("CorpusStub", (), normalized)()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]
