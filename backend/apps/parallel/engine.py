from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from apps.processing.text import token_matches

from .contracts import (
    ALIGNMENT_UNITS,
    MAX_CONDITION_LENGTH,
    SEARCH_SIDES,
    SORT_POSITIONS,
    HighlightFragment,
    ParallelHit,
    ParallelIndexCorrupt,
    ParallelIndexUnavailable,
    ParallelQuery,
    ParallelSearchResult,
    _unique_nonempty,
)

AUTO_HIGHLIGHT_MIN_MATCHES = 5
AUTO_HIGHLIGHT_MIN_COVERAGE = 0.20
AUTO_HIGHLIGHT_MIN_SCORE = 3.0
AUTO_HIGHLIGHT_MAX_MATCH_PAIRS = 2_000
AUTO_HIGHLIGHT_MAX_BACKGROUND_PAIRS = 20_000
AUTO_HIGHLIGHT_MAX_GROUPS = 2
AUTO_HIGHLIGHT_MAX_SURFACES = 4

_ENGLISH_STOPWORDS = frozenset(
    """
    a an and any are as at be been being but by can could did do does for from had
    has have he her hers him his how i if in is it its may might must no nor not of
    on or our ours shall she should some than that the their theirs them then there
    these they this those to us was we were what when where which who whom why will
    with would you your yours
    """.split()
)
_CHINESE_STOPWORDS = frozenset(
    "的 了 和 是 在 有 与 及 或 而 被 把 将 对 为 以 于 从 到 中 上 下 这 那 一个 一种 我们 他们 你们".split()
)


@dataclass(frozen=True, slots=True)
class _MatchedOccurrence:
    row: tuple[object, ...]
    occurrence_ordinal: int
    span: tuple[int, int] | None


