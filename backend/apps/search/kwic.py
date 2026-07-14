from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.processing.text import normalize_token, token_matches


DEFAULT_CONTEXT_SIZE = 5
DEFAULT_PAGE_SIZE = 50
MAX_CONTEXT_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_QUERY_TERMS = 20
SORT_FIELDS = ("L1", "L2", "L3", "R1", "R2", "R3")


class KwicSearchError(Exception):
    """Base exception for a user-visible KWIC search failure."""


class KwicIndexUnavailable(KwicSearchError):
    """The corpus has not produced the required search artifacts yet."""


class KwicIndexCorrupt(KwicSearchError):
    """The corpus search artifacts exist but cannot be read safely."""


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
    l3: str
    l2: str
    l1: str
    r1: str
    r2: str
    r3: str


@dataclass(frozen=True, slots=True)
class KwicPage:
    query: str
    hits: tuple[KwicHit, ...]
    total: int
    page: int
    page_size: int
    context_size: int
    sort_by: str
    pos: str

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
class KwicMatch:
    sentence_id: str
    document_id: str
    sentence_position: int
    language: str
    keyword_surfaces: tuple[str, ...]


class KwicSearchEngine:
    def __init__(self, *, data_root: Path, corpus_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = str(corpus_id)
        self.index_dir = self.data_root / "indexes" / self.corpus_id
        self.processed_dir = self.data_root / "processed" / self.corpus_id
        self.index_path = self.index_dir / "kwic_index.sqlite"

    def search(
        self,
        query: str,
        *,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        sort_by: str = "",
        pos: str = "",
    ) -> KwicPage:
        query = " ".join(query.split())
        if not query:
            raise ValueError("Query must not be empty.")
        if not 0 <= context_size <= MAX_CONTEXT_SIZE:
            raise ValueError(f"context_size must be between 0 and {MAX_CONTEXT_SIZE}.")
        if page < 1:
            raise ValueError("page must be at least 1.")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
        sort_by = sort_by.strip().upper()
        pos = pos.strip()
        if sort_by and sort_by not in SORT_FIELDS:
            raise ValueError(f"sort_by must be one of: {', '.join(SORT_FIELDS)}.")

        language, normalized_terms = query_terms(query)
        self._require_artifacts()
        try:
            with closing(
                sqlite3.connect(f"file:{self.index_path}?mode=ro", uri=True)
            ) as connection:
                resolved_terms = self._resolve_terms(
                    connection,
                    query,
                    language,
                    normalized_terms,
                )
                total = self._count_matches(connection, language, resolved_terms, pos=pos)
                num_pages = max(1, math.ceil(total / page_size))
                effective_page = min(page, num_pages)
                matches = self._page_matches(
                    connection,
                    language,
                    resolved_terms,
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
            raise KwicIndexCorrupt("KWIC 索引无法读取，请重新加工该语料库。") from exc

        metadata = self._metadata_for(matches)
        hits = tuple(
            self._build_hit(match, token_rows.get(match.sentence_id, ()), metadata, context_size)
            for match in matches
        )
        return KwicPage(
            query=query,
            hits=hits,
            total=total,
            page=effective_page,
            page_size=page_size,
            context_size=context_size,
            sort_by=sort_by,
            pos=pos,
        )

    def _resolve_terms(
        self,
        connection: sqlite3.Connection,
        query: str,
        language: str,
        default_terms: tuple[str, ...],
    ) -> tuple[str, ...]:
        if language != "zh":
            return default_terms
        word_terms = tuple(
            normalize_token(value, language) for value in query.split() if value
        )
        if not word_terms:
            word_terms = (normalize_token(query, language),)
        elif len(word_terms) == 1 and " " not in query:
            word_terms = (normalize_token(query, language),)
        if word_terms == default_terms:
            return default_terms
        if self._count_matches(connection, language, word_terms) > 0:
            return word_terms
        return default_terms

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
    def _match_sql(
        terms: tuple[str, ...],
        *,
        count: bool,
        sort_by: str = "",
        pos: str = "",
    ) -> tuple[str, list[str]]:
        aliases = [f"t{index}" for index in range(len(terms))]
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
            offset = sort_offset(sort_by, len(terms))
            joins += (
                " LEFT JOIN tokens sort_token ON sort_token.sentence_id = t0.sentence_id "
                f"AND sort_token.sentence_position = t0.sentence_position + {offset}"
            )
        predicates = ["t0.language = ?"] + [f"{alias}.normalized = ?" for alias in aliases]
        parameters = list(terms)
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

    def _count_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        terms: tuple[str, ...],
        pos: str = "",
    ) -> int:
        sql, term_params = self._match_sql(terms, count=True, pos=pos)
        row = connection.execute(sql, [language, *term_params]).fetchone()
        return int(row[0]) if row else 0

    def _page_matches(
        self,
        connection: sqlite3.Connection,
        language: str,
        terms: tuple[str, ...],
        *,
        page: int,
        page_size: int,
        sort_by: str,
        pos: str,
    ) -> list[KwicMatch]:
        sql, term_params = self._match_sql(
            terms,
            count=False,
            sort_by=sort_by,
            pos=pos,
        )
        rows = connection.execute(
            sql,
            [language, *term_params, page_size, (page - 1) * page_size],
        ).fetchall()
        term_count = len(terms)
        return [
            KwicMatch(
                sentence_id=str(row[0]),
                document_id=str(row[1]),
                sentence_position=int(row[2]),
                language=str(row[3]),
                keyword_surfaces=tuple(str(value) for value in row[4 : 4 + term_count]),
            )
            for row in rows
        ]

    @staticmethod
    def _sentence_tokens(
        connection: sqlite3.Connection,
        sentence_ids: set[str],
    ) -> dict[str, tuple[tuple[int, str], ...]]:
        if not sentence_ids:
            return {}
        placeholders = ",".join("?" for _ in sentence_ids)
        rows = connection.execute(
            f"""
            SELECT sentence_id, sentence_position, surface
            FROM tokens
            WHERE sentence_id IN ({placeholders})
            ORDER BY sentence_id, sentence_position
            """,
            sorted(sentence_ids),
        ).fetchall()
        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for sentence_id, position, surface in rows:
            grouped[str(sentence_id)].append((int(position), str(surface)))
        return {sentence_id: tuple(tokens) for sentence_id, tokens in grouped.items()}

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
    def _build_hit(
        match: KwicMatch,
        sentence_tokens: tuple[tuple[int, str], ...],
        metadata: dict[str, dict[str, Any]],
        context_size: int,
    ) -> KwicHit:
        by_position = dict(sentence_tokens)
        keyword_length = len(match.keyword_surfaces)
        left_values = [
            by_position[position]
            for position in range(max(1, match.sentence_position - context_size), match.sentence_position)
            if position in by_position
        ]
        right_start = match.sentence_position + keyword_length
        right_values = [
            by_position[position]
            for position in range(right_start, right_start + context_size)
            if position in by_position
        ]
        separator = "" if match.language == "zh" else " "
        sentence = metadata["sentences"].get(match.sentence_id, {})
        document = metadata["documents"].get(match.document_id, {})
        paragraph = metadata["paragraphs"].get(str(sentence.get("paragraph_id", "")), {})
        def neighbor(offset: int) -> str:
            return by_position.get(match.sentence_position + offset, "")

        return KwicHit(
            left=separator.join(left_values),
            keyword=separator.join(match.keyword_surfaces),
            right=separator.join(right_values),
            source_filename=str(document.get("filename", "")),
            document_id=match.document_id,
            sentence_id=match.sentence_id,
            sentence_ordinal=_safe_int(sentence.get("ordinal")),
            paragraph_ordinal=_safe_int(paragraph.get("ordinal")),
            language=match.language,
            l3=neighbor(-3),
            l2=neighbor(-2),
            l1=neighbor(-1),
            r1=neighbor(keyword_length),
            r2=neighbor(keyword_length + 1),
            r3=neighbor(keyword_length + 2),
        )


def query_terms(query: str) -> tuple[str, tuple[str, ...]]:
    language = "zh" if any("\u4e00" <= char <= "\u9fff" for char in query) else "en"
    terms = tuple(
        normalize_token(match.group(0), language) for match in token_matches(query, language)
    )
    if not terms:
        raise ValueError("Query must contain at least one searchable token.")
    if len(terms) > MAX_QUERY_TERMS:
        raise ValueError(f"Query must contain no more than {MAX_QUERY_TERMS} tokens.")
    return language, terms


def sort_offset(sort_by: str, keyword_length: int) -> int:
    distance = int(sort_by[1])
    return -distance if sort_by.startswith("L") else keyword_length + distance - 1


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
