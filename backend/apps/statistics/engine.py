from __future__ import annotations

import json
import math
import sqlite3
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from apps.processing.text import normalize_token
from apps.search.kwic import query_terms


SUPPORTED_LANGUAGES = ("zh", "en")
PAGE_SIZES = (20, 50, 100)
MAX_QUERY_TERMS = 20
WORDCLOUD_WIDTH = 1000
WORDCLOUD_HEIGHT = 560
WORDCLOUD_THEMES = {
    "ocean": ("#0f4c81", "#1261a0", "#1778b5", "#1597a5", "#2d9cdb", "#58b6d9"),
    "forest": ("#174c3c", "#236b4e", "#2f855a", "#479f76", "#68b984", "#8acb88"),
    "sunset": ("#713c67", "#9a3f62", "#c94c5c", "#e76f51", "#f49d5b", "#efbd68"),
}


class StatisticsIndexUnavailable(RuntimeError):
    pass


class StatisticsIndexCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrequencyRow:
    rank: int
    term: str
    frequency: int
    per_million: float


@dataclass(frozen=True, slots=True)
class FrequencyPage:
    rows: tuple[FrequencyRow, ...]
    total_tokens: int
    total_types: int
    page: int
    page_size: int
    num_pages: int
    language: str
    filter_text: str
    pos: str
    sort_by: str
    include_punctuation: bool

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class KeywordRow:
    rank: int
    term: str
    target_frequency: int
    target_range: int
    target_per_million: float
    reference_frequency: int
    reference_range: int
    reference_per_million: float
    log_likelihood: float
    chi_square: float
    log_ratio: float
    direction: str


@dataclass(frozen=True, slots=True)
class KeywordPage:
    rows: tuple[KeywordRow, ...]
    target_tokens: int
    reference_tokens: int
    total_types: int
    page: int
    page_size: int
    num_pages: int
    language: str
    reference_corpus_id: str
    reference_name: str
    min_frequency: int
    min_range: int
    filter_text: str
    include_negative: bool
    sort_by: str

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class NgramRow:
    rank: int
    ngram: str
    frequency: int


@dataclass(frozen=True, slots=True)
class NgramPage:
    rows: tuple[NgramRow, ...]
    total_types: int
    page: int
    page_size: int
    num_pages: int
    language: str
    n: int
    min_frequency: int
    filter_text: str
    include_punctuation: bool

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class CollocateRow:
    rank: int
    term: str
    pos: str
    frequency: int
    corpus_frequency: int
    mutual_information: float
    t_score: float
    log_dice: float


@dataclass(frozen=True, slots=True)
class CollocatePage:
    rows: tuple[CollocateRow, ...]
    node_frequency: int
    corpus_size: int
    total_types: int
    page: int
    page_size: int
    num_pages: int
    query: str
    language: str
    left_span: int
    right_span: int
    min_frequency: int
    pos: str
    sort_by: str
    include_punctuation: bool

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class PlotCell:
    bin_number: int
    count: int
    opacity: float


@dataclass(frozen=True, slots=True)
class PlotDocument:
    document_id: str
    filename: str
    hit_count: int
    cells: tuple[PlotCell, ...]


@dataclass(frozen=True, slots=True)
class ConcordancePlot:
    query: str
    language: str
    total: int
    documents: tuple[PlotDocument, ...]


@dataclass(frozen=True, slots=True)
class WordcloudTerm:
    term: str
    frequency: int
    font_size: float
    x: float
    y: float
    color: str


@dataclass(frozen=True, slots=True)
class WordcloudResult:
    terms: tuple[WordcloudTerm, ...]
    language: str
    min_frequency: int
    max_words: int
    excluded_stopwords: int
    source_types: int
    theme: str
    canvas_width: int = WORDCLOUD_WIDTH
    canvas_height: int = WORDCLOUD_HEIGHT