class ParallelSearchEngine:
    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.index_path = data_root.resolve() / "indexes" / corpus_id / "kwic_index.sqlite"

    def search(
        self,
        query: ParallelQuery,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> ParallelSearchResult:
        query.validate()
        if page < 1:
            raise ValueError("page must be at least 1.")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100.")

        try:
            with closing(self._connect()) as connection:
                columns = _parallel_columns(connection)
                where_sql, parameters = _build_filter(
                    query,
                    has_token_spans={
                        "zh_token_spans",
                        "en_token_spans",
                    }.issubset(columns),
                )
                select_columns = _parallel_select_columns(columns)
                row_sql = f"""
                    SELECT {select_columns}
                    FROM parallel_pairs
                    WHERE {where_sql}
                    ORDER BY global_position
                    """
                if query.sort_positions:
                    rows = connection.execute(row_sql, parameters).fetchall()
                    occurrences = _expand_occurrences(rows, query)
                    raw_total = len(occurrences)
                    thinned = occurrences[:: query.nth_entry]
                    thinned.sort(key=lambda item: _occurrence_sort_key(item, query))
                    total = len(thinned)
                    num_pages = max(1, math.ceil(total / page_size))
                    effective_page = min(page, num_pages)
                    start = (effective_page - 1) * page_size
                    page_occurrences = thinned[start : start + page_size]
                    row_total = len(rows)
                else:
                    page_occurrences, raw_total, total = _stream_occurrence_page(
                        connection.execute(row_sql, parameters),
                        query,
                        page=page,
                        page_size=page_size,
                    )
                    num_pages = max(1, math.ceil(total / page_size))
                    effective_page = min(page, num_pages)
                    if effective_page != page:
                        page_occurrences, _, _ = _stream_occurrence_page(
                            connection.execute(row_sql, parameters),
                            query,
                            page=effective_page,
                            page_size=page_size,
                        )
                    count_row = connection.execute(
                        f"SELECT COUNT(*) FROM parallel_pairs WHERE {where_sql}",
                        parameters,
                    ).fetchone()
                    row_total = int(count_row[0]) if count_row else 0
                auto_target_highlights = (
                    _infer_target_highlights(
                        connection,
                        query,
                        where_sql=where_sql,
                        parameters=parameters,
                        total=row_total,
                    )
                    if query.infer_target_highlights
                    else ()
                )
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引读取失败。") from exc

        hits = tuple(
            _row_to_hit(
                occurrence.row,
                query,
                auto_target_highlights=auto_target_highlights,
                occurrence_ordinal=occurrence.occurrence_ordinal,
                anchor_span=occurrence.span,
            )
            for occurrence in page_occurrences
        )
        return ParallelSearchResult(
            query=query,
            hits=hits,
            total=total,
            raw_total=raw_total,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            auto_target_highlights=auto_target_highlights,
        )

    def preview(self, *, alignment_unit: str, limit: int = 5) -> tuple[ParallelHit, ...]:
        """Return a bounded, ordered alignment sample without requiring a search term."""
        if alignment_unit not in ALIGNMENT_UNITS:
            raise ValueError("alignment_unit must be sentence or paragraph.")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query = ParallelQuery(alignment_unit=alignment_unit)
        try:
            with closing(self._connect()) as connection:
                select_columns = _parallel_select_columns(
                    _parallel_columns(connection)
                )
                rows = connection.execute(
                    f"""
                    SELECT {select_columns}
                    FROM parallel_pairs
                    WHERE alignment_unit = ?
                    ORDER BY global_position
                    LIMIT ?
                    """,
                    (alignment_unit, limit),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引读取失败。") from exc
        return tuple(_row_to_hit(row, query) for row in rows)

    def iter_export_rows(self, query: ParallelQuery) -> Iterator[tuple[object, ...]]:
        query.validate()
        try:
            with closing(self._connect()) as connection:
                columns = _parallel_columns(connection)
                where_sql, parameters = _build_filter(
                    query,
                    has_token_spans={
                        "zh_token_spans",
                        "en_token_spans",
                    }.issubset(columns),
                )
                select_columns = _parallel_select_columns(columns)
                row_cursor = connection.execute(
                    f"""
                    SELECT {select_columns}
                    FROM parallel_pairs
                    WHERE {where_sql}
                    ORDER BY global_position
                    """,
                    parameters,
                )
                if query.sort_positions:
                    occurrences = _expand_occurrences(row_cursor.fetchall(), query)[
                        :: query.nth_entry
                    ]
                    occurrences.sort(
                        key=lambda item: _occurrence_sort_key(item, query)
                    )
                    yield from _export_occurrences(occurrences)
                else:
                    raw_index = 0
                    for row in row_cursor:
                        for occurrence in _expand_occurrences([row], query):
                            if raw_index % query.nth_entry == 0:
                                yield from _export_occurrences((occurrence,))
                            raw_index += 1
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引读取失败。") from exc

    def _connect(self) -> sqlite3.Connection:
        if not self.index_path.is_file():
            raise ParallelIndexUnavailable("平行语料索引不存在。")
        try:
            connection = sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)
            connection.create_function(
                "whole_word_match",
                5,
                _whole_word_match,
                deterministic=True,
            )
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parallel_pairs'"
            ).fetchone()
            if table_exists is None:
                connection.close()
                raise ParallelIndexUnavailable("当前索引结构不支持平行检索。")
            return connection
        except ParallelIndexUnavailable:
            raise
        except sqlite3.Error as exc:
            raise ParallelIndexUnavailable("无法打开平行语料索引。") from exc


def normalize_condition(value: str) -> str:
    return " ".join(value.split())


