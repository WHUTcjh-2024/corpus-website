from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from math import ceil
from typing import Callable

from .retrieval import RagSearchResult


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    case_id: str
    observed_citation_ids: tuple[str, ...]
    expected_citation_ids: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float
    passed: bool
    latency_ms: float
    error_type: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_retrieval_cases(
    *,
    cases: list[dict],
    retrieve: Callable[[str, str | None, int], RagSearchResult],
    top_k: int,
) -> dict:
    """Score a fixed relevance set without asking an LLM to judge itself."""

    if not cases:
        raise ValueError("At least one RAG evaluation case is required.")
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10.")
    results = [_evaluate_case(case=case, retrieve=retrieve, top_k=top_k) for case in cases]
    return {
        "summary": {
            "cases": len(results),
            "passed": sum(result.passed for result in results),
            "pass_rate": round(sum(result.passed for result in results) / len(results), 4),
            "recall_at_k": round(sum(result.recall_at_k for result in results) / len(results), 4),
            "mrr": round(sum(result.reciprocal_rank for result in results) / len(results), 4),
            "hit_rate": round(
                sum(result.reciprocal_rank > 0 for result in results) / len(results), 4
            ),
            "p95_latency_ms": round(_percentile(sorted(result.latency_ms for result in results), 0.95), 3),
        },
        "cases": [result.to_dict() for result in results],
    }


def load_evaluation_cases(path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The RAG evaluation case file is invalid.") from exc
    if not isinstance(payload, list) or not all(isinstance(case, dict) for case in payload):
        raise ValueError("The RAG evaluation case file must be a JSON array of objects.")
    return payload


def _evaluate_case(
    *,
    case: dict,
    retrieve: Callable[[str, str | None, int], RagSearchResult],
    top_k: int,
) -> RetrievalCaseResult:
    started = time.perf_counter()
    case_id = str(case.get("id", "invalid-case"))
    expected = _expected_citations(case)
    query = " ".join(str(case.get("query", "")).split())
    language = case.get("language")
    if not query or language not in {None, "", "zh", "en"}:
        return _failed_result(case_id, expected, started, "ValueError")
    try:
        result = retrieve(query, language or None, top_k)
    except Exception as exc:
        return _failed_result(case_id, expected, started, type(exc).__name__)
    observed = tuple(hit.citation_id for hit in result.hits[:top_k])
    relevant = set(expected)
    matched = relevant.intersection(observed)
    first_rank = next(
        (rank for rank, citation_id in enumerate(observed, start=1) if citation_id in relevant),
        None,
    )
    return RetrievalCaseResult(
        case_id=case_id,
        observed_citation_ids=observed,
        expected_citation_ids=expected,
        recall_at_k=len(matched) / len(relevant),
        reciprocal_rank=1 / first_rank if first_rank else 0.0,
        passed=bool(matched),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def _expected_citations(case: dict) -> tuple[str, ...]:
    values = case.get("expected_citation_ids")
    if not isinstance(values, list) or not values:
        raise ValueError("A RAG evaluation case must include expected_citation_ids.")
    citations = tuple(str(value) for value in values)
    if any(not citation.startswith("rag:") for citation in citations):
        raise ValueError("Expected citations must use stable rag: identifiers.")
    return citations


def _failed_result(
    case_id: str,
    expected: tuple[str, ...],
    started: float,
    error_type: str,
) -> RetrievalCaseResult:
    return RetrievalCaseResult(
        case_id=case_id,
        observed_citation_ids=(),
        expected_citation_ids=expected,
        recall_at_k=0.0,
        reciprocal_rank=0.0,
        passed=False,
        latency_ms=(time.perf_counter() - started) * 1000,
        error_type=error_type,
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    # Nearest-rank percentile: a two-case evaluation's p95 must not silently
    # report the faster case as its tail latency.
    rank = max(1, ceil(len(values) * fraction))
    return values[min(len(values) - 1, rank - 1)]
