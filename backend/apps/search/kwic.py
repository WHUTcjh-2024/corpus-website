from __future__ import annotations

import json
import math
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any, Sequence

import regex as safe_regex

from apps.processing.text import normalize_token, token_matches

from .contracts import (
    FileView,
    FileViewSegment,
    KwicHit,
    KwicIndexCorrupt,
    KwicIndexUnavailable,
    KwicMatch,
    KwicPage,
    KwicQueryError,
    QueryToken,
)


DEFAULT_CONTEXT_SIZE = 5
DEFAULT_PAGE_SIZE = 50
MAX_CONTEXT_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_QUERY_TERMS = 20
MAX_REGEX_LENGTH = 200
REGEX_TIMEOUT_SECONDS = 0.05
SORT_FIELDS = (
    "L5",
    "L4",
    "L3",
    "L2",
    "L1",
    "C",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "FILE",
    "FILE_ID",
    "ROW_ID",
)
MAX_SORT_KEYS = 3


class KwicSearchEngine:
    """AntConc-style token concordancer backed by the immutable SQLite index."""

    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = str(corpus_id)
        self.index_dir = self.data_root / "indexes" / self.corpus_id
        self.processed_dir = self.data_root / "processed" / self.corpus_id
        self.index_path = self.index_dir / "kwic_index.sqlite"

    def file_view(
        self,
        *,
        document_id: str,
        language: str,
        row_id: int | None = None,
        query: str = "",
        whole_words: bool = True,
        case_sensitive: bool = False,
        regex: bool = False,
        full_regex: bool = False,
    ) -> FileView:
        """Return an indexed source document with an optional token anchor."""
        if language not in {"zh", "en"}:
            raise KwicQueryError("文件语言必须是中文或英文。")
        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                self._require_index_schema(connection)
                row = connection.execute(
                    """
                    SELECT d.filename, s.text
                    FROM document_streams s
                    JOIN documents d ON d.document_id = s.document_id
                    WHERE s.document_id = ? AND s.language = ?
                    """,
                    (document_id, language),
                ).fetchone()
                if row is None:
                    raise KwicQueryError("索引中不存在该来源文件。")
                filename, text = str(row[0]), str(row[1])
                token = None
                if row_id is not None:
                    token = connection.execute(
                        """
                        SELECT document_start, document_end, surface
                        FROM tokens
                        WHERE global_position = ?
                          AND document_id = ?
                          AND language = ?
                        """,
                        (row_id, document_id, language),
                    ).fetchone()
                statistics = connection.execute(
                    """
                    SELECT COUNT(*), COUNT(DISTINCT normalized)
                    FROM tokens WHERE document_id = ? AND language = ?
                    """,
                    (document_id, language),
                ).fetchone()
                spans: list[tuple[int, int, int | None]] = []
                query = query.strip() if full_regex else " ".join(query.split())
                if query and full_regex:
                    validate_full_regex(query, case_sensitive=case_sensitive)
                    compiled = safe_regex.compile(
                        query,
                        safe_regex.VERSION1
                        | (0 if case_sensitive else safe_regex.IGNORECASE),
                    )
                    for found in compiled.finditer(
                        text,
                        timeout=REGEX_TIMEOUT_SECONDS,
                    ):
                        if found.start() != found.end():
                            spans.append((found.start(), found.end(), None))
                elif query:
                    _, matchers = compile_query(
                        query,
                        language=language,
                        whole_words=whole_words,
                        case_sensitive=case_sensitive,
                        regex=regex,
                    )
                    _register_regex_function(connection)
                    sql, term_parameters = self._match_sql(
                        matchers,
                        count=False,
                        include_order=False,
                        paginated=False,
                    )
                    rows = connection.execute(
                        sql + " AND t0.document_id = ? ORDER BY t0.global_position",
                        [language, *term_parameters, document_id],
                    ).fetchall()
                    spans.extend(
                        (int(found[6]), int(found[7]), int(found[0]))
                        for found in rows
                    )
                    _raise_if_regex_timed_out(connection)
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("File View 索引读取失败。") from exc
        except TimeoutError as exc:
            raise KwicQueryError("File View 全文正则执行超时。") from exc

        if not spans and token is not None:
            spans.append((int(token[0]), int(token[1]), row_id))
        spans = sorted(
            {
                (max(start, 0), min(end, len(text)), hit_row_id)
                for start, end, hit_row_id in spans
                if start < end
            },
            key=lambda item: (item[0], item[1]),
        )
        segments: list[FileViewSegment] = []
        cursor = 0
        selected_hit = 0
        accepted_spans: list[tuple[int, int, int | None]] = []
        for start, end, hit_row_id in spans:
            if start < cursor:
                continue
            if start > cursor:
                segments.append(FileViewSegment(text[cursor:start], False))
            selected = row_id is not None and hit_row_id == row_id
            if selected:
                selected_hit = len(accepted_spans) + 1
            segments.append(FileViewSegment(text[start:end], True, hit_row_id, selected))
            accepted_spans.append((start, end, hit_row_id))
            cursor = end
        if cursor < len(text):
            segments.append(FileViewSegment(text[cursor:], False))
        token_count, type_count = (int(value) for value in statistics)
        if token is None:
            before, keyword, after = text, "", ""
        else:
            start, end = max(int(token[0]), 0), min(int(token[1]), len(text))
            before, keyword, after = text[:start], text[start:end], text[end:]
        return FileView(
            document_id,
            filename,
            language,
            before,
            keyword,
            after,
            row_id,
            tuple(segments),
            len(accepted_spans),
            token_count,
            type_count,
            selected_hit,
            query,
        )

    def search(
        self,
        query: str,
        *,
        language: str | None = None,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        sort_by: str = "",
        sort_keys: Sequence[str] | None = None,
        sort_order: str = "value",
        pos: str = "",
        whole_words: bool = True,
        case_sensitive: bool = False,
        regex: bool = False,
        full_regex: bool = False,
        sample_size: int = 0,
        sample_seed: int = 0,
    ) -> KwicPage:
        query = query.strip() if full_regex else " ".join(query.split())
        if not query:
            raise KwicQueryError("查询词不能为空。")
        _validate_page_options(context_size, page, page_size)
        if not 0 <= sample_size <= 100:
            raise KwicQueryError("随机结果集大小必须在 0 到 100 之间。")
        if not 0 <= sample_seed <= 2_147_483_647:
            raise KwicQueryError("随机种子必须在 0 到 2147483647 之间。")
        normalized_sort_keys = normalize_sort_keys(sort_keys, fallback=sort_by)
        sort_order = normalize_sort_order(sort_order)
        if full_regex:
            return self._search_full_regex(
                query,
                language=language,
                context_size=context_size,
                page=page,
                page_size=page_size,
                sort_keys=normalized_sort_keys,
                sort_order=sort_order,
                case_sensitive=case_sensitive,
                sample_size=sample_size,
                sample_seed=sample_seed,
            )
        language, matchers = compile_query(
            query,
            language=language,
            whole_words=whole_words,
            case_sensitive=case_sensitive,
            regex=regex,
        )
        pos = pos.strip()
        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                self._require_index_schema(connection)
                _register_regex_function(connection)
                matchers = self._resolve_chinese_terms(
                    connection,
                    query=query,
                    language=language,
                    matchers=matchers,
                    whole_words=whole_words,
                    case_sensitive=case_sensitive,
                    regex=regex,
                    pos=pos,
                )
                total = self._count_matches(connection, language, matchers, pos=pos)
                _raise_if_regex_timed_out(connection)
                available_total = total
                if sample_size:
                    effective_page = 1
                    matches = self._sample_matches(
                        connection,
                        language,
                        matchers,
                        sample_size=min(sample_size, total),
                        sample_seed=sample_seed,
                        sort_keys=normalized_sort_keys,
                        sort_order=sort_order,
                        pos=pos,
                    )
                    total = len(matches)
                    page_size = max(1, total)
                else:
                    num_pages = max(1, math.ceil(total / page_size))
                    effective_page = min(page, num_pages)
                    matches = self._page_matches(
                        connection,
                        language,
                        matchers,
                        page=effective_page,
                        page_size=page_size,
                        sort_keys=normalized_sort_keys,
                        sort_order=sort_order,
                        pos=pos,
                    )
                _raise_if_regex_timed_out(connection)
                context_tokens = self._context_tokens(
                    connection,
                    matches,
                    radius=max(context_size, 5),
                )
                source_snippets = self._source_snippets(
                    connection,
                    matches,
                    context_size=context_size,
                )
                if normalized_sort_keys and not sample_size:
                    kpf_matches = self._all_matches(
                        connection,
                        language,
                        matchers,
                        pos=pos,
                    )
                    kpf_context = self._context_tokens(
                        connection,
                        kpf_matches,
                        radius=5,
                    )
                else:
                    kpf_matches = matches
                    kpf_context = context_tokens
        except safe_regex.error as exc:
            raise KwicQueryError(f"正则表达式无效：{exc}") from exc
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("KWIC 索引读取失败。") from exc

        metadata = self._metadata_for(matches)
        kpf_patterns = Counter(
            _hit_pattern(match, normalized_sort_keys, kpf_context)
            for match in kpf_matches
        )
        hits = tuple(
            self._build_hit(
                match,
                context_tokens,
                metadata,
                context_size,
                source_snippets=source_snippets,
                kpf_count=kpf_patterns[
                    _hit_pattern(match, normalized_sort_keys, context_tokens)
                ],
            )
            for match in matches
        )
        return KwicPage(
            query=query,
            hits=hits,
            total=total,
            page=effective_page,
            page_size=page_size,
            context_size=context_size,
            sort_by=normalized_sort_keys[0] if normalized_sort_keys else "",
            sort_keys=normalized_sort_keys,
            sort_order=sort_order,
            pos=pos,
            whole_words=whole_words,
            case_sensitive=case_sensitive,
            regex=regex,
            available_total=available_total,
            sample_size=sample_size,
            sample_seed=sample_seed,
        )

    def search_advanced(
        self,
        query: str,
        *,
        query_list: Sequence[str] = (),
        context_queries: Sequence[str] = (),
        context_logic: str = "or",
        context_from: int = -5,
        context_to: int = 5,
        exclude_context: bool = False,
        language: str | None = None,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        sort_keys: Sequence[str] | None = None,
        sort_order: str = "value",
        pos: str = "",
        whole_words: bool = True,
        case_sensitive: bool = False,
        regex: bool = False,
        sample_size: int = 0,
        sample_seed: int = 0,
    ) -> KwicPage:
        queries = tuple(
            dict.fromkeys(
                normalized
                for value in (query, *query_list)
                if (normalized := " ".join(str(value).split()))
            )
        )
        if not queries:
            raise KwicQueryError("查询词或查询词列表不能为空。")
        if len(queries) > 100:
            raise KwicQueryError("查询词列表最多包含 100 项。")
        context_values = tuple(
            dict.fromkeys(
                normalized
                for value in context_queries
                if (normalized := " ".join(str(value).split()))
            )
        )
        if len(context_values) > 100:
            raise KwicQueryError("语境词列表最多包含 100 项。")
        if context_logic not in {"or", "and"}:
            raise KwicQueryError("语境词逻辑必须是 OR 或 AND。")
        if not -10 <= context_from <= 10 or not -10 <= context_to <= 10:
            raise KwicQueryError("语境窗口必须位于 L10 到 R10。")
        if context_from > context_to:
            raise KwicQueryError("语境窗口起点不能大于终点。")
        _validate_page_options(context_size, page, page_size)
        if not 0 <= sample_size <= 100:
            raise KwicQueryError("随机结果集大小必须在 0 到 100 之间。")
        normalized_sort_keys = normalize_sort_keys(sort_keys)
        sort_order = normalize_sort_order(sort_order)
        pos = pos.strip()
        detected_languages = {
            compile_query(
                value,
                language=language,
                whole_words=whole_words,
                case_sensitive=case_sensitive,
                regex=regex,
            )[0]
            for value in queries
        }
        if len(detected_languages) != 1:
            raise KwicQueryError("查询词列表中的语言必须一致。")
        selected_language = detected_languages.pop()
        context_matchers: list[QueryToken] = []
        for value in context_values:
            context_language, matchers = compile_query(
                value,
                language=selected_language,
                whole_words=whole_words,
                case_sensitive=case_sensitive,
                regex=regex,
            )
            if context_language != selected_language or len(matchers) != 1:
                raise KwicQueryError("每个语境词必须是所选语言中的单个 Token。")
            context_matchers.append(matchers[0])

        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                self._require_index_schema(connection)
                _register_regex_function(connection)
                all_matches: dict[tuple[int, int], KwicMatch] = {}
                for value in queries:
                    _, matchers = compile_query(
                        value,
                        language=selected_language,
                        whole_words=whole_words,
                        case_sensitive=case_sensitive,
                        regex=regex,
                    )
                    matchers = self._resolve_chinese_terms(
                        connection,
                        query=value,
                        language=selected_language,
                        matchers=matchers,
                        whole_words=whole_words,
                        case_sensitive=case_sensitive,
                        regex=regex,
                        pos=pos,
                    )
                    for match in self._all_matches(
                        connection,
                        selected_language,
                        matchers,
                        pos=pos,
                    ):
                        all_matches[(match.global_position, match.token_length)] = match
                matches = list(all_matches.values())
                if context_matchers:
                    radius = max(abs(context_from), abs(context_to), 1)
                    filtering_context = self._context_tokens(
                        connection,
                        matches,
                        radius=radius,
                    )
                    matches = [
                        match
                        for match in matches
                        if _matches_context_constraints(
                            match,
                            filtering_context,
                            context_matchers,
                            logic=context_logic,
                            window_from=context_from,
                            window_to=context_to,
                            exclude=exclude_context,
                        )
                    ]
                available_total = len(matches)
                if sample_size:
                    matches.sort(
                        key=lambda item: (
                            (item.global_position * 1103515245 + sample_seed) & 2147483647
                        )
                    )
                    matches = matches[:sample_size]
                sorting_context = self._context_tokens(connection, matches, radius=5)
                filenames = dict(
                    connection.execute("SELECT document_id, filename FROM documents").fetchall()
                )
                _sort_matches_in_memory(
                    matches,
                    normalized_sort_keys,
                    sort_order,
                    sorting_context,
                    filenames,
                )
                kpf_patterns = Counter(
                    _hit_pattern(match, normalized_sort_keys, sorting_context)
                    for match in matches
                )
                total = len(matches)
                if sample_size:
                    effective_page = 1
                    page_size = max(1, total)
                    page_matches = matches
                else:
                    num_pages = max(1, math.ceil(total / page_size))
                    effective_page = min(page, num_pages)
                    start = (effective_page - 1) * page_size
                    page_matches = matches[start : start + page_size]
                context_tokens = self._context_tokens(
                    connection,
                    page_matches,
                    radius=max(context_size, 5),
                )
                source_snippets = self._source_snippets(
                    connection,
                    page_matches,
                    context_size=context_size,
                )
                _raise_if_regex_timed_out(connection)
        except safe_regex.error as exc:
            raise KwicQueryError(f"正则表达式无效：{exc}") from exc
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("高级 KWIC 索引读取失败。") from exc

        metadata = self._metadata_for(page_matches)
        hits = tuple(
            self._build_hit(
                match,
                context_tokens,
                metadata,
                context_size,
                source_snippets=source_snippets,
                kpf_count=kpf_patterns[
                    _hit_pattern(match, normalized_sort_keys, context_tokens)
                ],
            )
            for match in page_matches
        )
        return KwicPage(
            query=query,
            hits=hits,
            total=total,
            page=effective_page,
            page_size=page_size,
            context_size=context_size,
            sort_by=normalized_sort_keys[0] if normalized_sort_keys else "",
            sort_keys=normalized_sort_keys,
            sort_order=sort_order,
            pos=pos,
            whole_words=whole_words,
            case_sensitive=case_sensitive,
            regex=regex,
            available_total=available_total,
            sample_size=sample_size,
            sample_seed=sample_seed,
            advanced_queries=queries,
            context_queries=context_values,
        )

    def _search_full_regex(
        self,
        query: str,
        *,
        language: str | None,
        context_size: int,
        page: int,
        page_size: int,
        sort_keys: tuple[str, ...],
        sort_order: str,
        case_sensitive: bool,
        sample_size: int,
        sample_seed: int,
    ) -> KwicPage:
        language = language or (
            "zh" if any("\u4e00" <= character <= "\u9fff" for character in query) else "en"
        )
        if language not in {"zh", "en"}:
            raise KwicQueryError("查询语言必须是中文或英文。")
        _validate_regex(query, case_sensitive=case_sensitive, max_length=500)
        self._require_artifacts()
        compiled = safe_regex.compile(
            query,
            safe_regex.VERSION1 | (0 if case_sensitive else safe_regex.IGNORECASE),
        )
        matches: list[KwicMatch] = []
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                self._require_index_schema(connection)
                streams = connection.execute(
                    """
                    SELECT document_id, text
                    FROM document_streams
                    WHERE language = ?
                    ORDER BY document_id
                    """,
                    (language,),
                ).fetchall()
                for document_id, text in streams:
                    token_rows = connection.execute(
                        """
                        SELECT global_position, stream_position, sentence_id,
                               sentence_position, document_start, document_end, surface
                        FROM tokens
                        WHERE document_id = ? AND language = ?
                        ORDER BY stream_position
                        """,
                        (document_id, language),
                    ).fetchall()
                    if not token_rows:
                        continue
                    starts = [int(row[4]) for row in token_rows]
                    ends = [int(row[5]) for row in token_rows]
                    for found in compiled.finditer(
                        str(text),
                        timeout=REGEX_TIMEOUT_SECONDS,
                    ):
                        if found.start() == found.end():
                            continue
                        first = bisect_right(ends, found.start())
                        last = bisect_left(starts, found.end()) - 1
                        if first > last or first >= len(token_rows) or last < 0:
                            continue
                        selected = token_rows[first : last + 1]
                        first_token = selected[0]
                        matches.append(
                            KwicMatch(
                                global_position=int(first_token[0]),
                                stream_position=int(first_token[1]),
                                sentence_id=str(first_token[2]),
                                document_id=str(document_id),
                                sentence_position=int(first_token[3]),
                                language=language,
                                keyword_surfaces=tuple(str(row[6]) for row in selected),
                                document_start=found.start(),
                                document_end=found.end(),
                                keyword_text=found.group(0),
                                keyword_token_length=len(selected),
                            )
                        )
                filenames = dict(
                    connection.execute(
                        "SELECT document_id, filename FROM documents"
                    ).fetchall()
                )
                available_total = len(matches)
                if sample_size:
                    matches.sort(
                        key=lambda item: (
                            (item.global_position * 1103515245 + sample_seed)
                            & 2147483647
                        )
                    )
                    matches = matches[:sample_size]
                sorting_context = self._context_tokens(connection, matches, radius=5)
                _sort_matches_in_memory(
                    matches,
                    sort_keys,
                    sort_order,
                    sorting_context,
                    filenames,
                )
                kpf_patterns = Counter(
                    _hit_pattern(match, sort_keys, sorting_context)
                    for match in matches
                )
                total = len(matches) if sample_size else available_total
                if sample_size:
                    effective_page = 1
                    page_size = max(1, total)
                    page_matches = matches
                else:
                    num_pages = max(1, math.ceil(total / page_size))
                    effective_page = min(page, num_pages)
                    start = (effective_page - 1) * page_size
                    page_matches = matches[start : start + page_size]
                context_tokens = self._context_tokens(
                    connection,
                    page_matches,
                    radius=max(context_size, 5),
                )
                source_snippets = self._source_snippets(
                    connection,
                    page_matches,
                    context_size=context_size,
                )
        except TimeoutError as exc:
            raise KwicQueryError("全文正则执行超时，请缩小表达式范围。") from exc
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("全文正则索引读取失败。") from exc

        metadata = self._metadata_for(page_matches)
        hits = tuple(
            self._build_hit(
                match,
                context_tokens,
                metadata,
                context_size,
                source_snippets=source_snippets,
                kpf_count=kpf_patterns[_hit_pattern(match, sort_keys, context_tokens)],
            )
            for match in page_matches
        )
        return KwicPage(
            query=query,
            hits=hits,
            total=total,
            page=effective_page,
            page_size=page_size,
            context_size=context_size,
            sort_by=sort_keys[0] if sort_keys else "",
            sort_keys=sort_keys,
            sort_order=sort_order,
            pos="",
            whole_words=False,
            case_sensitive=case_sensitive,
            regex=True,
            full_regex=True,
            available_total=available_total,
            sample_size=sample_size,
            sample_seed=sample_seed,
        )

    def _resolve_chinese_terms(
        self,
        connection: sqlite3.Connection,
        *,
        query: str,
        language: str,
        matchers: tuple[QueryToken, ...],
        whole_words: bool,
        case_sensitive: bool,
        regex: bool,
        pos: str,
    ) -> tuple[QueryToken, ...]:
        if (
            language != "zh"
            or regex
            or not whole_words
            or any(character.isspace() for character in query)
            or _has_wildcard_syntax(query)
        ):
            return matchers
        raw = (
            QueryToken(
                query if case_sensitive else normalize_token(query, language),
                "exact",
                case_sensitive,
            ),
        )
        if raw == matchers:
            return matchers
        if self._count_matches(connection, language, raw, pos=pos) > 0:
            return raw
        return matchers

    def _require_artifacts(self) -> None:
        required = (
            self.index_path,
            self.processed_dir / "documents.jsonl",
            self.processed_dir / "paragraphs.jsonl",
            self.processed_dir / "sentences.jsonl",
        )
        if not all(path.is_file() for path in required):
            raise KwicIndexUnavailable("该语料库尚未生成可用的 KWIC 索引。")

    @staticmethod
    def _require_index_schema(connection: sqlite3.Connection) -> None:
        token_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(tokens)")
        }
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_columns = {
            "stream_position",
            "document_start",
            "document_end",
        }
        required_tables = {"documents", "document_streams"}
        if not required_columns.issubset(token_columns) or not required_tables.issubset(tables):
            raise KwicIndexUnavailable("KWIC 索引结构不兼容。")

    @staticmethod
    def _match_sql(
        matchers: tuple[QueryToken, ...],
        *,
        count: bool,
        sort_keys: tuple[str, ...] = (),
        sort_order: str = "value",
        pos: str = "",
        include_order: bool = True,
        paginated: bool = True,
    ) -> tuple[str, list[Any]]:
        aliases = [f"t{index}" for index in range(len(matchers))]
        select = "COUNT(*)" if count else (
            "t0.global_position, t0.stream_position, t0.sentence_id, t0.document_id, "
            f"t0.sentence_position, t0.language, t0.document_start, "
            f"{aliases[-1]}.document_end, "
            + ", ".join(f"{alias}.surface" for alias in aliases)
        )
        joins = " ".join(
            f"JOIN tokens {alias} ON {alias}.document_id = t0.document_id "
            f"AND {alias}.language = t0.language "
            f"AND {alias}.stream_position = t0.stream_position + {index}"
            for index, alias in enumerate(aliases[1:], start=1)
        )
        sort_joins = ""
        order_expressions: list[str] = []
        if not count and sort_keys:
            sort_joins, order_expressions = _sort_clauses(
                sort_keys,
                len(matchers),
                order_by_frequency=sort_order == "frequency",
            )
            joins = f"{joins} {sort_joins}".strip()

        predicates = ["t0.language = ?"]
        parameters: list[Any] = []
        for alias, matcher in zip(aliases, matchers, strict=True):
            predicate, values = _matcher_predicate(alias, matcher)
            predicates.append(predicate)
            parameters.extend(values)
        if pos:
            predicates.append("t0.pos = ?")
            parameters.append(pos)
        sql = f"SELECT {select} FROM tokens t0 {joins} WHERE {' AND '.join(predicates)}"
        if not count and include_order:
            if order_expressions:
                sql += " ORDER BY " + ", ".join([*order_expressions, "t0.global_position"])
            else:
                sql += " ORDER BY t0.global_position"
        if not count and paginated:
            sql += " LIMIT ? OFFSET ?"
        return sql, parameters

    def _count_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        matchers: tuple[QueryToken, ...],
        *,
        pos: str = "",
    ) -> int:
        sql, term_params = self._match_sql(matchers, count=True, pos=pos)
        row = connection.execute(sql, [language, *term_params]).fetchone()
        return int(row[0]) if row else 0

    def _page_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        matchers: tuple[QueryToken, ...],
        *,
        page: int,
        page_size: int,
        sort_keys: tuple[str, ...],
        sort_order: str,
        pos: str,
    ) -> list[KwicMatch]:
        sql, term_params = self._match_sql(
            matchers,
            count=False,
            sort_keys=sort_keys,
            sort_order=sort_order,
            pos=pos,
        )
        rows = connection.execute(
            sql,
            [language, *term_params, page_size, (page - 1) * page_size],
        ).fetchall()
        term_count = len(matchers)
        return [
            KwicMatch(
                global_position=int(row[0]),
                stream_position=int(row[1]),
                sentence_id=str(row[2]),
                document_id=str(row[3]),
                sentence_position=int(row[4]),
                language=str(row[5]),
                document_start=int(row[6]),
                document_end=int(row[7]),
                keyword_surfaces=tuple(str(value) for value in row[8 : 8 + term_count]),
            )
            for row in rows
        ]

    def _all_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        matchers: tuple[QueryToken, ...],
        *,
        pos: str,
    ) -> list[KwicMatch]:
        sql, term_params = self._match_sql(
            matchers,
            count=False,
            pos=pos,
            paginated=False,
        )
        rows = connection.execute(sql, [language, *term_params]).fetchall()
        term_count = len(matchers)
        return [
            KwicMatch(
                global_position=int(row[0]),
                stream_position=int(row[1]),
                sentence_id=str(row[2]),
                document_id=str(row[3]),
                sentence_position=int(row[4]),
                language=str(row[5]),
                document_start=int(row[6]),
                document_end=int(row[7]),
                keyword_surfaces=tuple(str(value) for value in row[8 : 8 + term_count]),
            )
            for row in rows
        ]

    def _sample_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        matchers: tuple[QueryToken, ...],
        *,
        sample_size: int,
        sample_seed: int,
        sort_keys: tuple[str, ...],
        sort_order: str,
        pos: str,
    ) -> list[KwicMatch]:
        sql, term_params = self._match_sql(
            matchers,
            count=False,
            pos=pos,
            include_order=False,
            paginated=False,
        )
        rows = connection.execute(
            sql
            + " ORDER BY (((t0.global_position * 1103515245) + ?) & 2147483647) "
            + "LIMIT ?",
            [language, *term_params, sample_seed, sample_size],
        ).fetchall()
        term_count = len(matchers)
        matches = [
            KwicMatch(
                global_position=int(row[0]),
                stream_position=int(row[1]),
                sentence_id=str(row[2]),
                document_id=str(row[3]),
                sentence_position=int(row[4]),
                language=str(row[5]),
                document_start=int(row[6]),
                document_end=int(row[7]),
                keyword_surfaces=tuple(str(value) for value in row[8 : 8 + term_count]),
            )
            for row in rows
        ]
        if not sort_keys:
            return matches
        context = self._context_tokens(connection, matches, radius=5)
        filenames = dict(
            connection.execute("SELECT document_id, filename FROM documents").fetchall()
        )
        patterns = Counter(
            _python_sort_pattern(
                item,
                sort_keys=sort_keys,
                context_tokens=context,
                filenames=filenames,
            )
            for item in matches
        )
        matches.sort(
            key=lambda item: (
                -patterns[
                    _python_sort_pattern(
                        item,
                        sort_keys=sort_keys,
                        context_tokens=context,
                        filenames=filenames,
                    )
                ]
                if sort_order == "frequency"
                else 0,
                *_python_sort_key(
                    item,
                    sort_keys=sort_keys,
                    context_tokens=context,
                    filenames=filenames,
                ),
            )
        )
        return matches

    @staticmethod
    def _context_tokens(
        connection: sqlite3.Connection,
        matches: list[KwicMatch],
        *,
        radius: int,
    ) -> dict[tuple[str, str, int], str]:
        selected: dict[tuple[str, str, int], str] = {}
        for match in matches:
            keyword_length = match.token_length
            rows = connection.execute(
                """
                SELECT stream_position, surface
                FROM tokens
                WHERE document_id = ?
                  AND language = ?
                  AND stream_position BETWEEN ? AND ?
                ORDER BY stream_position
                """,
                (
                    match.document_id,
                    match.language,
                    max(1, match.stream_position - radius),
                    match.stream_position + keyword_length - 1 + radius,
                ),
            ).fetchall()
            for position, surface in rows:
                selected[(match.document_id, match.language, int(position))] = str(surface)
        return selected

    def _metadata_for(self, matches: list[KwicMatch]) -> dict[str, dict[str, Any]]:
        sentence_ids = {match.sentence_id for match in matches}
        document_ids = {match.document_id for match in matches}
        sentences = _select_jsonl(self.processed_dir / "sentences.jsonl", sentence_ids)
        documents = _select_jsonl(self.processed_dir / "documents.jsonl", document_ids)
        paragraph_ids = {
            str(record.get("paragraph_id", "")) for record in sentences.values() if record
        }
        paragraphs = _select_jsonl(self.processed_dir / "paragraphs.jsonl", paragraph_ids)
        return {
            "sentences": sentences,
            "documents": documents,
            "paragraphs": paragraphs,
        }

    @staticmethod
    def _source_snippets(
        connection: sqlite3.Connection,
        matches: list[KwicMatch],
        *,
        context_size: int,
    ) -> dict[int, tuple[str, str, str]]:
        streams: dict[tuple[str, str], str] = {}
        snippets: dict[int, tuple[str, str, str]] = {}
        for match in matches:
            stream_key = (match.document_id, match.language)
            if stream_key not in streams:
                row = connection.execute(
                    """
                    SELECT text FROM document_streams
                    WHERE document_id = ? AND language = ?
                    """,
                    stream_key,
                ).fetchone()
                streams[stream_key] = str(row[0]) if row else ""
            text = streams[stream_key]
            left_position = max(1, match.stream_position - context_size)
            right_position = match.stream_position + match.token_length + context_size - 1
            left_row = connection.execute(
                """
                SELECT document_start FROM tokens
                WHERE document_id = ? AND language = ? AND stream_position = ?
                """,
                (*stream_key, left_position),
            ).fetchone()
            right_row = connection.execute(
                """
                SELECT document_end FROM tokens
                WHERE document_id = ? AND language = ? AND stream_position = ?
                """,
                (*stream_key, right_position),
            ).fetchone()
            left_start = int(left_row[0]) if left_row else 0
            right_end = int(right_row[0]) if right_row else len(text)
            snippets[match.global_position] = (
                _display_fragment(text[left_start : match.document_start], match.language),
                _display_fragment(text[match.document_start : match.document_end], match.language),
                _display_fragment(text[match.document_end : right_end], match.language),
            )
        return snippets

    @staticmethod
    def _build_hit(
        match: KwicMatch,
        context_tokens: dict[tuple[str, str, int], str],
        metadata: dict[str, dict[str, Any]],
        context_size: int,
        *,
        source_snippets: dict[int, tuple[str, str, str]] | None = None,
        kpf_count: int = 1,
    ) -> KwicHit:
        keyword_length = match.token_length

        def token_at(offset: int) -> str:
            return context_tokens.get(
                (match.document_id, match.language, match.stream_position + offset),
                "",
            )

        left_values = [
            token_at(offset)
            for offset in range(-context_size, 0)
            if token_at(offset)
        ]
        right_values = [
            token_at(offset)
            for offset in range(keyword_length, keyword_length + context_size)
            if token_at(offset)
        ]
        separator = "" if match.language == "zh" else " "
        sentence = metadata["sentences"].get(match.sentence_id, {})
        document = metadata["documents"].get(match.document_id, {})
        paragraph = metadata["paragraphs"].get(str(sentence.get("paragraph_id", "")), {})
        fallback = (
            separator.join(left_values),
            match.keyword_text or separator.join(match.keyword_surfaces),
            separator.join(right_values),
        )
        left, keyword, right = (source_snippets or {}).get(
            match.global_position,
            fallback,
        )
        return KwicHit(
            left=left,
            keyword=keyword,
            right=right,
            source_filename=str(document.get("filename", "")),
            document_id=match.document_id,
            sentence_id=match.sentence_id,
            sentence_ordinal=_safe_int(sentence.get("ordinal")),
            paragraph_ordinal=_safe_int(paragraph.get("ordinal")),
            language=match.language,
            l5=token_at(-5),
            l4=token_at(-4),
            l3=token_at(-3),
            l2=token_at(-2),
            l1=token_at(-1),
            r1=token_at(keyword_length),
            r2=token_at(keyword_length + 1),
            r3=token_at(keyword_length + 2),
            r4=token_at(keyword_length + 3),
            r5=token_at(keyword_length + 4),
            row_id=match.global_position,
            kpf_count=kpf_count,
        )