def _build_filter(
    query: ParallelQuery,
    *,
    has_token_spans: bool,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["alignment_unit = ?"]
    parameters = [query.alignment_unit]
    if query.filename_contains:
        clauses.append(
            "(instr(lower(zh_filename), lower(?)) > 0 "
            "OR instr(lower(en_filename), lower(?)) > 0)"
        )
        parameters.extend((query.filename_contains, query.filename_contains))
    if query.min_confidence:
        clauses.append("confidence >= ?")
        parameters.append(query.min_confidence)
    positive: dict[str, list[str]] = {
        "zh": [query.zh_contains],
        "en": [query.en_contains],
    }
    positive[query.search_side].insert(0, query.q)
    negative = {
        "zh": query.zh_not_contains,
        "en": query.en_not_contains,
    }
    for side in SEARCH_SIDES:
        normalized_column = f"{side}_normalized"
        raw_column = f"{side}_text"
        token_spans_column = f"{side}_token_spans" if has_token_spans else "'[]'"
        for value in _unique_nonempty(positive[side]):
            if query.whole_words:
                clauses.append(
                    f"whole_word_match({raw_column}, ?, ?, ?, {token_spans_column}) = 1"
                )
                parameters.extend((value, int(query.case_sensitive), side))
            elif query.case_sensitive:
                clauses.append(f"instr({raw_column}, ?) > 0")
                parameters.append(value)
            else:
                clauses.append(f"instr({normalized_column}, ?) > 0")
                parameters.append(value.casefold())
        if negative[side]:
            if query.whole_words:
                clauses.append(
                    f"whole_word_match({raw_column}, ?, ?, ?, {token_spans_column}) = 0"
                )
                parameters.extend((negative[side], int(query.case_sensitive), side))
            elif query.case_sensitive:
                clauses.append(f"instr({raw_column}, ?) = 0")
                parameters.append(negative[side])
            else:
                clauses.append(f"instr({normalized_column}, ?) = 0")
                parameters.append(negative[side].casefold())
    return " AND ".join(clauses), tuple(parameters)


def _whole_word_match(
    text: str | None,
    value: str | None,
    case_sensitive: int,
    language: str,
    serialized_token_spans: str,
) -> int:
    """Match token boundaries without treating every Han character as one word."""
    if not text or not value:
        return 0
    return int(
        bool(
            _term_spans(
                text,
                value,
                case_sensitive=bool(case_sensitive),
                whole_words=True,
                language=language,
                token_spans=_decode_token_spans(serialized_token_spans),
            )
        )
    )


def _expand_occurrences(
    rows: list[tuple[object, ...]],
    query: ParallelQuery,
) -> list[_MatchedOccurrence]:
    anchor_side, anchor_term = _anchor_condition(query)
    expanded: list[_MatchedOccurrence] = []
    for row in rows:
        if not anchor_term:
            expanded.append(_MatchedOccurrence(row=row, occurrence_ordinal=1, span=None))
            continue
        text = str(row[3] if anchor_side == "zh" else row[4])
        spans = _term_spans(
            text,
            anchor_term,
            case_sensitive=query.case_sensitive,
            whole_words=query.whole_words,
            language=anchor_side,
            token_spans=_decode_token_spans(
                str(row[10] if anchor_side == "zh" else row[11])
            ),
        )
        for ordinal, span in enumerate(spans, start=1):
            expanded.append(
                _MatchedOccurrence(
                    row=row,
                    occurrence_ordinal=ordinal,
                    span=span,
                )
            )
    return expanded


def _stream_occurrence_page(
    rows: Iterator[tuple[object, ...]],
    query: ParallelQuery,
    *,
    page: int,
    page_size: int,
) -> tuple[list[_MatchedOccurrence], int, int]:
    start = (page - 1) * page_size
    selected: list[_MatchedOccurrence] = []
    raw_total = 0
    total = 0
    for row in rows:
        for occurrence in _expand_occurrences([row], query):
            include = raw_total % query.nth_entry == 0
            raw_total += 1
            if not include:
                continue
            if start <= total < start + page_size:
                selected.append(occurrence)
            total += 1
    return selected, raw_total, total


def _export_occurrences(
    occurrences: Iterable[_MatchedOccurrence],
) -> Iterator[tuple[object, ...]]:
    for occurrence in occurrences:
        row = occurrence.row
        yield (
            row[0],
            row[2],
            occurrence.occurrence_ordinal,
            row[8],
            row[9],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
        )


def _anchor_condition(query: ParallelQuery) -> tuple[str, str]:
    if query.q:
        return query.search_side, query.q
    preferred = query.zh_contains if query.search_side == "zh" else query.en_contains
    if preferred:
        return query.search_side, preferred
    if query.zh_contains:
        return "zh", query.zh_contains
    if query.en_contains:
        return "en", query.en_contains
    return query.search_side, ""


def _occurrence_sort_key(
    occurrence: _MatchedOccurrence,
    query: ParallelQuery,
) -> tuple[object, ...]:
    anchor_side, _ = _anchor_condition(query)
    text = str(occurrence.row[3] if anchor_side == "zh" else occurrence.row[4])
    token_spans = _decode_token_spans(
        str(occurrence.row[10] if anchor_side == "zh" else occurrence.row[11])
    )
    context = _sort_context(
        text,
        occurrence.span,
        anchor_side,
        token_spans=token_spans,
    )
    values = tuple(context.get(position, "").casefold() for position in query.sort_positions)
    return (*values, int(occurrence.row[0]), occurrence.occurrence_ordinal)


def _sort_context(
    text: str,
    span: tuple[int, int] | None,
    language: str,
    *,
    token_spans: tuple[tuple[str, int, int], ...] = (),
) -> dict[str, str]:
    if span is None:
        return {"CEN": ""}
    start, end = span
    tokens = token_spans or tuple(
        (match.group(0), match.start(), match.end())
        for match in token_matches(text, language)
    )
    left = [surface for surface, _, token_end in tokens if token_end <= start]
    right = [surface for surface, token_start, _ in tokens if token_start >= end]
    values = {"CEN": text[start:end]}
    for distance in range(1, 6):
        values[f"L{distance}"] = left[-distance] if len(left) >= distance else ""
        values[f"R{distance}"] = right[distance - 1] if len(right) >= distance else ""
    return values


def _context_slice(
    text: str,
    span: tuple[int, int],
    *,
    language: str,
    context_size: int,
    token_spans: tuple[tuple[str, int, int], ...] = (),
) -> tuple[str, tuple[int, int]]:
    start, end = span
    tokens = token_spans or tuple(
        (match.group(0), match.start(), match.end())
        for match in token_matches(text, language)
    )
    left = [token for token in tokens if token[2] <= start]
    right = [token for token in tokens if token[1] >= end]
    slice_start = left[-context_size][1] if len(left) >= context_size else 0
    slice_end = right[context_size - 1][2] if len(right) >= context_size else len(text)
    prefix = "…" if slice_start else ""
    suffix = "…" if slice_end < len(text) else ""
    clipped = f"{prefix}{text[slice_start:slice_end]}{suffix}"
    adjusted = (
        len(prefix) + start - slice_start,
        len(prefix) + end - slice_start,
    )
    return clipped, adjusted


def _row_to_hit(
    row: tuple[object, ...],
    query: ParallelQuery,
    *,
    auto_target_highlights: tuple[str, ...] = (),
    occurrence_ordinal: int = 1,
    anchor_span: tuple[int, int] | None = None,
) -> ParallelHit:
    zh_text = str(row[3])
    en_text = str(row[4])
    zh_highlights = query.zh_highlights
    en_highlights = query.en_highlights
    if query.search_side == "zh":
        en_highlights = _merge_highlights(en_highlights, auto_target_highlights)
    else:
        zh_highlights = _merge_highlights(zh_highlights, auto_target_highlights)
    anchor_side, _ = _anchor_condition(query)
    display_anchor_span = anchor_span
    if anchor_span is not None and anchor_side == "zh":
        zh_text, display_anchor_span = _context_slice(
            zh_text,
            anchor_span,
            language="zh",
            context_size=query.context_size,
            token_spans=_decode_token_spans(str(row[10])),
        )
    elif anchor_span is not None and anchor_side == "en":
        en_text, display_anchor_span = _context_slice(
            en_text,
            anchor_span,
            language="en",
            context_size=query.context_size,
            token_spans=_decode_token_spans(str(row[11])),
        )
    zh_fragments = (
        _highlight_explicit_span(zh_text, display_anchor_span)
        if display_anchor_span is not None and anchor_side == "zh"
        else highlight_fragments(
            zh_text,
            zh_highlights,
            case_sensitive=query.case_sensitive,
            whole_words=query.whole_words,
            language="zh",
            token_spans=_decode_token_spans(str(row[10])),
        )
    )
    en_fragments = (
        _highlight_explicit_span(en_text, display_anchor_span)
        if display_anchor_span is not None and anchor_side == "en"
        else highlight_fragments(
            en_text,
            en_highlights,
            case_sensitive=query.case_sensitive,
            whole_words=query.whole_words,
            language="en",
            token_spans=_decode_token_spans(str(row[11])),
        )
    )
    return ParallelHit(
        global_position=int(row[0]),
        pair_id=str(row[1]),
        pair_ordinal=int(row[2]),
        zh_text=zh_text,
        en_text=en_text,
        zh_fragments=zh_fragments,
        en_fragments=en_fragments,
        alignment_unit=str(row[5]),
        method=str(row[6]),
        confidence=float(row[7]),
        zh_filename=str(row[8]),
        en_filename=str(row[9]),
        occurrence_ordinal=occurrence_ordinal,
    )


def highlight_fragments(
    text: str,
    terms: tuple[str, ...],
    *,
    case_sensitive: bool = False,
    whole_words: bool = False,
    language: str = "en",
    token_spans: tuple[tuple[str, int, int], ...] = (),
) -> tuple[HighlightFragment, ...]:
    ordered = sorted((term for term in terms if term), key=len, reverse=True)
    if not ordered:
        return (HighlightFragment(text=text, matched=False),)
    candidates: list[tuple[int, int]] = []
    for term in ordered:
        candidates.extend(
            _term_spans(
                text,
                term,
                case_sensitive=case_sensitive,
                whole_words=whole_words,
                language=language,
                token_spans=token_spans,
            )
        )
    candidates.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    spans: list[tuple[int, int]] = []
    for start, end in candidates:
        if spans and start < spans[-1][1]:
            continue
        spans.append((start, end))
    fragments: list[HighlightFragment] = []
    position = 0
    for start, end in spans:
        if start > position:
            fragments.append(HighlightFragment(text[position:start], False))
        fragments.append(HighlightFragment(text[start:end], True))
        position = end
    if position < len(text):
        fragments.append(HighlightFragment(text[position:], False))
    return tuple(fragments) or (HighlightFragment(text=text, matched=False),)


def _highlight_explicit_span(
    text: str,
    span: tuple[int, int],
) -> tuple[HighlightFragment, ...]:
    start, end = span
    fragments: list[HighlightFragment] = []
    if start:
        fragments.append(HighlightFragment(text[:start], False))
    fragments.append(HighlightFragment(text[start:end], True))
    if end < len(text):
        fragments.append(HighlightFragment(text[end:], False))
    return tuple(fragments)


def _term_spans(
    text: str,
    value: str,
    *,
    case_sensitive: bool,
    whole_words: bool,
    language: str,
    token_spans: tuple[tuple[str, int, int], ...] = (),
) -> tuple[tuple[int, int], ...]:
    spans = _literal_spans(text, value, case_sensitive=case_sensitive)
    if not whole_words or not spans:
        return spans
    if token_spans:
        starts = {start for _, start, _ in token_spans}
        ends = {end for _, _, end in token_spans}
        return tuple((start, end) for start, end in spans if start in starts and end in ends)
    if language == "zh":
        fallback_spans = tuple(
            (match.start(), match.end()) for match in token_matches(text, "zh")
        )
        starts = {start for start, _ in fallback_spans}
        ends = {end for _, end in fallback_spans}
        return tuple((start, end) for start, end in spans if start in starts and end in ends)
    return tuple(
        (start, end)
        for start, end in spans
        if _is_word_boundary(text, start, end)
    )


def _literal_spans(
    text: str,
    value: str,
    *,
    case_sensitive: bool,
) -> tuple[tuple[int, int], ...]:
    if not value:
        return ()
    if case_sensitive:
        haystack = text
        needle = value
        offsets = tuple(range(len(text)))
    else:
        folded: list[str] = []
        offset_list: list[int] = []
        for index, character in enumerate(text):
            replacement = character.casefold()
            folded.append(replacement)
            offset_list.extend([index] * len(replacement))
        haystack = "".join(folded)
        needle = value.casefold()
        offsets = tuple(offset_list)
    if not needle or not offsets:
        return ()
    spans: list[tuple[int, int]] = []
    position = 0
    while (found := haystack.find(needle, position)) >= 0:
        folded_end = found + len(needle) - 1
        if folded_end < len(offsets):
            spans.append((offsets[found], offsets[folded_end] + 1))
        position = found + max(1, len(needle))
    return tuple(dict.fromkeys(spans))


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    return (not before or not _is_unicode_letter(before)) and (
        not after or not _is_unicode_letter(after)
    )


def _is_unicode_letter(value: str) -> bool:
    return value.isalpha()


def _parallel_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(parallel_pairs)").fetchall()
    }


