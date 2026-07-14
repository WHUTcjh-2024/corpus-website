from __future__ import annotations

import math
import sqlite3
from contextlib import closing

from .filters import TokenFilter, compile_token_filter
from .kwic import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_PAGE_SIZE,
    MAX_CONTEXT_SIZE,
    MAX_PAGE_SIZE,
    SORT_FIELDS,
    KwicIndexCorrupt,
    KwicMatch,
    KwicPage,
    KwicSearchEngine,
    sort_offset,
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
        pos: str = "",
    ) -> KwicPage:
        plan = parse_query(query, language=language)
        sort_by, pos = _validate_options(
            context_size=context_size,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            pos=pos,
        )
        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                total = self._count_plan(connection, plan, pos=pos)
                num_pages = max(1, math.ceil(total / page_size))
                effective_page = min(page, num_pages)
                matches = self._page_plan(
                    connection,
                    plan,
                    page=effective_page,
                    page_size=page_size,
                    sort_by=sort_by,
                    pos=pos,
                )
                token_rows = self._sentence_tokens(
                    connection,
                    {match.sentence_id for match in matches},
                )
        except sqlite3.Error as exc:
            raise KwicIndexCorrupt("复杂查询索引无法读取，请重新加工该语料库。") from exc

        metadata = self._metadata_for(matches)
        hits = tuple(
            self._build_hit(
                match,
                token_rows.get(match.sentence_id, ()),
                metadata,
                context_size,
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
            sort_by=sort_by,
            pos=pos,
        )

    @staticmethod
    def _match_sql(
        plan: QueryPlan,
        *,
        count: bool,
        sort_by: str = "",
        pos: str = "",
    ) -> tuple[str, list[str]]:
        aliases = [f"t{index}" for index in range(len(plan.filters))]
        select = "COUNT(*)" if count else (
            "t0.sentence_id, t0.document_id, t0.sentence_position, t0.language, "
            + ", ".join(f"{alias}.surface" for alias in aliases)
        )
        joins = " ".join(
            f"JOIN tokens {alias} ON {alias}.sentence_id = t0.sentence_id "
            f"AND {alias}.sentence_position = t0.sentence_position + {index}"
            for index, alias in enumerate(aliases[1:], start=1)
        )
        if sort_by:
            offset = sort_offset(sort_by, len(plan.filters))
            joins += (
                " LEFT JOIN tokens sort_token ON sort_token.sentence_id = t0.sentence_id "
                f"AND sort_token.sentence_position = t0.sentence_position + {offset}"
            )
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
            if sort_by:
                sql += (
                    " ORDER BY CASE WHEN sort_token.normalized IS NULL THEN 1 ELSE 0 END, "
                    "sort_token.normalized COLLATE NOCASE, t0.global_position"
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
        sort_by: str,
        pos: str,
    ) -> list[KwicMatch]:
        sql, parameters = self._match_sql(
            plan,
            count=False,
            sort_by=sort_by,
            pos=pos,
        )
        rows = connection.execute(
            sql,
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()
        filter_count = len(plan.filters)
        return [
            KwicMatch(
                sentence_id=str(row[0]),
                document_id=str(row[1]),
                sentence_position=int(row[2]),
                language=str(row[3]),
                keyword_surfaces=tuple(str(value) for value in row[4 : 4 + filter_count]),
            )
            for row in rows
        ]


def _validate_options(
    *,
    context_size: int,
    page: int,
    page_size: int,
    sort_by: str,
    pos: str,
) -> tuple[str, str]:
    if not 0 <= context_size <= MAX_CONTEXT_SIZE:
        raise ValueError(f"context_size must be between 0 and {MAX_CONTEXT_SIZE}.")
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
    normalized_sort = sort_by.strip().upper()
    if normalized_sort and normalized_sort not in SORT_FIELDS:
        raise ValueError(f"sort_by must be one of: {', '.join(SORT_FIELDS)}.")
    return normalized_sort, pos.strip()