def compile_query(
    query: str,
    *,
    language: str | None,
    whole_words: bool,
    case_sensitive: bool,
    regex: bool,
) -> tuple[str, tuple[QueryToken, ...]]:
    detected = "zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"
    language = language or detected
    if language not in {"zh", "en"}:
        raise KwicQueryError("查询语言必须是中文或英文。")
    if len(query) > 500:
        raise KwicQueryError("查询表达式不能超过 500 个字符。")

    if regex:
        raw_terms = tuple(query.split())
        if not raw_terms:
            raise KwicQueryError("正则表达式不能为空。")
        matchers = tuple(
            QueryToken(term, "regex", case_sensitive, full_match=whole_words)
            for term in raw_terms
        )
        for term in raw_terms:
            _validate_regex(term, case_sensitive=case_sensitive)
    else:
        raw_terms = _plain_query_terms(query, language)
        matchers = tuple(
            _plain_matcher(
                term,
                language=language,
                whole_words=whole_words,
                case_sensitive=case_sensitive,
            )
            for term in raw_terms
        )
    if len(matchers) > MAX_QUERY_TERMS:
        raise KwicQueryError(f"查询最多包含 {MAX_QUERY_TERMS} 个 Token。")
    return language, matchers


def validate_full_regex(query: str, *, case_sensitive: bool = False) -> None:
    if not query or not query.strip():
        raise KwicQueryError("全文正则表达式不能为空。")
    _validate_regex(query, case_sensitive=case_sensitive, max_length=500)


