from __future__ import annotations

import math
from dataclasses import dataclass


class KwicSearchError(Exception):
    """Base exception for a user-visible KWIC search failure."""


class KwicIndexUnavailable(KwicSearchError):
    """The corpus has not produced the required search artifacts yet."""


class KwicIndexCorrupt(KwicSearchError):
    """The corpus search artifacts exist but cannot be read safely."""


class KwicQueryError(KwicSearchError, ValueError):
    """The query is invalid or cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class QueryToken:
    value: str
    operator: str
    case_sensitive: bool
    full_match: bool = True


@dataclass(frozen=True, slots=True)
class KwicHit:
    left: str
    keyword: str
    right: str
    source_filename: str
    document_id: str
    sentence_id: str
    sentence_ordinal: int
    paragraph_ordinal: int
    language: str
    l5: str
    l4: str
    l3: str
    l2: str
    l1: str
    r1: str
    r2: str
    r3: str
    r4: str
    r5: str
    row_id: int
    kpf_count: int = 1


@dataclass(frozen=True, slots=True)
class KwicPage:
    query: str
    hits: tuple[KwicHit, ...]
    total: int
    page: int
    page_size: int
    context_size: int
    sort_by: str
    sort_keys: tuple[str, ...]
    sort_order: str
    pos: str
    whole_words: bool = True
    case_sensitive: bool = False
    regex: bool = False
    full_regex: bool = False
    available_total: int = 0
    sample_size: int = 0
    sample_seed: int = 0
    advanced_queries: tuple[str, ...] = ()
    context_queries: tuple[str, ...] = ()

    @property
    def num_pages(self) -> int:
        return max(1, math.ceil(self.total / self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class FileViewSegment:
    text: str
    is_hit: bool
    row_id: int | None = None
    selected: bool = False


@dataclass(frozen=True, slots=True)
class FileView:
    document_id: str
    filename: str
    language: str
    before: str
    keyword: str
    after: str
    row_id: int | None = None
    segments: tuple[FileViewSegment, ...] = ()
    hit_count: int = 0
    token_count: int = 0
    type_count: int = 0
    selected_hit: int = 0
    query: str = ""


@dataclass(frozen=True, slots=True)
class KwicMatch:
    global_position: int
    stream_position: int
    sentence_id: str
    document_id: str
    sentence_position: int
    language: str
    keyword_surfaces: tuple[str, ...]
    document_start: int = 0
    document_end: int = 0
    keyword_text: str = ""
    keyword_token_length: int = 0

    @property
    def token_length(self) -> int:
        return self.keyword_token_length or len(self.keyword_surfaces)
