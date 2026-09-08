from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterator, Sequence

from ..contracts import ImportResult, SourceFile


class BaseImporter(ABC):
    name = "base"

    @abstractmethod
    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        raise NotImplementedError


def iter_language_pairs(sources: Sequence[SourceFile]) -> Iterator[tuple[SourceFile, SourceFile]]:
    """Yield deterministic zh/en pairs for legacy or manifest-backed corpora."""

    if len(sources) == 2 and not any(source.pair_id for source in sources):
        yield _validated_pair(sources, pair_id="legacy")
        return

    missing_pair_ids = [source.filename for source in sources if not source.pair_id]
    if missing_pair_ids:
        raise ValueError(
            "批量双语语料中的文件缺少 pair_id：" + ", ".join(missing_pair_ids[:5])
        )

    grouped: dict[str, list[SourceFile]] = defaultdict(list)
    for source in sources:
        grouped[source.pair_id].append(source)
    for pair_id in sorted(grouped):
        yield _validated_pair(grouped[pair_id], pair_id=pair_id)


def _validated_pair(
    sources: Sequence[SourceFile],
    *,
    pair_id: str,
) -> tuple[SourceFile, SourceFile]:
    zh_sources = [source for source in sources if source.language == "zh"]
    en_sources = [source for source in sources if source.language == "en"]
    if len(sources) != 2 or len(zh_sources) != 1 or len(en_sources) != 1:
        raise ValueError(f"双语配对 {pair_id} 必须恰好包含一个中文文件和一个英文文件。")
    return zh_sources[0], en_sources[0]