def query_terms(query: str) -> tuple[str, tuple[str, ...]]:
    """Backward-compatible tokenization helper used by forms and tests."""
    language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"
    terms = tuple(
        normalize_token(match.group(0), language) for match in token_matches(query, language)
    )
    if not terms:
        raise ValueError("Query must contain at least one searchable token.")
    if len(terms) > MAX_QUERY_TERMS:
        raise ValueError(f"Query must contain no more than {MAX_QUERY_TERMS} tokens.")
    return language, terms


def normalize_sort_keys(
    sort_keys: Sequence[str] | None,
    *,
    fallback: str = "",
) -> tuple[str, ...]:
    values = list(sort_keys or ())
    if not values and fallback:
        values = [fallback]
    normalized: list[str] = []
    for value in values:
        item = str(value).strip().upper()
        if not item:
            continue
        if item not in SORT_FIELDS:
            raise ValueError(f"sort key must be one of: {', '.join(SORT_FIELDS)}.")
        if item not in normalized:
            normalized.append(item)
    if len(normalized) > MAX_SORT_KEYS:
        raise ValueError(f"最多只能设置 {MAX_SORT_KEYS} 个排序层级。")
    return tuple(normalized)


def normalize_sort_order(value: str) -> str:
    normalized = str(value or "value").strip().lower()
    if normalized not in {"value", "frequency"}:
        raise ValueError("sort_order must be value or frequency.")
    return normalized


