from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ..contracts import ImportResult, ParallelPairRecord, ParagraphRecord, SourceFile, stable_id
from ..exceptions import ProcessingError
from .base import BaseImporter
from .raw_mono import import_raw_source


@dataclass(frozen=True, slots=True)
class _Step:
    zh_count: int
    en_count: int


_STEPS = (_Step(1, 1), _Step(1, 2), _Step(2, 1), _Step(1, 0), _Step(0, 1))
_STEP_PENALTY = {(1, 1): 0.0, (1, 2): 0.35, (2, 1): 0.35, (1, 0): 1.2, (0, 1): 1.2}


class PairedParagraphImporter(BaseImporter):
    """Build conservative candidate paragraph links for a probable file pair.

    A filename/directory match does not prove human alignment.  Links are
    therefore produced by a length-based dynamic program, carry non-perfect
    confidence, and preserve unmatched paragraphs as explicit gaps.
    """

    name = "paired_paragraphs_length_dp"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        zh_sources = [source for source in sources if source.language == "zh"]
        en_sources = [source for source in sources if source.language == "en"]
        if len(zh_sources) != 1 or len(en_sources) != 1:
            raise ProcessingError(
                "PairedParagraphImporter requires exactly one zh file and one en file."
            )

        zh_result = import_raw_source(zh_sources[0])
        en_result = import_raw_source(en_sources[0])
        if not zh_result.paragraphs or not en_result.paragraphs:
            raise ProcessingError("候选双语文件至少有一侧不包含可用段落。")

        alignments = _align_paragraphs(zh_result.paragraphs, en_result.paragraphs)
        result = ImportResult(
            source_file_ids=[zh_sources[0].id, en_sources[0].id],
            documents=[*zh_result.documents, *en_result.documents],
            paragraphs=[*zh_result.paragraphs, *en_result.paragraphs],
            sentences=[*zh_result.sentences, *en_result.sentences],
            tokens=[*zh_result.tokens, *en_result.tokens],
            warnings=[
                "该双语关系由文件名和段落长度自动推断，不代表人工句对齐；请按置信度抽检。"
            ],
        )
        low_confidence = 0
        gap_count = 0
        for ordinal, (zh_items, en_items, confidence) in enumerate(alignments, start=1):
            zh_text = "\n".join(item.text for item in zh_items)
            en_text = "\n".join(item.text for item in en_items)
            if not zh_items or not en_items:
                gap_count += 1
            if confidence < 0.65:
                low_confidence += 1
            method = f"automatic_length_dp_{len(zh_items)}_{len(en_items)}"
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id(
                        "pair",
                        zh_sources[0].id,
                        en_sources[0].id,
                        "paragraph",
                        ordinal,
                    ),
                    ordinal=ordinal,
                    zh_unit_id=zh_items[0].id if zh_items else "",
                    en_unit_id=en_items[0].id if en_items else "",
                    zh_text=zh_text,
                    en_text=en_text,
                    alignment_unit="paragraph",
                    method=method,
                    confidence=confidence,
                )
            )
        if len(zh_result.paragraphs) != len(en_result.paragraphs):
            result.warnings.append(
                "中英文段落数不一致："
                f"zh={len(zh_result.paragraphs)}, en={len(en_result.paragraphs)}。"
            )
        if gap_count:
            result.warnings.append(f"自动对齐保留了 {gap_count} 个单边缺口。")
        if low_confidence:
            result.warnings.append(
                f"{low_confidence}/{len(alignments)} 个候选段对置信度低于 0.65。"
            )
        yield result


def _align_paragraphs(
    zh_paragraphs: Sequence[ParagraphRecord],
    en_paragraphs: Sequence[ParagraphRecord],
) -> list[tuple[list[ParagraphRecord], list[ParagraphRecord], float]]:
    n, m = len(zh_paragraphs), len(en_paragraphs)
    ratio = sum(_text_length(item.text) for item in en_paragraphs) / max(
        sum(_text_length(item.text) for item in zh_paragraphs), 1
    )
    ratio = min(max(ratio, 0.5), 8.0)
    infinity = float("inf")
    costs = [[infinity] * (m + 1) for _ in range(n + 1)]
    previous: list[list[_Step | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    costs[0][0] = 0.0

    for i in range(n + 1):
        for j in range(m + 1):
            if not math.isfinite(costs[i][j]):
                continue
            for step in _STEPS:
                next_i, next_j = i + step.zh_count, j + step.en_count
                if next_i > n or next_j > m:
                    continue
                zh_items = zh_paragraphs[i:next_i]
                en_items = en_paragraphs[j:next_j]
                transition_cost = _alignment_cost(zh_items, en_items, ratio)
                transition_cost += _STEP_PENALTY[(step.zh_count, step.en_count)]
                candidate = costs[i][j] + transition_cost
                if candidate < costs[next_i][next_j]:
                    costs[next_i][next_j] = candidate
                    previous[next_i][next_j] = step

    aligned: list[tuple[list[ParagraphRecord], list[ParagraphRecord], float]] = []
    i, j = n, m
    while i or j:
        step = previous[i][j]
        if step is None:
            raise ProcessingError("无法为候选双语文件建立段落映射。")
        start_i, start_j = i - step.zh_count, j - step.en_count
        zh_items = list(zh_paragraphs[start_i:i])
        en_items = list(en_paragraphs[start_j:j])
        confidence = _alignment_confidence(zh_items, en_items, ratio)
        aligned.append((zh_items, en_items, confidence))
        i, j = start_i, start_j
    aligned.reverse()
    return aligned


def _alignment_cost(
    zh_items: Sequence[ParagraphRecord],
    en_items: Sequence[ParagraphRecord],
    ratio: float,
) -> float:
    if not zh_items or not en_items:
        return 0.0
    zh_length = sum(_text_length(item.text) for item in zh_items)
    en_length = sum(_text_length(item.text) for item in en_items)
    return abs(math.log((en_length + 1) / (zh_length * ratio + 1)))


def _alignment_confidence(
    zh_items: Sequence[ParagraphRecord],
    en_items: Sequence[ParagraphRecord],
    ratio: float,
) -> float:
    if not zh_items or not en_items:
        return 0.0
    ceiling = 0.95 if len(zh_items) == len(en_items) == 1 else 0.82
    return round(ceiling * math.exp(-_alignment_cost(zh_items, en_items, ratio)), 4)


def _text_length(value: str) -> int:
    return sum(1 for character in value if not character.isspace())
