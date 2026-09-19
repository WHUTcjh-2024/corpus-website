from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from typing import TYPE_CHECKING

from apps.processing.text import normalize_token
from apps.search.kwic import query_terms

from .calculations import (
    WORDCLOUD_THEMES,
    _keyword_statistics,
    _layout_wordcloud,
    _normalize_filter,
    _resolve_terms,
    _validate_language,
    _validate_page,
    _wordcloud_font_size,
)
from .contracts import (
    ClusterPage,
    ClusterRow,
    FrequencyPage,
    FrequencyRow,
    KeywordPage,
    KeywordRow,
    NgramPage,
    NgramRow,
    StatisticsIndexCorrupt,
    StatisticsIndexUnavailable,
    WordcloudResult,
)

if TYPE_CHECKING:
    from .engine import StatisticsEngine


class FrequencyAnalysisMixin:
    def word_list(
        self,
        *,
        language: str,
        filter_text: str = "",
        pos: str = "",
        min_frequency: int = 1,
        min_range: int = 1,
        sort_by: str = "frequency",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
        display_type: str = "type",
        case_sensitive: bool = False,
        invert_order: bool = False,
        stoplist: tuple[str, ...] = (),
        allowlist: tuple[str, ...] = (),
    ) -> FrequencyPage:
        _validate_language(language)
        _validate_page(page, page_size)
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        if not 1 <= min_range <= 1_000_000:
            raise ValueError("min_range must be between 1 and 1000000.")
        if sort_by not in {"frequency", "range", "term", "end"}:
            raise ValueError("sort_by must be frequency, range, term, or end.")
        if display_type not in {"type", "type_pos", "headword"}:
            raise ValueError("display_type must be type, type_pos, or headword.")
        if stoplist and allowlist:
            raise ValueError("stoplist and allowlist cannot be enabled together.")
        if (
            display_type != "type"
            or case_sensitive
            or invert_order
            or stoplist
            or allowlist
            or sort_by == "end"
        ):
            return self._dynamic_word_list(
                language=language,
                filter_text=filter_text,
                pos=pos,
                min_frequency=min_frequency,
                min_range=min_range,
                sort_by=sort_by,
                include_punctuation=include_punctuation,
                page=page,
                page_size=page_size,
                display_type=display_type,
                case_sensitive=case_sensitive,
                invert_order=invert_order,
                stoplist=stoplist,
                allowlist=allowlist,
            )
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
        filtered_predicates.extend(("frequency >= ?", "document_range >= ?"))
        filtered_parameters.extend((min_frequency, min_range))
        filtered_where = " AND ".join(filtered_predicates)
        order = {
            "frequency": "frequency DESC, document_range DESC, normalized COLLATE NOCASE",
            "range": "document_range DESC, frequency DESC, normalized COLLATE NOCASE",
            "term": "normalized COLLATE NOCASE, frequency DESC",
        }[sort_by]
        try:
            with closing(
                self._connect(required_tables=(table, "word_totals"))
            ) as connection:
                denominator_predicates = ["language = ?"]
                denominator_parameters: list[object] = [language]
                if not include_punctuation:
                    denominator_predicates.append("is_punctuation = 0")
                total_tokens = int(
                    connection.execute(
                        "SELECT COALESCE(SUM(frequency), 0) FROM word_totals "
                        f"WHERE {' AND '.join(denominator_predicates)}",
                        denominator_parameters,
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
                    SELECT normalized, MIN(display) AS display,
                           SUM(frequency) AS frequency,
                           MAX(document_range) AS document_range
                    FROM {table}
                    WHERE {filtered_where}
                    GROUP BY normalized
                    ORDER BY {order}
                    LIMIT ? OFFSET ?
                    """,
                    [*filtered_parameters, page_size, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("词频索引读取失败。") from exc
        result_rows = tuple(
            FrequencyRow(
                rank=offset + index,
                term=str(row[1]),
                frequency=int(row[2]),
                document_range=int(row[3]),
                per_million=(
                    int(row[2]) * 1_000_000 / total_tokens if total_tokens else 0.0
                ),
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
            min_frequency=min_frequency,
            min_range=min_range,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
            display_type="type",
            case_sensitive=False,
            invert_order=False,
        )

    def _dynamic_word_list(
        self,
        *,
        language: str,
        filter_text: str,
        pos: str,
        min_frequency: int,
        min_range: int,
        sort_by: str,
        include_punctuation: bool,
        page: int,
        page_size: int,
        display_type: str,
        case_sensitive: bool,
        invert_order: bool,
        stoplist: tuple[str, ...],
        allowlist: tuple[str, ...],
    ) -> FrequencyPage:
        predicates = ["language = ?"]
        parameters: list[object] = [language]
        if pos:
            predicates.append("pos = ?")
            parameters.append(pos)
        if not include_punctuation:
            predicates.append("is_punctuation = 0")
        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                rows = connection.execute(
                    "SELECT normalized, surface, lemma, pos, document_id "
                    f"FROM tokens WHERE {' AND '.join(predicates)}",
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("动态词表索引读取失败。") from exc
        total_tokens = len(rows)
        stop = {normalize_token(value, language) for value in stoplist}
        allow = {normalize_token(value, language) for value in allowlist}
        normalized_filter = _normalize_filter(filter_text, language)
        counts: Counter[tuple[str, str]] = Counter()
        documents: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        displays: dict[tuple[str, str], str] = {}
        for (
            normalized_value,
            surface_value,
            lemma_value,
            pos_value,
            document_id,
        ) in rows:
            normalized = str(normalized_value)
            if normalized in stop or (allow and normalized not in allow):
                continue
            surface = str(surface_value)
            row_pos = str(pos_value)
            if display_type == "headword":
                lexical = str(lemma_value) or normalized
                key = (lexical if case_sensitive else lexical.casefold(), "")
                display = lexical
            elif display_type == "type_pos":
                lexical = surface if case_sensitive else normalized
                key = (lexical, row_pos)
                display = f"{surface}/{row_pos}" if row_pos else surface
            else:
                lexical = surface if case_sensitive else normalized
                key = (lexical, "")
                display = surface
            if normalized_filter and normalized_filter not in lexical.casefold():
                continue
            counts[key] += 1
            documents[key].add(str(document_id))
            displays.setdefault(key, display)
        calculated = [
            (
                displays[key],
                frequency,
                len(documents[key]),
                frequency * 1_000_000 / total_tokens if total_tokens else 0.0,
            )
            for key, frequency in counts.items()
            if frequency >= min_frequency and len(documents[key]) >= min_range
        ]
        if sort_by == "frequency":
            calculated.sort(key=lambda row: (-row[1], -row[2], row[0].casefold()))
        elif sort_by == "range":
            calculated.sort(key=lambda row: (-row[2], -row[1], row[0].casefold()))
        elif sort_by == "end":
            calculated.sort(key=lambda row: (row[0].casefold()[::-1], -row[1]))
        else:
            calculated.sort(key=lambda row: (row[0].casefold(), -row[1]))
        if invert_order:
            calculated.reverse()
        total_types = len(calculated)
        num_pages = max(1, math.ceil(total_types / page_size))
        effective_page = min(page, num_pages)
        offset = (effective_page - 1) * page_size
        return FrequencyPage(
            rows=tuple(
                FrequencyRow(offset + index, *row)
                for index, row in enumerate(
                    calculated[offset : offset + page_size], start=1
                )
            ),
            total_tokens=total_tokens,
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            filter_text=filter_text,
            pos=pos,
            min_frequency=min_frequency,
            min_range=min_range,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
            display_type=display_type,
            case_sensitive=case_sensitive,
            invert_order=invert_order,
        )

    def clusters(
        self,
        query: str,
        *,
        language: str,
        cluster_size: int = 3,
        query_position: str = "left",
        min_frequency: int = 2,
        min_range: int = 1,
        sort_by: str = "frequency",
        include_punctuation: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> ClusterPage:
        _validate_language(language)
        _validate_page(page, page_size)
        query = " ".join(query.split())
        if not query:
            raise ValueError("query must not be empty.")
        if not 2 <= cluster_size <= 10:
            raise ValueError("cluster_size must be between 2 and 10.")
        if query_position not in {"left", "right"}:
            raise ValueError("query_position must be left or right.")
        if sort_by not in {"frequency", "range", "term", "probability"}:
            raise ValueError("unsupported cluster sort.")
        if min_frequency < 1 or min_range < 1:
            raise ValueError("cluster minimums must be greater than zero.")
        detected_language, default_terms = query_terms(query)
        if detected_language != language:
            raise ValueError("query language does not match the selected language.")

        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                terms = _resolve_terms(connection, query, language, default_terms)
                if len(terms) > cluster_size:
                    raise ValueError("query span exceeds cluster_size.")
                sentence_rows = connection.execute(
                    """
                    SELECT document_id, sentence_id, normalized, surface, is_punctuation
                    FROM tokens
                    WHERE language = ?
                    ORDER BY document_id, sentence_id, sentence_position
                    """,
                    (language,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("词簇索引读取失败。") from exc

        sentences: defaultdict[tuple[str, str], list[tuple[str, str, int]]] = (
            defaultdict(list)
        )
        for (
            document_id,
            sentence_id,
            normalized,
            surface,
            is_punctuation,
        ) in sentence_rows:
            sentences[(str(document_id), str(sentence_id))].append(
                (str(normalized), str(surface), int(is_punctuation))
            )
        counts: Counter[tuple[str, ...]] = Counter()
        displays: dict[tuple[str, ...], tuple[str, ...]] = {}
        documents: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
        suffix_counts: Counter[tuple[str, ...]] = Counter()
        total_tokens = sum(len(tokens) for tokens in sentences.values())
        for (document_id, _), tokens in sentences.items():
            for start in range(len(tokens) - cluster_size + 1):
                window = tokens[start : start + cluster_size]
                if not include_punctuation and any(item[2] for item in window):
                    continue
                normalized = tuple(item[0] for item in window)
                suffix_counts[normalized[1:]] += 1
                query_slice = (
                    normalized[: len(terms)]
                    if query_position == "left"
                    else normalized[-len(terms) :]
                )
                if query_slice != terms:
                    continue
                counts[normalized] += 1
                displays.setdefault(normalized, tuple(item[1] for item in window))
                documents[normalized].add(document_id)

        separator = "" if language == "zh" else " "
        calculated = [
            (
                separator.join(displays[key]),
                frequency,
                len(documents[key]),
                frequency * 1_000_000 / total_tokens if total_tokens else 0.0,
                frequency / suffix_counts[key[1:]] if suffix_counts[key[1:]] else 0.0,
            )
            for key, frequency in counts.items()
            if frequency >= min_frequency and len(documents[key]) >= min_range
        ]
        if sort_by == "frequency":
            calculated.sort(key=lambda row: (-row[1], -row[2], row[0].casefold()))
        elif sort_by == "range":
            calculated.sort(key=lambda row: (-row[2], -row[1], row[0].casefold()))
        elif sort_by == "probability":
            calculated.sort(key=lambda row: (-row[4], -row[1], row[0].casefold()))
        else:
            calculated.sort(key=lambda row: (row[0].casefold(), -row[1]))
        total_types = len(calculated)
        num_pages = max(1, math.ceil(total_types / page_size))
        effective_page = min(page, num_pages)
        offset = (effective_page - 1) * page_size
        return ClusterPage(
            rows=tuple(
                ClusterRow(offset + index, *row)
                for index, row in enumerate(
                    calculated[offset : offset + page_size], start=1
                )
            ),
            total_types=total_types,
            total_tokens=sum(counts.values()),
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            query=query,
            language=language,
            cluster_size=cluster_size,
            query_position=query_position,
            min_frequency=min_frequency,
            min_range=min_range,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
        )

    def ngrams(
        self,
        *,
        language: str,
        n: int = 2,
        min_frequency: int = 2,
        min_range: int = 1,
        filter_text: str = "",
        sort_by: str = "frequency",
        include_punctuation: bool = False,
        open_slot: int = 0,
        page: int = 1,
        page_size: int = 50,
    ) -> NgramPage:
        _validate_language(language)
        _validate_page(page, page_size)
        if n not in {2, 3, 4, 5}:
            raise ValueError("n must be between 2 and 5.")
        if not 0 <= open_slot <= n:
            raise ValueError(
                "open_slot must be zero or a one-based slot within the n-gram."
            )
        if not 1 <= min_frequency <= 1_000_000:
            raise ValueError("min_frequency must be between 1 and 1000000.")
        if not 1 <= min_range <= 1_000_000:
            raise ValueError("min_range must be between 1 and 1000000.")
        if sort_by not in {"frequency", "range", "term"}:
            raise ValueError("sort_by must be frequency, range, or term.")
        if open_slot:
            return self._open_slot_ngrams(
                language=language,
                n=n,
                open_slot=open_slot,
                min_frequency=min_frequency,
                min_range=min_range,
                filter_text=filter_text,
                sort_by=sort_by,
                include_punctuation=include_punctuation,
                page=page,
                page_size=page_size,
            )
        predicates = [
            "language = ?",
            "n = ?",
            "frequency >= ?",
            "document_range >= ?",
        ]
        parameters: list[object] = [language, n, min_frequency, min_range]
        if filter_text:
            predicates.append("instr(lower(display), ?) > 0")
            parameters.append(filter_text.casefold())
        if not include_punctuation:
            predicates.append("contains_punctuation = 0")
        where = " AND ".join(predicates)
        order = {
            "frequency": "frequency DESC, document_range DESC, normalized COLLATE NOCASE",
            "range": "document_range DESC, frequency DESC, normalized COLLATE NOCASE",
            "term": "normalized COLLATE NOCASE, frequency DESC",
        }[sort_by]
        try:
            with closing(self._connect(required_tables=("ngrams",))) as connection:
                ngram_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(ngrams)")
                }
                if "document_range" not in ngram_columns:
                    raise StatisticsIndexUnavailable("N-Gram 索引结构不兼容。")
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
                    SELECT display, frequency, document_range
                    FROM ngrams
                    WHERE {where}
                    ORDER BY {order}
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, page_size, offset],
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("N-Gram 索引读取失败。") from exc
        return NgramPage(
            rows=tuple(
                NgramRow(offset + index, str(row[0]), int(row[1]), int(row[2]))
                for index, row in enumerate(rows, start=1)
            ),
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            n=n,
            min_frequency=min_frequency,
            min_range=min_range,
            filter_text=filter_text,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
            open_slot=0,
        )

    def _open_slot_ngrams(
        self,
        *,
        language: str,
        n: int,
        open_slot: int,
        min_frequency: int,
        min_range: int,
        filter_text: str,
        sort_by: str,
        include_punctuation: bool,
        page: int,
        page_size: int,
    ) -> NgramPage:
        try:
            with closing(self._connect(required_tables=("tokens",))) as connection:
                rows = connection.execute(
                    """
                    SELECT document_id, sentence_id, normalized, surface, is_punctuation
                    FROM tokens
                    WHERE language = ?
                    ORDER BY document_id, sentence_id, sentence_position
                    """,
                    (language,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise StatisticsIndexCorrupt("开放槽 N-Gram 索引读取失败。") from exc
        sentences: defaultdict[tuple[str, str], list[tuple[str, str, int]]] = (
            defaultdict(list)
        )
        for document_id, sentence_id, normalized, surface, is_punctuation in rows:
            sentences[(str(document_id), str(sentence_id))].append(
                (str(normalized), str(surface), int(is_punctuation))
            )
        slot_index = open_slot - 1
        counts: Counter[tuple[str, ...]] = Counter()
        documents: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
        variants: defaultdict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
        for (document_id, _), tokens in sentences.items():
            for start in range(len(tokens) - n + 1):
                window = tokens[start : start + n]
                if not include_punctuation and any(item[2] for item in window):
                    continue
                pattern = tuple(
                    "<*>" if index == slot_index else item[0]
                    for index, item in enumerate(window)
                )
                counts[pattern] += 1
                documents[pattern].add(document_id)
                variants[pattern][window[slot_index][1]] += 1
        separator = "" if language == "zh" else " "
        normalized_filter = filter_text.casefold().strip()
        calculated: list[tuple[str, int, int, int, float, float]] = []
        for pattern, frequency in counts.items():
            display = separator.join(pattern)
            document_range = len(documents[pattern])
            if frequency < min_frequency or document_range < min_range:
                continue
            if normalized_filter and normalized_filter not in display.casefold():
                continue
            variant_counts = variants[pattern]
            entropy = -sum(
                (count / frequency) * math.log2(count / frequency)
                for count in variant_counts.values()
            )
            calculated.append(
                (
                    display,
                    frequency,
                    document_range,
                    len(variant_counts),
                    len(variant_counts) / frequency,
                    entropy,
                )
            )
        if sort_by == "frequency":
            calculated.sort(key=lambda row: (-row[1], -row[2], row[0]))
        elif sort_by == "range":
            calculated.sort(key=lambda row: (-row[2], -row[1], row[0]))
        else:
            calculated.sort(key=lambda row: (row[0], -row[1]))
        total_types = len(calculated)
        num_pages = max(1, math.ceil(total_types / page_size))
        effective_page = min(page, num_pages)
        offset = (effective_page - 1) * page_size
        return NgramPage(
            rows=tuple(
                NgramRow(offset + index, *row)
                for index, row in enumerate(
                    calculated[offset : offset + page_size], start=1
                )
            ),
            total_types=total_types,
            page=effective_page,
            page_size=page_size,
            num_pages=num_pages,
            language=language,
            n=n,
            min_frequency=min_frequency,
            min_range=min_range,
            filter_text=filter_text,
            sort_by=sort_by,
            include_punctuation=include_punctuation,
            open_slot=open_slot,
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
        if sort_by not in {
            "log_likelihood",
            "chi_square",
            "log_ratio",
            "frequency",
            "term",
        }:
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
            reference_display, reference_frequency, reference_range = (
                reference_rows.get(normalized, (normalized, 0, 0))
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
            raise StatisticsIndexCorrupt("词云索引读取失败。") from exc

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