def sort_offset(sort_by: str, keyword_length: int) -> int:
    if sort_by == "C":
        return 0
    distance = int(sort_by[1:])
    return -distance if sort_by.startswith("L") else keyword_length + distance - 1


def _plain_query_terms(query: str, language: str) -> tuple[str, ...]:
    if _has_wildcard_syntax(query):
        terms = tuple(query.split())
    else:
        terms = tuple(match.group(0) for match in token_matches(query, language))
    if not terms:
        raise KwicQueryError("查询中没有可检索的 Token。")
    return terms


def _plain_matcher(
    value: str,
    *,
    language: str,
    whole_words: bool,
    case_sensitive: bool,
) -> QueryToken:
    if _has_wildcard_syntax(value):
        pattern = _wildcard_pattern(value)
        _validate_regex(pattern, case_sensitive=case_sensitive)
        return QueryToken(pattern, "regex", case_sensitive, full_match=whole_words)
    normalized = value if case_sensitive else normalize_token(value, language)
    return QueryToken(
        normalized,
        "exact" if whole_words else "contains",
        case_sensitive,
    )


def _has_wildcard_syntax(value: str) -> bool:
    return "*" in value or "?" in value or "|" in value or (
        value.startswith("[") and value.endswith("]") and "," in value
    )


def _wildcard_pattern(value: str) -> str:
    if value.startswith("[") and value.endswith("]") and "," in value:
        options = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        if not options:
            raise KwicQueryError("通配列表不能为空。")
        return "(?:" + "|".join(safe_regex.escape(item) for item in options) + ")"
    if "|" in value:
        options = [item for item in value.split("|") if item]
        if len(options) < 2:
            raise KwicQueryError("备选词语法无效。")
        return "(?:" + "|".join(_wildcard_pattern(item) for item in options) + ")"
    pieces: list[str] = []
    for character in value:
        if character == "*":
            pieces.append(".*")
        elif character == "?":
            pieces.append(".")
        else:
            pieces.append(safe_regex.escape(character))
    if not any(character not in "*?" for character in value):
        raise KwicQueryError("通配符必须至少包含一个普通字符。")
    return "".join(pieces)


