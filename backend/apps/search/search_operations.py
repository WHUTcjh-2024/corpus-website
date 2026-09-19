from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import Counter
from contextlib import closing
from typing import Sequence

import regex as safe_regex

from .contracts import (
    FileView,
    FileViewSegment,
    KwicIndexCorrupt,
    KwicMatch,
    KwicPage,
    KwicQueryError,
    QueryToken,
)
from .query import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_PAGE_SIZE,
    REGEX_TIMEOUT_SECONDS,
    _hit_pattern,
    _matches_context_constraints,
    _raise_if_regex_timed_out,
    _register_regex_function,
    _sort_matches_in_memory,
    _validate_page_options,
    _validate_regex,
    compile_query,
    normalize_sort_keys,
    normalize_sort_order,
    validate_full_regex,
)


class KwicSearchOperationsMixin:
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
                        (int(found[6]), int(found[7]), int(found[0])) for found in rows
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
            segments.append(
                FileViewSegment(text[start:end], True, hit_row_id, selected)
            )
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
                            (item.global_position * 1103515245 + sample_seed)
                            & 2147483647
                        )
                    )
                    matches = matches[:sample_size]
                sorting_context = self._context_tokens(connection, matches, radius=5)
                filenames = dict(
                    connection.execute(
                        "SELECT document_id, filename FROM documents"
                    ).fetchall()
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
            "zh"
            if any("\u4e00" <= character <= "\u9fff" for character in query)
            else "en"
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
                    _hit_pattern(match, sort_keys, sorting_context) for match in matches
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
