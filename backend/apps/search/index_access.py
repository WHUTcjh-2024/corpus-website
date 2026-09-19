from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from apps.processing.text import normalize_token

from .contracts import KwicHit, KwicIndexUnavailable, KwicMatch, QueryToken
from .query import (
    _display_fragment,
    _has_wildcard_syntax,
    _matcher_predicate,
    _python_sort_key,
    _python_sort_pattern,
    _safe_int,
    _select_jsonl,
    _sort_clauses,
)


class KwicIndexAccessMixin:
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
        if not required_columns.issubset(token_columns) or not required_tables.issubset(
            tables
        ):
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
        select = (
            "COUNT(*)"
            if count
            else (
                "t0.global_position, t0.stream_position, t0.sentence_id, t0.document_id, "
                f"t0.sentence_position, t0.language, t0.document_start, "
                f"{aliases[-1]}.document_end, "
                + ", ".join(f"{alias}.surface" for alias in aliases)
            )
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
                sql += " ORDER BY " + ", ".join(
                    [*order_expressions, "t0.global_position"]
                )
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
                selected[(match.document_id, match.language, int(position))] = str(
                    surface
                )
        return selected

    def _metadata_for(self, matches: list[KwicMatch]) -> dict[str, dict[str, Any]]:
        sentence_ids = {match.sentence_id for match in matches}
        document_ids = {match.document_id for match in matches}
        sentences = _select_jsonl(self.processed_dir / "sentences.jsonl", sentence_ids)
        documents = _select_jsonl(self.processed_dir / "documents.jsonl", document_ids)
        paragraph_ids = {
            str(record.get("paragraph_id", ""))
            for record in sentences.values()
            if record
        }
        paragraphs = _select_jsonl(
            self.processed_dir / "paragraphs.jsonl", paragraph_ids
        )
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
            right_position = (
                match.stream_position + match.token_length + context_size - 1
            )
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
                _display_fragment(
                    text[left_start : match.document_start], match.language
                ),
                _display_fragment(
                    text[match.document_start : match.document_end], match.language
                ),
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
            token_at(offset) for offset in range(-context_size, 0) if token_at(offset)
        ]
        right_values = [
            token_at(offset)
            for offset in range(keyword_length, keyword_length + context_size)
            if token_at(offset)
        ]
        separator = "" if match.language == "zh" else " "
        sentence = metadata["sentences"].get(match.sentence_id, {})
        document = metadata["documents"].get(match.document_id, {})
        paragraph = metadata["paragraphs"].get(
            str(sentence.get("paragraph_id", "")), {}
        )
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