def _validate_regex(
    pattern: str,
    *,
    case_sensitive: bool,
    max_length: int = MAX_REGEX_LENGTH,
) -> None:
    if len(pattern) > max_length:
        raise KwicQueryError(f"正则表达式不能超过 {max_length} 个字符。")
    try:
        safe_regex.compile(
            pattern,
            safe_regex.VERSION1 | (0 if case_sensitive else safe_regex.IGNORECASE),
        )
    except safe_regex.error as exc:
        raise KwicQueryError(f"正则表达式无效：{exc}") from exc


def _register_regex_function(connection: sqlite3.Connection) -> None:
    cache: dict[tuple[str, bool], safe_regex.Pattern] = {}
    state = {"timed_out": False}

    def token_regex(pattern: str, value: str, case_sensitive: int, full_match: int) -> int:
        key = (str(pattern), bool(case_sensitive))
        compiled = cache.get(key)
        if compiled is None:
            compiled = safe_regex.compile(
                key[0],
                safe_regex.VERSION1 | (0 if key[1] else safe_regex.IGNORECASE),
            )
            cache[key] = compiled
        try:
            matched = (
                compiled.fullmatch(value, timeout=REGEX_TIMEOUT_SECONDS)
                if full_match
                else compiled.search(value, timeout=REGEX_TIMEOUT_SECONDS)
            )
        except TimeoutError:
            state["timed_out"] = True
            return 0
        return int(matched is not None)

    connection.create_function("TOKEN_REGEX", 4, token_regex, deterministic=True)
    connection.create_function(
        "TOKEN_REGEX_TIMED_OUT",
        0,
        lambda: int(state["timed_out"]),
    )


