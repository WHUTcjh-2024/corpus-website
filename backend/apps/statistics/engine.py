from __future__ import annotations

import sqlite3
from pathlib import Path


from .context_analysis import ContextAnalysisMixin
from .contracts import (
    ClusterPage,
    ClusterRow,
    CollocatePage,
    CollocateRow,
    ConcordancePlot,
    FrequencyPage,
    FrequencyRow,
    KeywordPage,
    KeywordRow,
    NgramPage,
    NgramRow,
    PlotCell,
    PlotDocument,
    StatisticsIndexCorrupt,
    StatisticsIndexUnavailable,
    WordcloudResult,
)
from .frequency_analysis import FrequencyAnalysisMixin


__all__ = [
    "ClusterPage",
    "ClusterRow",
    "CollocatePage",
    "CollocateRow",
    "ConcordancePlot",
    "FrequencyPage",
    "FrequencyRow",
    "KeywordPage",
    "KeywordRow",
    "NgramPage",
    "NgramRow",
    "PlotCell",
    "PlotDocument",
    "StatisticsEngine",
    "StatisticsIndexCorrupt",
    "StatisticsIndexUnavailable",
    "WordcloudResult",
]


class StatisticsEngine(FrequencyAnalysisMixin, ContextAnalysisMixin):
    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = str(corpus_id)
        self.index_path = (
            self.data_root / "indexes" / self.corpus_id / "kwic_index.sqlite"
        )
        self.processed_dir = self.data_root / "processed" / self.corpus_id

    def _connect(self, *, required_tables: tuple[str, ...]) -> sqlite3.Connection:
        if not self.index_path.is_file():
            raise StatisticsIndexUnavailable("统计索引不存在。")
        try:
            connection = sqlite3.connect(
                f"file:{self.index_path.as_posix()}?mode=ro", uri=True
            )
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = set(required_tables) - existing
            if missing:
                connection.close()
                raise StatisticsIndexUnavailable("统计索引结构不兼容。")
            return connection
        except StatisticsIndexUnavailable:
            raise
        except sqlite3.Error as exc:
            raise StatisticsIndexUnavailable("无法打开统计索引。") from exc
