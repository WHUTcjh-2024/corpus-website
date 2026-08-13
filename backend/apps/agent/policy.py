from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.corpora.models import Corpus, CorpusType

from .models import AgentRunMode


class AgentPolicyError(ValueError):
    """Raised when a request or a tool call crosses the Agent trust boundary."""


PARALLEL_CORPUS_TYPES = frozenset(
    {
        CorpusType.ALIGNED_TSV,
        CorpusType.PAIRED_RAW_ZH_EN,
        CorpusType.PAIRED_TAGGED_ZH_EN,
    }
)


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    version: str
    allowed_tools: frozenset[str]

    @property
    def identifier(self) -> str:
        return f"{self.name}@{self.version}"


SKILLS = {
    "corpus_retrieval@v1": SkillDefinition(
        name="corpus_retrieval",
        version="v1",
        allowed_tools=frozenset({"search_kwic", "search_parallel"}),
    ),
    "parallel_quality_review@v2": SkillDefinition(
        name="parallel_quality_review",
        version="v2",
        allowed_tools=frozenset({
            "request_quality_audit",
            "get_latest_quality_report",
            "search_parallel",
        }),
    ),
    # Read compatibility for plans persisted before the resumable Saga rollout.
    # New requests always use v2; old runs remain replayable during deployment.
    "parallel_quality_review@v1": SkillDefinition(
        name="parallel_quality_review",
        version="v1",
        allowed_tools=frozenset({"get_latest_quality_report", "search_parallel"}),
    ),
    "parallel_quality_audit@v1": SkillDefinition(
        name="parallel_quality_audit",
        version="v1",
        allowed_tools=frozenset({"request_quality_audit"}),
    ),
    "corpus_export_handoff@v1": SkillDefinition(
        name="corpus_export_handoff",
        version="v1",
        allowed_tools=frozenset({"search_kwic", "search_parallel", "prepare_export"}),
    ),
}


def is_parallel_corpus(corpus: Corpus) -> bool:
    return corpus.corpus_type in PARALLEL_CORPUS_TYPES


def plan_run(
    *,
    corpus: Corpus,
    mode: str,
    query: str,
    language: str | None,
    max_results: int,
) -> dict[str, Any]:
    """Build the only executable plan shape accepted by the runtime.

    Tool selection is deterministic on purpose: corpus text never reaches this
    planner, so a malicious document cannot steer the Agent into another tool.
    An optional LLM may summarize already-authorized evidence later, but it is
    never an authorization component.
    """

    if mode not in AgentRunMode.values:
        raise AgentPolicyError("Unsupported Agent mode.")
    if not 1 <= max_results <= 10:
        raise AgentPolicyError("max_results must be between 1 and 10.")
    query = " ".join(query.split())

    parallel = is_parallel_corpus(corpus)
    if mode in {AgentRunMode.RETRIEVE, AgentRunMode.EXPORT} and not query:
        raise AgentPolicyError("A retrieval or export request requires a query.")
    if language and language not in {"zh", "en"}:
        raise AgentPolicyError("language must be zh or en.")

    retrieval_tool = "search_parallel" if parallel else "search_kwic"
    retrieval_input: dict[str, Any] = {"query": query, "max_results": max_results}
    if parallel:
        retrieval_input.update(
            {
                "search_side": language or ("zh" if corpus.language != "en" else "en"),
                "alignment_unit": _default_alignment_unit(corpus),
            }
        )
    elif language:
        retrieval_input["language"] = language

    steps: list[dict[str, Any]]
    if mode == AgentRunMode.RETRIEVE:
        skill = SKILLS["corpus_retrieval@v1"]
        steps = [{"node": "retrieve", "tool": retrieval_tool, "input": retrieval_input}]
    elif mode == AgentRunMode.QUALITY_REVIEW:
        if not parallel:
            raise AgentPolicyError("Quality review is available only for processed parallel corpora.")
        skill = SKILLS["parallel_quality_review@v2"]
        # This is deliberately a fixed two-stage Saga.  The first step either
        # reuses a completed report or creates/joins a durable audit.  In the
        # latter case the runtime pauses until the worker result resumes it.
        steps = [
            {"node": "ensure_quality_audit", "tool": "request_quality_audit", "input": {}},
            {"node": "load_quality_report", "tool": "get_latest_quality_report", "input": {}},
        ]
        if query:
            steps.append({"node": "inspect_evidence", "tool": "search_parallel", "input": retrieval_input})
    else:
        skill = SKILLS["corpus_export_handoff@v1"]
        steps = [
            {"node": "retrieve_preview", "tool": retrieval_tool, "input": retrieval_input},
            {
                "node": "prepare_export",
                "tool": "prepare_export",
                "input": {
                    "kind": "parallel" if parallel else "kwic",
                    "parameters": _export_parameters(
                        query=query,
                        language=language,
                        parallel=parallel,
                        corpus=corpus,
                    ),
                },
            },
        ]

    return {
        "skill": skill.identifier,
        "version": 1,
        "steps": steps,
        "planner": "deterministic-policy-v1",
    }


def skill_from_plan(plan: dict[str, Any]) -> SkillDefinition:
    identifier = str(plan.get("skill", ""))
    try:
        return SKILLS[identifier]
    except KeyError as exc:
        raise AgentPolicyError("The stored Agent skill is not registered.") from exc


def ensure_tool_allowed(*, skill: SkillDefinition, tool_name: str) -> None:
    if tool_name not in skill.allowed_tools:
        raise AgentPolicyError(
            f"Tool {tool_name!r} is not permitted by skill {skill.identifier!r}."
        )


def _default_alignment_unit(corpus: Corpus) -> str:
    return "paragraph" if corpus.corpus_type == CorpusType.PAIRED_RAW_ZH_EN else "sentence"


def _export_parameters(
    *, query: str, language: str | None, parallel: bool, corpus: Corpus
) -> dict[str, str]:
    if parallel:
        return {
            "q": query,
            "search_side": language or ("zh" if corpus.language != "en" else "en"),
            "alignment_unit": _default_alignment_unit(corpus),
            "whole_words": "1",
            "page": "1",
            "page_size": "100",
        }
    return {
        "q": query,
        "query_mode": "simple",
        "language": language or "",
        "context": "5",
        "whole_words": "1",
        "case_sensitive": "0",
        "regex": "0",
        "page": "1",
        "page_size": "100",
    }