def _parallel_select_columns(columns: set[str]) -> str:
    source_columns = (
        "zh_filename, en_filename"
        if {"zh_filename", "en_filename"}.issubset(columns)
        else "'' AS zh_filename, '' AS en_filename"
    )
    token_columns = (
        "zh_token_spans, en_token_spans"
        if {"zh_token_spans", "en_token_spans"}.issubset(columns)
        else "'[]' AS zh_token_spans, '[]' AS en_token_spans"
    )
    return (
        "global_position, pair_id, pair_ordinal, zh_text, en_text, "
        f"alignment_unit, method, confidence, {source_columns}, {token_columns}"
    )


def _decode_token_spans(value: str) -> tuple[tuple[str, int, int], ...]:
    if not value or value == "[]":
        return ()
    try:
        payload = json.loads(value)
        spans = tuple(
            (str(surface), int(start), int(end))
            for surface, start, end in payload
            if 0 <= int(start) <= int(end)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return spans


def _infer_target_highlights(
    connection: sqlite3.Connection,
    query: ParallelQuery,
    *,
    where_sql: str,
    parameters: tuple[object, ...],
    total: int,
) -> tuple[str, ...]:
    """Infer likely target-side equivalents from pair-level corpus co-occurrence.

    This intentionally requires repeated evidence and a strong foreground/background
    contrast. It is a display aid, not word alignment or a translation assertion.
    """
    if total < AUTO_HIGHLIGHT_MIN_MATCHES or not query.q:
        return ()
    target_side = "en" if query.search_side == "zh" else "zh"
    explicit_target = query.en_contains if target_side == "en" else query.zh_contains
    if explicit_target:
        return ()

    target_column = f"{target_side}_text"
    foreground_rows = connection.execute(
        f"""
        SELECT global_position, {target_column}
        FROM parallel_pairs
        WHERE {where_sql}
        ORDER BY global_position
        LIMIT ?
        """,
        (*parameters, AUTO_HIGHLIGHT_MAX_MATCH_PAIRS),
    ).fetchall()
    if len(foreground_rows) < AUTO_HIGHLIGHT_MIN_MATCHES:
        return ()
    background_rows = connection.execute(
        f"""
        SELECT global_position, {target_column}
        FROM parallel_pairs
        WHERE alignment_unit = ?
        ORDER BY global_position
        LIMIT ?
        """,
        (query.alignment_unit, AUTO_HIGHLIGHT_MAX_BACKGROUND_PAIRS),
    ).fetchall()

    foreground = {int(position): str(text) for position, text in foreground_rows}
    background = {int(position): str(text) for position, text in background_rows}
    background.update(foreground)
    if len(background) <= len(foreground):
        return ()

    foreground_counts: Counter[str] = Counter()
    background_counts: Counter[str] = Counter()
    surfaces: dict[str, Counter[str]] = {}
    for text in foreground.values():
        terms = _candidate_terms(text, target_side)
        foreground_counts.update({key for key, _ in terms})
        for key, surface in terms:
            surfaces.setdefault(key, Counter())[surface] += 1
    for text in background.values():
        background_counts.update({key for key, _ in _candidate_terms(text, target_side)})

    matched_total = len(foreground)
    background_only_total = len(background) - matched_total
    ranked: list[tuple[float, str]] = []
    for key, matched_count in foreground_counts.items():
        coverage = matched_count / matched_total
        if coverage < AUTO_HIGHLIGHT_MIN_COVERAGE:
            continue
        corpus_count = background_counts[key]
        background_only_count = max(0, corpus_count - matched_count)
        matched_odds = (matched_count + 0.5) / (matched_total - matched_count + 0.5)
        background_odds = (background_only_count + 0.5) / (
            background_only_total - background_only_count + 0.5
        )
        score = math.log(matched_odds / background_odds) * math.log1p(matched_count)
        if score >= AUTO_HIGHLIGHT_MIN_SCORE:
            ranked.append((score, key))
    if not ranked:
        return ()

    ranked.sort(reverse=True)
    relative_cutoff = ranked[0][0] * 0.55
    selected_keys = [
        key
        for score, key in ranked
        if score >= relative_cutoff
    ][:AUTO_HIGHLIGHT_MAX_GROUPS]
    highlights: list[str] = []
    for key in selected_keys:
        variants = sorted(
            surfaces[key].items(),
            key=lambda item: (-item[1], -len(item[0]), item[0].casefold()),
        )
        highlights.extend(surface for surface, _ in variants[:AUTO_HIGHLIGHT_MAX_SURFACES])
    return tuple(dict.fromkeys(highlights))


def _candidate_terms(text: str, language: str) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    for match in token_matches(text, language):
        surface = match.group(0)
        normalized = surface.casefold()
        if language == "en":
            if normalized in _ENGLISH_STOPWORDS or len(normalized) < 3:
                continue
            key = _english_stem(normalized)
        else:
            if normalized in _CHINESE_STOPWORDS or len(normalized) < 2:
                continue
            key = normalized
        candidates.append((key, normalized if language == "en" else surface))
    return tuple(candidates)


def _english_stem(value: str) -> str:
    if len(value) > 4 and value.endswith("ies"):
        return f"{value[:-3]}y"
    if len(value) > 4 and value.endswith(("ches", "shes", "xes", "zes", "sses")):
        return value[:-2]
    if len(value) > 3 and value.endswith("s") and not value.endswith(("is", "ss", "us")):
        return value[:-1]
    return value


def _merge_highlights(
    explicit: tuple[str, ...],
    inferred: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*explicit, *inferred)))
