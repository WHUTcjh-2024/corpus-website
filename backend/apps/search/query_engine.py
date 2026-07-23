from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from contextlib import closing

from .filters import compile_token_filter
from .kwic import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_PAGE_SIZE,
    MAX_CONTEXT_SIZE,
    MAX_PAGE_SIZE,
    KwicIndexCorrupt,
    KwicMatch,
    KwicPage,
    KwicSearchEngine,
    _sort_clauses,
    normalize_sort_keys,
    normalize_sort_order,
)
from .query_parser import QueryPlan, parse_query


class ComplexQueryEngine(KwicSearchEngine):
    """Execute the platform's safe, documented CQP-style query subset."""

    def search(
        self,
        query: str,
        *,
        language: str,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        sort_by: str = "",
        sort_keys: Sequence[str] | None = None,
        sort_order: str = "value",
        pos: str = "",
    ) -> KwicPage:
        plan = parse_query(query, language=language)
        normalized_sort_keys, sort_order, pos = _validate_options(
            context_size=context_size,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_keys=sort_keys,
            sort_order=sort_order,
            pos=pos,
        )
        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                self._require_index_schema(connection)
                total = self._count_plan(connection, plan, pos=pos)
                num_pages = max(1, math.ceil(total / page_size))
                effective_page = min(page, num_pages)
                matches = self._page_plan(
                    connection,
                    plan,
                    page=effective_page,
                    page_size=page_size,
                    sort_keys=normalized_sort_keys,
                    sort_order=sort_order,
                    pos=pos,
                )
                token_rows = self._context_tokens(
                    connection,
                    matches,
                    radius=max(context_size, 5),
                )
                source_snippets = self._source_snippets(
                    connection,
                    matches,
                    context_size=context_size,
                )
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("复杂查询索引读取失败。") from exc

        metadata = self._metadata_for(matches)
        hits = tuple(
            self._build_hit(
                match,
                token_rows,
                metadata,
                context_size,
                source_snippets=source_snippets,
            )
            for match in matches
        )
        return KwicPage(
            query=plan.source,
            hits=hits,
            total=total,
            page=effective_page,
            page_size=page_size,
            context_size=context_size,
            sort_by=normalized_sort_keys[0] if normalized_sort_keys else "",
            sort_keys=normalized_sort_keys,
            sort_order=sort_order,
            pos=pos,
        )

    @staticmethod
    def _match_sql(
        plan: QueryPlan,
        *,
        count: bool,
        sort_keys: tuple[str, ...] = (),
        sort_order: str = "value",
        pos: str = "",
    ) -> tuple[str, list[str]]:
        aliases = [f"t{index}" for index in range(len(plan.filters))]
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
        order_expressions: list[str] = []
        if not count and sort_keys:
            sort_joins, order_expressions = _sort_clauses(
                sort_keys,
                len(plan.filters),
                order_by_frequency=sort_order == "frequency",
            )
            joins = f"{joins} {sort_joins}".strip()
        predicates = ["t0.language = ?"]
        parameters = [plan.language]
        for alias, token_filter in zip(aliases, plan.filters, strict=True):
            predicate, values = compile_token_filter(
                token_filter,
                alias=alias,
                language=plan.language,
            )
            predicates.append(predicate)
            parameters.extend(values)
        if pos:
            predicates.append("t0.pos = ?")
            parameters.append(pos)
        sql = f"SELECT {select} FROM tokens t0 {joins} WHERE {' AND '.join(predicates)}"
        if not count:
            if order_expressions:
                sql += " ORDER BY " + ", ".join(
                    [*order_expressions, "t0.global_position"]
                )
            else:
                sql += " ORDER BY t0.global_position"
            sql += " LIMIT ? OFFSET ?"
        return sql, parameters

    def _count_plan(
        self,
        connection: sqlite3.Connection,
        plan: QueryPlan,
        *,
        pos: str,
    ) -> int:
        sql, parameters = self._match_sql(plan, count=True, pos=pos)
        row = connection.execute(sql, parameters).fetchone()
        return int(row[0]) if row else 0

    def _page_plan(
        self,
        connection: sqlite3.Connection,
        plan: QueryPlan,
        *,
        page: int,
        page_size: int,
        sort_keys: tuple[str, ...],
        sort_order: str,
        pos: str,
    ) -> list[KwicMatch]:
        sql, parameters = self._match_sql(
            plan,
            count=False,
            sort_keys=sort_keys,
            sort_order=sort_order,
            pos=pos,
        )
        rows = connection.execute(
            sql,
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()
        filter_count = len(plan.filters)
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
                keyword_surfaces=tuple(str(value) for value in row[8 : 8 + filter_count]),
            )
            for row in rows
        ]


def _validate_options(
    *,
    context_size: int,
    page: int,
    page_size: int,
    sort_by: str,
    sort_keys: Sequence[str] | None,
    sort_order: str,
    pos: str,
) -> tuple[tuple[str, ...], str, str]:
    if not 0 <= context_size <= MAX_CONTEXT_SIZE:
        raise ValueError(f"context_size must be between 0 and {MAX_CONTEXT_SIZE}.")
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
    normalized_sort = normalize_sort_keys(sort_keys, fallback=sort_by)
    return normalized_sort, normalize_sort_order(sort_order), pos.strip()