class StatisticsEngine:
    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = str(corpus_id)
        self.index_path = self.data_root / "indexes" / self.corpus_id / "kwic_index.sqlite"
        self.processed_dir = self.data_root / "processed" / self.corpus_id

    def word_list(
        self,
        *,
        language: str,
        filter_text: str = "",
        pos: str = "",
        sort_by: str = "frequency",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> FrequencyPage:
        _validate_language(language)
        _validate_page(page, page_size)
        if sort_by not in {"frequency", "term"}:
            raise ValueError("sort_by must be frequency or term.")
        normalized_filter = _normalize_filter(filter_text, language)
        table = "word_frequencies" if pos else "word_totals"
        predicates = ["language = ?"]
        parameters: list[object] = [language]
        if pos:
            predicates.append("pos = ?")
            parameters.append(pos)
        if not include_punctuation:
            predicates.append("is_punctuation = 0")
        filtered_predicates = list(predicates)
        filtered_parameters = list(parameters)
        if normalized_filter:
            filtered_predicates.append("instr(normalized, ?) > 0")
            filtered_parameters.append(normalized_filter)
        where = " AND ".join(predicates)
        filtered_where = " AND ".join(filtered_predicates)
        order = (
            "frequency DESC, normalized COLLATE NOCASE"
            if sort_by == "frequency"
            else "normalized COLLATE NOCASE, frequency DESC"
        )
        try:
            with closing(self._connect(required_tables=(table,))) as connection:
                total_tokens = int(
                    connection.execute(
                        f"SELECT COALESCE(SUM(frequency), 0) FROM {table} WHERE {where}",
                        parameters,
                    ).fetchone()[0]
                )
                total_types = int(
                    connection.execute(
                        f"SELECT COUNT(DISTINCT normalized) FROM {table} WHERE {filtered_where}",
                        filtered_parameters,
                    ).fetchone()[0]
                )
                num_pages = max(1, math.ceil(total_types / page_size))
                effective_page = min(page, num_pages)
                offset = (effective_page - 1) * page_size
                rows = connection.execute(
                    f"""
                    SELECT normalized, MIN(display) AS display, SUM(frequency) AS frequency
                    FROM {table}
                    WHERE {filtered_where}
                    GROUP BY normalized
                    ORDER BY {order}
                    LIMIT ? OFFSET ?
                    """,
                    [*filtered_parameters, page_size, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("词频索引无法读取，请重新加工语料库。") from exc
        result_rows = tuple(
            FrequencyRow(
                rank=offset + index,
                term=str(row[1]),
                frequency=int(row[2]),
                per_million=(int(row[2]) * 1_000_000 / total_tokens if total_tokens else 0.0),
            )
            for index, row in enumerate(rows, start=1)
        )
        return FrequencyPage(
            rows=result_rows,
            total_tokens=total_tokens,
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            filter_text=filter_text,
            pos=pos,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
        )

    def ngrams(
        self,
        *,
        language: str,
        n: int = 2,
        min_frequency: int = 2,
        filter_text: str = "",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> NgramPage:
        _validate_language(language)
        _validate_page(page, page_size)
        if n not in {2, 3, 4, 5}:
            raise ValueError("n must be between 2 and 5.")
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        predicates = ["language = ?", "n = ?", "frequency >= ?"]
        parameters: list[object] = [language, n, min_frequency]
        if filter_text:
            predicates.append("instr(lower(display), ?) > 0")
            parameters.append(filter_text.casefold())
        if not include_punctuation:
            predicates.append("contains_punctuation = 0")
        where = " AND ".join(predicates)
        try:
            with closing(self._connect(required_tables=("ngrams",))) as connection:
                total_types = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM ngrams WHERE {where}", parameters
                    ).fetchone()[0]
                )
                num_pages = max(1, math.ceil(total_types / page_size))
                effective_page = min(page, num_pages)
                offset = (effective_page - 1) * page_size
                rows = connection.execute(
                    f"""
                    SELECT display, frequency
                    FROM ngrams
                    WHERE {where}
                    ORDER BY frequency DESC, normalized COLLATE NOCASE
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, page_size, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("N-Gram 索引无法读取，请重新加工语料库。") from exc
        return NgramPage(
            rows=tuple(
                NgramRow(offset + index, str(row[0]), int(row[1]))
                for index, row in enumerate(rows, start=1)
            ),
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            n=n,
            min_frequency=min_frequency,
            filter_text=filter_text,
            include_punctuation=include_punctuation,
        )

    def keywords(
        self,
        *,
        reference: "StatisticsEngine",
        reference_name: str,
        language: str,
        min_frequency: int = 2,
        min_range: int = 1,
        filter_text: str = "",
        include_negative: bool = False,
        sort_by: str = "log_likelihood",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> KeywordPage:
        _validate_language(language)
        _validate_page(page, page_size)
        if reference.index_path == self.index_path:
            raise ValueError("reference corpus must differ from target corpus.")
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        if not 1 <= min_range <= 1_000_000:
            raise ValueError("min_range must be between 1 and 1000000.")
        if sort_by not in {"log_likelihood", "chi_square", "log_ratio", "frequency", "term"}:
            raise ValueError("unsupported keyword sort.")

        target_tokens, target = self._frequency_snapshot(
            language=language,
            include_punctuation=include_punctuation,
        )
        reference_tokens, reference_rows = reference._frequency_snapshot(
            language=language,
            include_punctuation=include_punctuation,
        )
        if not target_tokens or not reference_tokens:
            raise ValueError("目标语料和参照语料都必须包含所选语言的 Token。")

        normalized_filter = _normalize_filter(filter_text, language)
        calculated: list[tuple[str, int, int, int, int, float, float, float, str]] = []
        for normalized in target.keys() | reference_rows.keys():
            target_display, target_frequency, target_range = target.get(
                normalized, (normalized, 0, 0)
            )
            reference_display, reference_frequency, reference_range = reference_rows.get(
                normalized, (normalized, 0, 0)
            )
            if normalized_filter and normalized_filter not in normalized:
                continue
            if max(target_frequency, reference_frequency) < min_frequency:
                continue
            if max(target_range, reference_range) < min_range:
                continue
            log_likelihood, chi_square, log_ratio = _keyword_statistics(
                target_frequency=target_frequency,
                target_tokens=target_tokens,
                reference_frequency=reference_frequency,
                reference_tokens=reference_tokens,
            )
            direction = "positive" if log_ratio >= 0 else "negative"
            if direction == "negative" and not include_negative:
                continue
            calculated.append(
                (
                    target_display if target_frequency else reference_display,
                    target_frequency,
                    target_range,
                    reference_frequency,
                    reference_range,
                    log_likelihood,
                    chi_square,
                    log_ratio,
                    direction,
                )
            )

        if sort_by == "term":
            calculated.sort(key=lambda row: (row[0].casefold(), -row[1], -row[3]))
        elif sort_by == "frequency":
            calculated.sort(key=lambda row: (-row[1], -row[3], row[0].casefold()))
        else:
            sort_index = {"log_likelihood": 5, "chi_square": 6, "log_ratio": 7}[sort_by]
            calculated.sort(
                key=lambda row: (-abs(row[sort_index]), -row[1], row[0].casefold())
            )

        total_types = len(calculated)
        num_pages = max(1, math.ceil(total_types / page_size))
        effective_page = min(page, num_pages)
        offset = (effective_page - 1) * page_size
        page_rows = calculated[offset : offset + page_size]
        return KeywordPage(
            rows=tuple(
                KeywordRow(
                    rank=offset + index,
                    term=row[0],
                    target_frequency=row[1],
                    target_range=row[2],
                    target_per_million=row[1] * 1_000_000 / target_tokens,
                    reference_frequency=row[3],
                    reference_range=row[4],
                    reference_per_million=row[3] * 1_000_000 / reference_tokens,
                    log_likelihood=row[5],
                    chi_square=row[6],
                    log_ratio=row[7],
                    direction=row[8],
                )
                for index, row in enumerate(page_rows, start=1)
            ),
            target_tokens=target_tokens,
            reference_tokens=reference_tokens,
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            reference_corpus_id=reference.corpus_id,
            reference_name=reference_name,
            min_frequency=min_frequency,
            min_range=min_range,
            filter_text=filter_text,
            include_negative=include_negative,
            sort_by=sort_by,
        )

    def wordcloud(
        self,
        *,
        language: str,
        min_frequency: int = 2,
        max_words: int = 50,
        stopwords: tuple[str, ...] = (),
        include_punctuation: bool = False,
        theme: str = "ocean",
    ) -> WordcloudResult:
        _validate_language(language)
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        if not 10 <= max_words <= 200:
            raise ValueError("max_words must be between 10 and 200.")
        if theme not in WORDCLOUD_THEMES:
            raise ValueError("unsupported wordcloud theme.")
        normalized_stopwords = tuple(
            dict.fromkeys(
                normalize_token(value.strip(), language)
                for value in stopwords
                if value.strip()
            )
        )
        if len(normalized_stopwords) > 200:
            raise ValueError("stopwords cannot contain more than 200 items.")

        predicates = ["language = ?", "frequency >= ?"]
        parameters: list[object] = [language, min_frequency]
        if not include_punctuation:
            predicates.append("is_punctuation = 0")
        base_where = " AND ".join(predicates)
        filtered_where = base_where
        filtered_parameters = list(parameters)
        if normalized_stopwords:
            placeholders = ",".join("?" for _ in normalized_stopwords)
            filtered_where += f" AND normalized NOT IN ({placeholders})"
            filtered_parameters.extend(normalized_stopwords)
        try:
            with closing(self._connect(required_tables=("word_totals",))) as connection:
                source_types = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM word_totals WHERE {base_where}",
                        parameters,
                    ).fetchone()[0]
                )
                excluded_stopwords = source_types - int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM word_totals WHERE {filtered_where}",
                        filtered_parameters,
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                    SELECT display, frequency
                    FROM word_totals
                    WHERE {filtered_where}
                    ORDER BY frequency DESC, normalized COLLATE NOCASE
                    LIMIT ?
                    """,
                    [*filtered_parameters, max_words],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("词云索引无法读取，请重新加工语料库。") from exc

        frequencies = [int(row[1]) for row in rows]
        minimum = min(frequencies, default=0)
        maximum = max(frequencies, default=0)
        weighted_terms = tuple(
            (
                str(display),
                int(frequency),
                _wordcloud_font_size(int(frequency), minimum, maximum),
            )
            for display, frequency in rows
        )
        terms = _layout_wordcloud(weighted_terms, WORDCLOUD_THEMES[theme])
        return WordcloudResult(
            terms=terms,
            language=language,
            min_frequency=min_frequency,
            max_words=max_words,
            excluded_stopwords=excluded_stopwords,
            source_types=source_types,
            theme=theme,
        )

    def collocates(
        self,
        query: str,
        *,
        language: str,
        left_span: int = 5,
        right_span: int = 5,
        min_frequency: int = 2,
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
        if sort_by not in {"frequency", "mi", "t_score", "log_dice", "term"}:
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
                corpus_size = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM tokens WHERE language = ?", (language,)
                    ).fetchone()[0]
                )
                if node_frequency:
                    rows = connection.execute(
                        f"""
                        WITH node AS ({node_sql}),
                        observed AS (
                            SELECT c.normalized, c.pos, MIN(c.surface) AS display,
                                   COUNT(*) AS observed_frequency
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
                            GROUP BY c.normalized, c.pos
                            HAVING COUNT(*) >= ?
                        ),
                        corpus_frequency AS (
                            SELECT normalized, pos, COUNT(*) AS frequency
                            FROM tokens
                            WHERE language = ?
                            GROUP BY normalized, pos
                        )
                        SELECT observed.normalized, observed.display, observed.pos,
                               observed.observed_frequency, corpus_frequency.frequency
                        FROM observed
                        JOIN corpus_frequency
                          ON corpus_frequency.normalized = observed.normalized
                         AND corpus_frequency.pos = observed.pos
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
                            language,
                        ],
                    ).fetchall()
                else:
                    rows = []
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("搭配索引无法读取，请重新加工语料库。") from exc

        calculated: list[tuple[str, str, int, int, float, float, float]] = []
        for _, display, row_pos, observed, corpus_frequency in rows:
            observed_value = int(observed)
            corpus_frequency_value = int(corpus_frequency)
            expected = (
                node_frequency * corpus_frequency_value / corpus_size if corpus_size else 0.0
            )
            mutual_information = (
                math.log2(observed_value / expected) if expected > 0 and observed_value else 0.0
            )
            t_score = (
                (observed_value - expected) / math.sqrt(observed_value)
                if observed_value
                else 0.0
            )
            log_dice = (
                14
                + math.log2(
                    2 * observed_value / (node_frequency + corpus_frequency_value)
                )
                if observed_value and node_frequency + corpus_frequency_value
                else 0.0
            )
            calculated.append(
                (
                    str(display),
                    str(row_pos),
                    observed_value,
                    corpus_frequency_value,
                    mutual_information,
                    t_score,
                    log_dice,
                )
            )
        sort_index = {"mi": 4, "t_score": 5, "log_dice": 6}.get(sort_by)
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
                    corpus_frequency=row[3],
                    mutual_information=row[4],
                    t_score=row[5],
                    log_dice=row[6],
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
            pos=pos,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
        )

    def concordance_plot(self, query: str, *, language: str) -> ConcordancePlot:
        _validate_language(language)
        query = " ".join(query.split())
        if not query:
            raise ValueError("query must not be empty.")
        detected_language, default_terms = query_terms(query)
        if detected_language != language:
            raise ValueError("query language does not match the selected language.")
        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                terms = _resolve_terms(connection, query, language, default_terms)
                node_sql, node_parameters = _node_match_sql(language, terms)
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM ({node_sql}) node", node_parameters
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                    WITH node AS ({node_sql}),
                    bounds AS (
                        SELECT document_id, MIN(global_position) AS first_position,
                               MAX(global_position) AS last_position
                        FROM tokens
                        WHERE language = ?
                        GROUP BY document_id
                    )
                    SELECT node.document_id,
                           CASE WHEN bounds.last_position = bounds.first_position THEN 0
                                ELSE CAST(
                                    (node.global_position - bounds.first_position) * 99.0 /
                                    (bounds.last_position - bounds.first_position)
                                    AS INTEGER
                                )
                           END AS bin_number,
                           COUNT(*) AS hit_count,
                           bounds.first_position
                    FROM node
                    JOIN bounds ON bounds.document_id = node.document_id
                    GROUP BY node.document_id, bin_number
                    ORDER BY bounds.first_position, bin_number
                    """,
                    [*node_parameters, language],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("分布图索引无法读取，请重新加工语料库。") from exc

        document_metadata = _documents_by_id(self.processed_dir / "documents.jsonl")
        grouped: dict[str, dict[int, int]] = {}
        document_order: list[str] = []
        for document_id, bin_number, hit_count, _ in rows:
            key = str(document_id)
            if key not in grouped:
                grouped[key] = {}
                document_order.append(key)
            grouped[key][int(bin_number)] = int(hit_count)
        documents: list[PlotDocument] = []
        for document_id in document_order:
            bins = grouped[document_id]
            maximum = max(bins.values(), default=1)
            cells = tuple(
                PlotCell(
                    bin_number=bin_number,
                    count=bins.get(bin_number, 0),
                    opacity=(0.15 + 0.85 * bins[bin_number] / maximum if bins.get(bin_number) else 0.0),
                )
                for bin_number in range(100)
            )
            metadata = document_metadata.get(document_id, {})
            documents.append(
                PlotDocument(
                    document_id=document_id,
                    filename=str(metadata.get("filename", document_id)),
                    hit_count=sum(bins.values()),
                    cells=cells,
                )
            )
        return ConcordancePlot(query, language, total, tuple(documents))

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
            raise StatisticsIndexCorrupt("词频索引无法读取，请重新加工语料库。") from exc
        snapshot = {
            str(normalized): (str(display), int(frequency), int(document_range))
            for normalized, display, frequency, document_range in rows
        }
        return sum(row[1] for row in snapshot.values()), snapshot

    def _connect(self, *, required_tables: tuple[str, ...]) -> sqlite3.Connection:
        if not self.index_path.is_file():
            raise StatisticsIndexUnavailable("统计索引不存在，请先加工语料库。")
        try:
            connection = sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = set(required_tables) - existing
            if missing:
                connection.close()
                raise StatisticsIndexUnavailable("索引版本过旧，请重新加工语料库。")
            return connection
        except StatisticsIndexUnavailable:
            raise
        except sqlite3.Error as exc:
            raise StatisticsIndexUnavailable("无法打开统计索引。") from exc


def _node_match_sql(language: str, terms: tuple[str, ...]) -> tuple[str, list[object]]:
    aliases = [f"t{index}" for index in range(len(terms))]
    joins = " ".join(
        f"JOIN tokens {alias} ON {alias}.sentence_id = t0.sentence_id "
        f"AND {alias}.sentence_position = t0.sentence_position + {index}"
        for index, alias in enumerate(aliases[1:], start=1)
    )
    predicates = ["t0.language = ?"] + [
        f"{alias}.normalized = ?" for alias in aliases
    ]
    sql = (
        "SELECT t0.document_id, t0.sentence_id, t0.sentence_position, "
        f"t0.global_position FROM tokens t0 {joins} WHERE {' AND '.join(predicates)}"
    )
    return sql, [language, *terms]


def _resolve_terms(
    connection: sqlite3.Connection,
    query: str,
    language: str,
    default_terms: tuple[str, ...],
) -> tuple[str, ...]:
    if language != "zh":
        return default_terms
    word_terms = tuple(normalize_token(value, language) for value in query.split() if value)
    if len(word_terms) <= 1:
        word_terms = (normalize_token(query, language),)
    if word_terms == default_terms:
        return default_terms
    sql, parameters = _node_match_sql(language, word_terms)
    row = connection.execute(f"SELECT 1 FROM ({sql}) node LIMIT 1", parameters).fetchone()
    return word_terms if row else default_terms


def _documents_by_id(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise StatisticsIndexUnavailable("文档元数据不存在，请重新加工语料库。")
    try:
        return {
            str(record.get("id", "")): record
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for record in [json.loads(line)]
        }
    except (OSError, json.JSONDecodeError) as exc:
        raise StatisticsIndexCorrupt("文档元数据损坏。") from exc


def _keyword_statistics(
    *,
    target_frequency: int,
    target_tokens: int,
    reference_frequency: int,
    reference_tokens: int,
) -> tuple[float, float, float]:
    observed = (
        float(target_frequency),
        float(target_tokens - target_frequency),
        float(reference_frequency),
        float(reference_tokens - reference_frequency),
    )
    total = float(target_tokens + reference_tokens)
    item_total = float(target_frequency + reference_frequency)
    other_total = total - item_total
    expected = (
        target_tokens * item_total / total,
        target_tokens * other_total / total,
        reference_tokens * item_total / total,
        reference_tokens * other_total / total,
    )
    log_likelihood = 2 * sum(
        value * math.log(value / expectation)
        for value, expectation in zip(observed, expected, strict=True)
        if value > 0 and expectation > 0
    )
    chi_square = sum(
        (value - expectation) ** 2 / expectation
        for value, expectation in zip(observed, expected, strict=True)
        if expectation > 0
    )
    adjusted_target = float(target_frequency) if target_frequency else 0.5
    adjusted_reference = float(reference_frequency) if reference_frequency else 0.5
    log_ratio = math.log2(
        (adjusted_target / target_tokens) / (adjusted_reference / reference_tokens)
    )
    return log_likelihood, chi_square, log_ratio


def _wordcloud_font_size(frequency: int, minimum: int, maximum: int) -> float:
    if maximum <= minimum:
        return 40.0
    low = math.log1p(minimum)
    high = math.log1p(maximum)
    scale = (math.log1p(frequency) - low) / (high - low)
    return round(18.0 + 54.0 * scale, 2)


def _layout_wordcloud(
    weighted_terms: tuple[tuple[str, int, float], ...],
    palette: tuple[str, ...],
) -> tuple[WordcloudTerm, ...]:
    """Place terms on a deterministic elliptical spiral without overlaps."""
    occupied: list[tuple[float, float, float, float]] = []
    placed: list[WordcloudTerm] = []
    center_x = WORDCLOUD_WIDTH / 2
    center_y = WORDCLOUD_HEIGHT / 2
    for rank, (term, frequency, font_size) in enumerate(weighted_terms):
        width = min(_display_width(term, font_size), WORDCLOUD_WIDTH - 32)
        height = font_size * 1.12
        position = None
        for step in range(2600):
            radius = step * 0.34
            angle = step * 0.48 + rank * 0.19
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * 0.56 * math.sin(angle)
            box = (
                x - width / 2 - 5,
                y - height / 2 - 4,
                x + width / 2 + 5,
                y + height / 2 + 4,
            )
            if not _inside_canvas(box) or any(_overlaps(box, other) for other in occupied):
                continue
            position = (round(x, 2), round(y, 2), box)
            break
        if position is None:
            continue
        x, y, box = position
        occupied.append(box)
        placed.append(
            WordcloudTerm(
                term=term,
                frequency=frequency,
                font_size=font_size,
                x=x,
                y=y,
                color=palette[rank % len(palette)],
            )
        )
    return tuple(placed)


def _display_width(term: str, font_size: float) -> float:
    units = sum(
        1.0 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 0.58
        for character in term
    )
    return max(font_size, units * font_size)


def _inside_canvas(box: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = box
    return left >= 16 and top >= 16 and right <= WORDCLOUD_WIDTH - 16 and bottom <= WORDCLOUD_HEIGHT - 16


def _overlaps(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] <= second[0]
        or first[0] >= second[2]
        or first[3] <= second[1]
        or first[1] >= second[3]
    )


def _normalize_filter(value: str, language: str) -> str:
    compact = " ".join(value.split())
    return compact if language == "zh" else compact.casefold()


def _validate_language(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError("language must be zh or en.")


def _validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be at least 1.")
    if page_size not in PAGE_SIZES:
        raise ValueError(f"page_size must be one of {PAGE_SIZES}.")
