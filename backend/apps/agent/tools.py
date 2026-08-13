from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from django.conf import settings
from django.core.exceptions import PermissionDenied

from apps.audits.models import ParallelAudit, ParallelAuditStatus
from apps.corpora.models import Corpus, CorpusLanguage
from apps.corpora.services import visible_corpora_for
from apps.exports.services import create_export_job
from apps.parallel.contracts import ParallelIndexCorrupt, ParallelIndexUnavailable
from apps.parallel.engine import ParallelQuery, ParallelSearchEngine
from apps.search.contracts import KwicIndexCorrupt, KwicIndexUnavailable, KwicQueryError
from apps.search.kwic import KwicSearchEngine

from .policy import AgentPolicyError, ensure_tool_allowed


class AgentToolError(RuntimeError):
    code = "TOOL_EXECUTION_FAILED"


class AgentToolUnavailable(AgentToolError):
    code = "TOOL_UNAVAILABLE"


class AgentToolInputError(AgentToolError):
    code = "INVALID_TOOL_ARGUMENT"


@dataclass(frozen=True, slots=True)
class ToolContext:
    user: Any
    corpus: Corpus
    skill: Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    output: dict[str, Any]
    evidence: list[dict[str, Any]]


class CorpusToolRegistry:
    """The sole execution path for Agent tool calls.

    Tools are registered in code, validated against the stored Skill, and are
    called with the authenticated user and already-authorized corpus.  This
    intentionally prevents model text from becoming an executable capability.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[[ToolContext, dict[str, Any]], ToolResult]] = {
            "search_kwic": self._search_kwic,
            "search_parallel": self._search_parallel,
            "get_latest_quality_report": self._get_latest_quality_report,
            "prepare_export": self._prepare_export,
        }

    def execute(self, *, context: ToolContext, tool_name: str, input: dict[str, Any]) -> ToolResult:
        ensure_tool_allowed(skill=context.skill, tool_name=tool_name)
        tool = self._tools.get(tool_name)
        if tool is None:
            raise AgentPolicyError(f"Tool {tool_name!r} is not registered.")
        if not isinstance(input, dict):
            raise AgentToolInputError("Tool input must be an object.")
        self._require_visible(context.user, context.corpus)
        return tool(context, input)

    @staticmethod
    def _require_visible(user, corpus: Corpus) -> None:
        if not visible_corpora_for(user).filter(pk=corpus.pk).exists():
            raise PermissionDenied("The user is not allowed to access this corpus.")

    def _search_kwic(self, context: ToolContext, input: dict[str, Any]) -> ToolResult:
        query = _query(input)
        language = str(input.get("language", "") or "")
        if language and language not in {CorpusLanguage.ZH, CorpusLanguage.EN}:
            raise AgentToolInputError("language must be zh or en.")
        max_results = _max_results(input)
        try:
            result = KwicSearchEngine(
                data_root=settings.DATA_ROOT, corpus_id=str(context.corpus.pk)
            ).search(
                query,
                language=language or None,
                context_size=5,
                page=1,
                page_size=max_results,
                whole_words=True,
            )
        except (KwicIndexUnavailable, KwicIndexCorrupt) as exc:
            raise AgentToolUnavailable("The KWIC index is unavailable.") from exc
        except KwicQueryError as exc:
            raise AgentToolInputError(str(exc)) from exc

        hits = [
            {
                "citation_id": f"kwic:{hit.row_id}",
                "row_id": hit.row_id,
                "document_id": hit.document_id,
                "source_filename": hit.source_filename,
                "language": hit.language,
                "left": _clip(hit.left),
                "keyword": _clip(hit.keyword),
                "right": _clip(hit.right),
            }
            for hit in result.hits
        ]
        return ToolResult(
            output={"query": query, "total": result.total, "hits": hits},
            evidence=hits,
        )

    def _search_parallel(self, context: ToolContext, input: dict[str, Any]) -> ToolResult:
        query = _query(input)
        search_side = str(input.get("search_side", "zh"))
        alignment_unit = str(input.get("alignment_unit", "sentence"))
        max_results = _max_results(input)
        try:
            result = ParallelSearchEngine(
                data_root=settings.DATA_ROOT, corpus_id=str(context.corpus.pk)
            ).search(
                ParallelQuery(
                    q=query,
                    search_side=search_side,
                    alignment_unit=alignment_unit,
                    context_size=20,
                ),
                page=1,
                page_size=max_results,
            )
        except (ParallelIndexUnavailable, ParallelIndexCorrupt) as exc:
            raise AgentToolUnavailable("The parallel corpus index is unavailable.") from exc
        except ValueError as exc:
            raise AgentToolInputError(str(exc)) from exc

        hits = [
            {
                "citation_id": f"parallel:{hit.global_position}:{hit.occurrence_ordinal}",
                "global_position": hit.global_position,
                "pair_id": hit.pair_id,
                "zh_filename": hit.zh_filename,
                "en_filename": hit.en_filename,
                "alignment_unit": hit.alignment_unit,
                "alignment_method": hit.method,
                "confidence": hit.confidence,
                "zh_text": _clip(hit.zh_text),
                "en_text": _clip(hit.en_text),
            }
            for hit in result.hits
        ]
        return ToolResult(
            output={"query": query, "total": result.total, "hits": hits},
            evidence=hits,
        )

    def _get_latest_quality_report(self, context: ToolContext, input: dict[str, Any]) -> ToolResult:
        if input:
            raise AgentToolInputError("get_latest_quality_report does not accept arguments.")
        audit = (
            ParallelAudit.objects.filter(
                corpus=context.corpus, status=ParallelAuditStatus.SUCCESS
            )
            .order_by("-finished_at", "-created_at")
            .first()
        )
        if audit is None or not audit.report_path:
            raise AgentToolUnavailable("No completed parallel quality report is available.")
        report_path = _validated_audit_path(audit.report_path, context.corpus.pk)
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentToolUnavailable("The quality report cannot be read safely.") from exc
        summary = report.get("summary")
        if not isinstance(summary, dict):
            raise AgentToolUnavailable("The quality report has an invalid summary.")
        evidence = [{
            "citation_id": f"audit:{audit.pk}",
            "audit_id": str(audit.pk),
            "finished_at": audit.finished_at.isoformat() if audit.finished_at else None,
            "summary": _bounded_mapping(summary),
        }]
        return ToolResult(output={"audit_id": str(audit.pk), "summary": _bounded_mapping(summary)}, evidence=evidence)

    def _prepare_export(self, context: ToolContext, input: dict[str, Any]) -> ToolResult:
        kind = str(input.get("kind", ""))
        parameters = input.get("parameters")
        if kind not in {"kwic", "parallel"} or not isinstance(parameters, dict):
            raise AgentToolInputError("prepare_export requires a supported kind and object parameters.")
        return ToolResult(
            output={"kind": kind, "parameters": {str(k): str(v)[:200] for k, v in parameters.items()}},
            evidence=[],
        )


def commit_export(*, user, corpus: Corpus, payload: dict[str, Any], request=None):
    """Execute the only write-capable Agent action after explicit approval."""
    kind = str(payload.get("kind", ""))
    parameters = payload.get("parameters")
    if kind not in {"kwic", "parallel"} or not isinstance(parameters, dict):
        raise AgentToolInputError("Approved export payload is invalid.")
    return create_export_job(
        user=user,
        corpus=corpus,
        kind=kind,
        parameters=parameters,
        request=request,
    )


def _query(input: dict[str, Any]) -> str:
    value = " ".join(str(input.get("query", "")).split())
    if not value or len(value) > 200:
        raise AgentToolInputError("query must contain 1 to 200 characters.")
    return value


def _max_results(input: dict[str, Any]) -> int:
    try:
        value = int(input.get("max_results", 5))
    except (TypeError, ValueError) as exc:
        raise AgentToolInputError("max_results must be an integer.") from exc
    if not 1 <= value <= 10:
        raise AgentToolInputError("max_results must be between 1 and 10.")
    return value


def _validated_audit_path(value: str, corpus_id) -> Path:
    root = (settings.DATA_ROOT / "processed" / str(corpus_id)).resolve()
    path = Path(value).resolve()
    if path == root or not path.is_relative_to(root) or path.suffix != ".json":
        raise AgentToolUnavailable("The quality report path is invalid.")
    return path


def _clip(value: str, limit: int = 500) -> str:
    return " ".join(str(value).split())[:limit]


def _bounded_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key)[:100]: item for key, item in list(value.items())[:50]}