def _raise_if_regex_timed_out(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT TOKEN_REGEX_TIMED_OUT()").fetchone()
    if row and row[0]:
        raise KwicQueryError("正则表达式执行超时，请缩小表达式范围。")


def _matcher_predicate(alias: str, matcher: QueryToken) -> tuple[str, list[Any]]:
    expression = f"{alias}.surface" if matcher.case_sensitive else f"{alias}.normalized"
    if matcher.operator == "exact":
        return f"{expression} = ? COLLATE BINARY", [matcher.value]
    if matcher.operator == "contains":
        return f"instr({expression}, ?) > 0", [matcher.value]
    if matcher.operator == "regex":
        return (
            f"TOKEN_REGEX(?, {alias}.surface, ?, ?) = 1",
            [matcher.value, int(matcher.case_sensitive), int(matcher.full_match)],
        )
    raise ValueError("Unsupported query matcher.")


def _sort_clauses(
    sort_keys: tuple[str, ...],
    keyword_length: int,
    *,
    order_by_frequency: bool = False,
) -> tuple[str, list[str]]:
    joins: list[str] = []
    order: list[str] = []
    expressions: list[str] = []
    needs_document = any(key == "FILE" for key in sort_keys)
    if needs_document:
        joins.append("LEFT JOIN documents kwic_doc ON kwic_doc.document_id = t0.document_id")
    for index, key in enumerate(sort_keys):
        if key in {"FILE", "FILE_ID", "ROW_ID"}:
            expression = {
                "FILE": "kwic_doc.filename",
                "FILE_ID": "t0.document_id",
                "ROW_ID": "t0.global_position",
            }[key]
        elif key == "C":
            expression = "t0.normalized"
        else:
            alias = f"sort_{index}"
            offset = sort_offset(key, keyword_length)
            joins.append(
                f"LEFT JOIN tokens {alias} ON {alias}.document_id = t0.document_id "
                f"AND {alias}.language = t0.language "
                f"AND {alias}.stream_position = t0.stream_position + {offset}"
            )
            expression = f"{alias}.normalized"
        expressions.append(expression)
        order.append(f"CASE WHEN {expression} IS NULL THEN 1 ELSE 0 END")
        order.append(
            f"{expression}" if key == "ROW_ID" else f"{expression} COLLATE NOCASE"
        )
    if order_by_frequency and expressions:
        order.insert(
            0,
            f"COUNT(*) OVER (PARTITION BY {', '.join(expressions)}) DESC",
        )
    return " ".join(joins), order


def _python_sort_key(
    match: KwicMatch,
    *,
    sort_keys: tuple[str, ...],
    context_tokens: dict[tuple[str, str, int], str],
    filenames: dict[str, str],
) -> tuple[Any, ...]:
    if not sort_keys:
        return (match.global_position,)

    def token_at(offset: int) -> str:
        return context_tokens.get(
            (match.document_id, match.language, match.stream_position + offset),
            "",
        )

    values: list[Any] = []
    for key in sort_keys:
        if key == "ROW_ID":
            values.append((0, match.global_position))
            continue
        if key == "FILE":
            value = filenames.get(match.document_id, "")
        elif key == "FILE_ID":
            value = match.document_id
        elif key == "C":
            value = match.keyword_text or " ".join(match.keyword_surfaces)
        else:
            value = token_at(sort_offset(key, match.token_length))
        values.append((int(not value), value.casefold()))
    return (*values, match.global_position)


def _python_sort_pattern(
    match: KwicMatch,
    *,
    sort_keys: tuple[str, ...],
    context_tokens: dict[tuple[str, str, int], str],
    filenames: dict[str, str],
) -> tuple[Any, ...]:
    if not sort_keys:
        return ()

    def token_at(offset: int) -> str:
        return context_tokens.get(
            (match.document_id, match.language, match.stream_position + offset),
            "",
        )

    values: list[Any] = []
    for key in sort_keys:
        if key == "ROW_ID":
            value: Any = match.global_position
        elif key == "FILE":
            value = filenames.get(match.document_id, "").casefold()
        elif key == "FILE_ID":
            value = match.document_id
        elif key == "C":
            value = (match.keyword_text or " ".join(match.keyword_surfaces)).casefold()
        else:
            value = token_at(sort_offset(key, match.token_length)).casefold()
        values.append(value)
    return tuple(values)


def _hit_pattern(
    match: KwicMatch,
    sort_keys: tuple[str, ...],
    context_tokens: dict[tuple[str, str, int], str],
) -> tuple[Any, ...]:
    if not sort_keys:
        return (match.global_position,)

    def token_at(offset: int) -> str:
        return context_tokens.get(
            (match.document_id, match.language, match.stream_position + offset),
            "",
        ).casefold()

    pattern: list[Any] = []
    for key in sort_keys:
        if key in {"FILE", "FILE_ID"}:
            value: Any = match.document_id
        elif key == "ROW_ID":
            value = match.global_position
        elif key == "C":
            value = (match.keyword_text or " ".join(match.keyword_surfaces)).casefold()
        else:
            value = token_at(sort_offset(key, match.token_length))
        pattern.append(value)
    return tuple(pattern)


def _sort_matches_in_memory(
    matches: list[KwicMatch],
    sort_keys: tuple[str, ...],
    sort_order: str,
    context_tokens: dict[tuple[str, str, int], str],
    filenames: dict[str, str],
) -> None:
    patterns = Counter(
        _python_sort_pattern(
            item,
            sort_keys=sort_keys,
            context_tokens=context_tokens,
            filenames=filenames,
        )
        for item in matches
    )
    matches.sort(
        key=lambda item: (
            -patterns[
                _python_sort_pattern(
                    item,
                    sort_keys=sort_keys,
                    context_tokens=context_tokens,
                    filenames=filenames,
                )
            ]
            if sort_order == "frequency" and sort_keys
            else 0,
            *_python_sort_key(
                item,
                sort_keys=sort_keys,
                context_tokens=context_tokens,
                filenames=filenames,
            ),
        )
    )


def _matches_context_constraints(
    match: KwicMatch,
    context_tokens: dict[tuple[str, str, int], str],
    matchers: Sequence[QueryToken],
    *,
    logic: str,
    window_from: int,
    window_to: int,
    exclude: bool,
) -> bool:
    values: list[str] = []
    for position in range(window_from, window_to + 1):
        if position == 0:
            continue
        offset = position if position < 0 else match.token_length + position - 1
        value = context_tokens.get(
            (match.document_id, match.language, match.stream_position + offset),
            "",
        )
        if value:
            values.append(value)
    outcomes = [any(_matcher_accepts(matcher, value) for value in values) for matcher in matchers]
    accepted = all(outcomes) if logic == "and" else any(outcomes)
    return not accepted if exclude else accepted


def _matcher_accepts(matcher: QueryToken, value: str) -> bool:
    candidate = value if matcher.case_sensitive else value.casefold()
    expected = matcher.value if matcher.case_sensitive else matcher.value.casefold()
    if matcher.operator == "exact":
        return candidate == expected
    if matcher.operator == "contains":
        return expected in candidate
    if matcher.operator == "regex":
        compiled = safe_regex.compile(
            matcher.value,
            safe_regex.VERSION1
            | (0 if matcher.case_sensitive else safe_regex.IGNORECASE),
        )
        try:
            found = (
                compiled.fullmatch(value, timeout=REGEX_TIMEOUT_SECONDS)
                if matcher.full_match
                else compiled.search(value, timeout=REGEX_TIMEOUT_SECONDS)
            )
        except TimeoutError as exc:
            raise KwicQueryError("语境词正则表达式执行超时。") from exc
        return found is not None
    raise ValueError("unsupported context matcher")


def _validate_page_options(context_size: int, page: int, page_size: int) -> None:
    if not 0 <= context_size <= MAX_CONTEXT_SIZE:
        raise ValueError(f"context_size must be between 0 and {MAX_CONTEXT_SIZE}.")
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")


def _display_fragment(value: str, language: str) -> str:
    if language == "zh":
        return re.sub(r"\s+", "", value)
    return " ".join(value.split())


def _select_jsonl(path: Path, wanted_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not wanted_ids:
        return {}
    selected: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise KwicIndexCorrupt(f"索引元数据损坏：{path.name}:{line_number}") from exc
                record_id = str(record.get("id", ""))
                if record_id in wanted_ids:
                    selected[record_id] = record
                    if len(selected) == len(wanted_ids):
                        break
    except OSError as exc:
        raise KwicIndexUnavailable(f"无法读取索引元数据：{path.name}") from exc
    return selected


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
