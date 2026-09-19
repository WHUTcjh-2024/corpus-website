from __future__ import annotations

from pathlib import Path


from .contracts import (
    FileView,
    FileViewSegment,
    KwicHit,
    KwicIndexCorrupt,
    KwicIndexUnavailable,
    KwicMatch,
    KwicPage,
    KwicQueryError,
)
from .index_access import KwicIndexAccessMixin
from .query import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_PAGE_SIZE,
    MAX_CONTEXT_SIZE,
    MAX_PAGE_SIZE,
    MAX_QUERY_TERMS,
    SORT_FIELDS,
    _sort_clauses,
    compile_query,
    normalize_sort_keys,
    normalize_sort_order,
    query_terms,
    validate_full_regex,
)
from .search_operations import KwicSearchOperationsMixin


__all__ = [
    "DEFAULT_CONTEXT_SIZE",
    "DEFAULT_PAGE_SIZE",
    "MAX_QUERY_TERMS",
    "MAX_CONTEXT_SIZE",
    "MAX_PAGE_SIZE",
    "SORT_FIELDS",
    "FileView",
    "FileViewSegment",
    "KwicHit",
    "KwicIndexCorrupt",
    "KwicIndexUnavailable",
    "KwicMatch",
    "KwicPage",
    "KwicQueryError",
    "KwicSearchEngine",
    "compile_query",
    "normalize_sort_keys",
    "normalize_sort_order",
    "query_terms",
    "validate_full_regex",
    "_sort_clauses",
]


class KwicSearchEngine(KwicSearchOperationsMixin, KwicIndexAccessMixin):
    """AntConc-style token concordancer backed by the immutable SQLite index."""

    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = str(corpus_id)
        self.index_dir = self.data_root / "indexes" / self.corpus_id
        self.processed_dir = self.data_root / "processed" / self.corpus_id
        self.index_path = self.index_dir / "kwic_index.sqlite"
