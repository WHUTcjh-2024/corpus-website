from __future__ import annotations

import math
import sqlite3
from contextlib import closing

from apps.search.kwic import query_terms

from .calculations import (
    _node_match_sql,
    _plot_bins,
    _positional_dispersion,
    _resolve_terms,
    _validate_language,
    _validate_page,
)
from .contracts import (
    CollocatePage,
    CollocateRow,
    ConcordancePlot,
    PlotCell,
    PlotDocument,
    StatisticsIndexCorrupt,
)
from .measures import ContingencyTable, association_measures


class ContextAnalysisMixin:
    def collocates(
        self,
        query: str,
        *,
        language: str,
        left_span: int = 5,
        right_span: int = 5,
        min_frequency: int = 2,
        min_range: int = 1,
        pos: str = "",
        sort_by: str = "log_dice",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> CollocatePage:
        _validate_language(language)
        _validate_page(page, page_size)
        query = " ".join(query.split())
        if not query:
            raise ValueError("query must not be empty.")
        if not 0 <= left_span <= 10 or not 0 <= right_span <= 10:
            raise ValueError("collocate spans must be between 0 and 10.")
        if left_span == 0 and right_span == 0:
            raise ValueError("at least one collocate span must be greater than 0.")
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        if not 1 <= min_range <= 1_000_000:
            raise ValueError("min_range must be between 1 and 1000000.")
        if sort_by not in {
            "frequency",
            "left_frequency",
            "right_frequency",
            "range",
            "mi",
            "t_score",
            "log_dice",
            "dice",
            "mi2",
            "mi3",
            "minimum_sensitivity",
            "mu",
            "rrf",
            "drf",
            "z_score",
            "log_ratio",
            "log_likelihood",
            "chi_square",
            "term",
        }:
            raise ValueError("unsupported collocate sort.")
        detected_language, default_terms = query_terms(query)
        if detected_language != language:
            raise ValueError("query language does not match the selected language.")
        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                terms = _resolve_terms(connection, query, language, default_terms)
                node_sql, node_parameters = _node_match_sql(language, terms)
                node_frequency = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM ({node_sql}) node",
                        node_parameters,
                    ).fetchone()[0]
                )
                corpus_predicates = ["language = ?"]
                corpus_parameters: list[object] = [language]
                if pos:
                    corpus_predicates.append("pos = ?")
                    corpus_parameters.append(pos)
                if not include_punctuation:
                    corpus_predicates.append("is_punctuation = 0")
                corpus_size = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM tokens WHERE {' AND '.join(corpus_predicates)}",
                        corpus_parameters,
                    ).fetchone()[0]
                )
                if node_frequency:
                    rows = connection.execute(
                        f"""
                        WITH node AS ({node_sql}),
                        context AS (
                            SELECT c.normalized, c.pos, c.surface, c.document_id,
                                   CASE WHEN c.sentence_position < node.sentence_position
                                        THEN 'left' ELSE 'right' END AS side
                            FROM node
                            JOIN tokens c ON c.sentence_id = node.sentence_id
                            WHERE (
                                c.sentence_position BETWEEN node.sentence_position - ?
                                                        AND node.sentence_position - 1
                                OR c.sentence_position BETWEEN node.sentence_position + ?
                                                        AND node.sentence_position + ?
                            )
                            AND (? = '' OR c.pos = ?)
                            AND (? = 1 OR c.is_punctuation = 0)
                        ),
                        observed AS (
                            SELECT normalized, MIN(surface) AS display,
                                   GROUP_CONCAT(DISTINCT pos) AS pos,
                                   COUNT(*) AS observed_frequency,
                                   SUM(CASE WHEN side = 'left' THEN 1 ELSE 0 END) AS left_frequency,
                                   SUM(CASE WHEN side = 'right' THEN 1 ELSE 0 END) AS right_frequency,
                                   COUNT(DISTINCT document_id) AS document_range
                            FROM context
                            GROUP BY normalized
                            HAVING COUNT(*) >= ?
                               AND COUNT(DISTINCT document_id) >= ?
                        ),
                        corpus_frequency AS (
                            SELECT normalized, COUNT(*) AS frequency
                            FROM tokens
                            WHERE language = ?
                              AND (? = '' OR pos = ?)
                              AND (? = 1 OR is_punctuation = 0)
                            GROUP BY normalized
                        )
                        SELECT observed.normalized, observed.display, observed.pos,
                               observed.observed_frequency, observed.left_frequency,
                               observed.right_frequency, observed.document_range,
                               corpus_frequency.frequency,
                               (SELECT COUNT(*) FROM context) AS context_size
                        FROM observed
                        JOIN corpus_frequency
                          ON corpus_frequency.normalized = observed.normalized
                        """,
                        [
                            *node_parameters,
                            left_span,
                            len(terms),
                            len(terms) + right_span - 1,
                            pos,
                            pos,
                            int(include_punctuation),
                            min_frequency,
                            min_range,
                            language,
                            pos,
                            pos,
                            int(include_punctuation),
                        ],
                    ).fetchall()
                else:
                    rows = []
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("搭配索引读取失败。") from exc

        calculated: list[
            tuple[str, str, int, int, int, int, int, *tuple[float, ...]]
        ] = []
        for (
            _,
            display,
            row_pos,
            observed,
            left_frequency,
            right_frequency,
            document_range,
            corpus_frequency,
            context_size,
        ) in rows:
            observed_value = int(observed)
            corpus_frequency_value = int(corpus_frequency)
            # A token can occur in multiple windows around the same node.
            # The contingency table, however, uses token-level marginals, so
            # cap the co-occurrence count before calculating association scores.
            opportunities = int(context_size)
            cooccurrence = min(observed_value, opportunities, corpus_frequency_value)
            table_size = max(
                corpus_size,
                opportunities + corpus_frequency_value - cooccurrence,
            )
            measures = association_measures(
                ContingencyTable.from_marginals(
                    cooccurrence=cooccurrence,
                    node_opportunities=opportunities,
                    collocate_frequency=corpus_frequency_value,
                    corpus_size=table_size,
                )
            )
            calculated.append(
                (
                    str(display),
                    str(row_pos),
                    observed_value,
                    int(left_frequency),
                    int(right_frequency),
                    int(document_range),
                    corpus_frequency_value,
                    measures["mi"],
                    measures["t_score"],
                    measures["log_dice"],
                    measures["dice"],
                    measures["mi2"],
                    measures["mi3"],
                    measures["minimum_sensitivity"],
                    measures["mu"],
                    measures["rrf"],
                    measures["drf"],
                    measures["z_score"],
                    measures["log_ratio"],
                    measures["log_likelihood"],
                    measures["chi_square"],
                    measures["p_value"],
                )
            )
        sort_index = {
            "left_frequency": 3,
            "right_frequency": 4,
            "range": 5,
            "mi": 7,
            "t_score": 8,
            "log_dice": 9,
            "dice": 10,
            "mi2": 11,
            "mi3": 12,
            "minimum_sensitivity": 13,
            "mu": 14,
            "rrf": 15,
            "drf": 16,
            "z_score": 17,
            "log_ratio": 18,
            "log_likelihood": 19,
            "chi_square": 20,
        }.get(sort_by)
        if sort_by == "frequency":
            calculated.sort(key=lambda row: (-row[2], row[0].casefold(), row[1]))
        elif sort_by == "term":
            calculated.sort(key=lambda row: (row[0].casefold(), row[1], -row[2]))
        else:
            calculated.sort(
                key=lambda row: (-row[sort_index], -row[2], row[0].casefold())  # type: ignore[index]
            )
        total_types = len(calculated)
        num_pages = max(1, math.ceil(total_types / page_size))
        effective_page = min(page, num_pages)
        offset = (effective_page - 1) * page_size
        page_rows = calculated[offset : offset + page_size]
        return CollocatePage(
            rows=tuple(
                CollocateRow(
                    rank=offset + index,
                    term=row[0],
                    pos=row[1],
                    frequency=row[2],
                    left_frequency=row[3],
                    right_frequency=row[4],
                    document_range=row[5],
                    corpus_frequency=row[6],
                    mutual_information=row[7],
                    t_score=row[8],
                    log_dice=row[9],
                    dice=row[10],
                    mi2=row[11],
                    mi3=row[12],
                    minimum_sensitivity=row[13],
                    mu=row[14],
                    rrf=row[15],
                    drf=row[16],
                    z_score=row[17],
                    log_ratio=row[18],
                    log_likelihood=row[19],
                    chi_square=row[20],
                    p_value=row[21],
                )
                for index, row in enumerate(page_rows, start=1)
            ),
            node_frequency=node_frequency,
            corpus_size=corpus_size,
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            query=query,
            language=language,
            left_span=left_span,
            right_span=right_span,
            min_frequency=min_frequency,
            min_range=min_range,
            pos=pos,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
        )

    def concordance_plot(
        self,
        query: str,
        *,
        language: str,
        overlay_query: str = "",
        sort_by: str = "doc_id",
        invert_order: bool = False,
        show_zero_hits: bool = False,
        bin_count: int = 100,
    ) -> ConcordancePlot:
        _validate_language(language)
        query = " ".join(query.split())
        overlay_query = " ".join(overlay_query.split())
        if not query:
            raise ValueError("query must not be empty.")
        if sort_by not in {
            "doc_id",
            "filename",
            "tokens",
            "frequency",
            "normalized_frequency",
            "dispersion",
        }:
            raise ValueError("unsupported plot sort.")
        if not 10 <= bin_count <= 200:
            raise ValueError("bin_count must be between 10 and 200.")
        detected_language, default_terms = query_terms(query)
        if detected_language != language:
            raise ValueError("query language does not match the selected language.")
        overlay_default_terms: tuple[str, ...] = ()
        if overlay_query:
            overlay_language, overlay_default_terms = query_terms(overlay_query)
            if overlay_language != language:
                raise ValueError(
                    "overlay query language does not match the selected language."
                )
        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                terms = _resolve_terms(connection, query, language, default_terms)
                rows, total = _plot_bins(connection, language, terms, bin_count)
                overlay_rows: list[tuple] = []
                overlay_total = 0
                if overlay_query:
                    overlay_terms = _resolve_terms(
                        connection,
                        overlay_query,
                        language,
                        overlay_default_terms,
                    )
                    overlay_rows, overlay_total = _plot_bins(
                        connection,
                        language,
                        overlay_terms,
                        bin_count,
                    )
                document_rows = connection.execute(
                    """
                    SELECT d.document_id, d.filename, COUNT(t.global_position) AS token_count,
                           MIN(t.global_position) AS first_position
                    FROM documents d
                    JOIN tokens t ON t.document_id = d.document_id AND t.language = ?
                    GROUP BY d.document_id, d.filename
                    ORDER BY first_position
                    """,
                    (language,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("分布图索引读取失败。") from exc

        grouped: dict[str, dict[int, int]] = {}
        for document_id, bin_number, hit_count, _ in rows:
            key = str(document_id)
            grouped.setdefault(key, {})[int(bin_number)] = int(hit_count)
        overlay_grouped: dict[str, dict[int, int]] = {}
        for document_id, bin_number, hit_count, _ in overlay_rows:
            overlay_grouped.setdefault(str(document_id), {})[int(bin_number)] = int(
                hit_count
            )
        documents: list[PlotDocument] = []
        for document_id_value, filename, token_count_value, _ in document_rows:
            document_id = str(document_id_value)
            bins = grouped.get(document_id, {})
            overlay_bins = overlay_grouped.get(document_id, {})
            hit_count = sum(bins.values())
            if not show_zero_hits and not hit_count and not sum(overlay_bins.values()):
                continue
            maximum = max(bins.values(), default=1)
            overlay_maximum = max(overlay_bins.values(), default=1)
            cells = tuple(
                PlotCell(
                    bin_number=bin_number,
                    count=bins.get(bin_number, 0),
                    opacity=(
                        0.15 + 0.85 * bins[bin_number] / maximum
                        if bins.get(bin_number)
                        else 0.0
                    ),
                    overlay_count=overlay_bins.get(bin_number, 0),
                    overlay_opacity=(
                        0.15 + 0.85 * overlay_bins[bin_number] / overlay_maximum
                        if overlay_bins.get(bin_number)
                        else 0.0
                    ),
                )
                for bin_number in range(bin_count)
            )
            token_count = int(token_count_value)
            documents.append(
                PlotDocument(
                    document_id=document_id,
                    filename=str(filename),
                    hit_count=hit_count,
                    cells=cells,
                    token_count=token_count,
                    normalized_frequency=(
                        hit_count * 1_000_000 / token_count if token_count else 0.0
                    ),
                    dispersion=_positional_dispersion(bins, bin_count),
                )
            )
        key_functions = {
            "doc_id": lambda row: row.document_id,
            "filename": lambda row: row.filename.casefold(),
            "tokens": lambda row: row.token_count,
            "frequency": lambda row: row.hit_count,
            "normalized_frequency": lambda row: row.normalized_frequency,
            "dispersion": lambda row: row.dispersion,
        }
        documents.sort(
            key=key_functions[sort_by],
            reverse=invert_order,
        )
        return ConcordancePlot(
            query,
            language,
            total,
            tuple(documents),
            overlay_query,
            overlay_total,
        )

    def _frequency_snapshot(
        self,
        *,
        language: str,
        include_punctuation: bool,
    ) -> tuple[int, dict[str, tuple[str, int, int]]]:
        predicates = ["language = ?"]
        parameters: list[object] = [language]
        if not include_punctuation:
            predicates.append("is_punctuation = 0")
        where = " AND ".join(predicates)
        try:
            with closing(self._connect(required_tables=("word_totals",))) as connection:
                rows = connection.execute(
                    f"""
                    SELECT normalized, display, frequency, document_range
                    FROM word_totals
                    WHERE {where}
                    """,
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("词频索引读取失败。") from exc
        snapshot = {
            str(normalized): (str(display), int(frequency), int(document_range))
            for normalized, display, frequency, document_range in rows
        }
        return sum(row[1] for row in snapshot.values()), snapshot
