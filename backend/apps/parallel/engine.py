from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from apps.processing.text import token_matches


ALIGNMENT_UNITS = ("sentence", "paragraph")
SEARCH_SIDES = ("zh", "en")
MAX_CONDITION_LENGTH = 200
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


class ParallelIndexUnavailable(RuntimeError):
    pass


class ParallelIndexCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParallelQuery:
    q: str = ""
    search_side: str = "zh"
    zh_contains: str = ""
    en_contains: str = ""
    zh_not_contains: str = ""
    en_not_contains: str = ""
    alignment_unit: str = "sentence"

    def validate(self) -> None:
        if self.search_side not in SEARCH_SIDES:
            raise ValueError("search_side must be zh or en.")
        if self.alignment_unit not in ALIGNMENT_UNITS:
            raise ValueError("alignment_unit must be sentence or paragraph.")
        values = (
            self.q,
            self.zh_contains,
            self.en_contains,
            self.zh_not_contains,
            self.en_not_contains,
        )
        if not self.q and not self.zh_contains and not self.en_contains:
            raise ValueError("至少填写一个主检索词或包含条件。")
        if any(len(value) > MAX_CONDITION_LENGTH for value in values):
            raise ValueError(f"单个检索条件不能超过 {MAX_CONDITION_LENGTH} 个字符。")

    @property
    def zh_highlights(self) -> tuple[str, ...]:
        values = [self.zh_contains]
        if self.search_side == "zh":
            values.insert(0, self.q)
        return _unique_nonempty(values)

    @property
    def en_highlights(self) -> tuple[str, ...]:
        values = [self.en_contains]
        if self.search_side == "en":
            values.insert(0, self.q)
        return _unique_nonempty(values)


@dataclass(frozen=True, slots=True)
class HighlightFragment:
    text: str
    matched: bool


@dataclass(frozen=True, slots=True)
class ParallelHit:
    global_position: int
    pair_id: str
    pair_ordinal: int
    zh_text: str
    en_text: str
    zh_fragments: tuple[HighlightFragment, ...]
    en_fragments: tuple[HighlightFragment, ...]
    alignment_unit: str
    method: str
    confidence: float

    @property
    def alignment_unit_display(self) -> str:
        return {"sentence": "句子对齐", "paragraph": "段落对齐"}.get(
            self.alignment_unit,
            self.alignment_unit,
        )

    @property
    def method_display(self) -> str:
        return {
            "provided": "人工提供",
            "provided_paragraph_order": "人工段落顺序",
            "provided_structure_id": "人工结构编号",
        }.get(self.method, self.method)


@dataclass(frozen=True, slots=True)
class ParallelSearchResult:
    query: ParallelQuery
    hits: tuple[ParallelHit, ...]
    total: int
    page: int
    page_size: int
    num_pages: int
    auto_target_highlights: tuple[str, ...] = ()

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


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

        where_sql, parameters = _build_filter(query)
        try:
            with closing(self._connect()) as connection:
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM parallel_pairs WHERE {where_sql}",
                        parameters,
                    ).fetchone()[0]
                )
                num_pages = max(1, math.ceil(total / page_size))
                effective_page = min(page, num_pages)
                rows = connection.execute(
                    f"""
                    SELECT global_position, pair_id, pair_ordinal, zh_text, en_text,
                           alignment_unit, method, confidence
                    FROM parallel_pairs
                    WHERE {where_sql}
                    ORDER BY global_position
                    LIMIT ? OFFSET ?
                    """,
                    (*parameters, page_size, (effective_page - 1) * page_size),
                ).fetchall()
                auto_target_highlights = _infer_target_highlights(
                    connection,
                    query,
                    where_sql=where_sql,
                    parameters=parameters,
                    total=total,
                )
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引损坏，请重新加工该语料库。") from exc

        hits = tuple(
            _row_to_hit(
                row,
                query,
                auto_target_highlights=auto_target_highlights,
            )
            for row in rows
        )
        return ParallelSearchResult(
            query=query,
            hits=hits,
            total=total,
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
                rows = connection.execute(
                    """
                    SELECT global_position, pair_id, pair_ordinal, zh_text, en_text,
                           alignment_unit, method, confidence
                    FROM parallel_pairs
                    WHERE alignment_unit = ?
                    ORDER BY global_position
                    LIMIT ?
                    """,
                    (alignment_unit, limit),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引损坏，请重新加工该语料库。") from exc
        return tuple(_row_to_hit(row, query) for row in rows)

    def iter_export_rows(self, query: ParallelQuery) -> Iterator[tuple[object, ...]]:
        query.validate()
        where_sql, parameters = _build_filter(query)
        try:
            with closing(self._connect()) as connection:
                cursor = connection.execute(
                    f"""
                    SELECT global_position, pair_ordinal, zh_text, en_text,
                           alignment_unit, method, confidence
                    FROM parallel_pairs
                    WHERE {where_sql}
                    ORDER BY global_position
                    """,
                    parameters,
                )
                while rows := cursor.fetchmany(500):
                    yield from rows
        except sqlite3.DatabaseError as exc:
            raise ParallelIndexCorrupt("平行语料索引损坏，请重新加工该语料库。") from exc

    def _connect(self) -> sqlite3.Connection:
        if not self.index_path.is_file():
            raise ParallelIndexUnavailable("平行语料索引不存在，请先加工该语料库。")
        try:
            connection = sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='parallel_pairs'"
            ).fetchone()
            if table_exists is None:
                connection.close()
                raise ParallelIndexUnavailable("当前索引版本不含平行检索表，请重新加工该语料库。")
            return connection
        except ParallelIndexUnavailable:
            raise
        except sqlite3.Error as exc:
            raise ParallelIndexUnavailable("无法打开平行语料索引。") from exc


def normalize_condition(value: str) -> str:
    return " ".join(value.split())


def _build_filter(query: ParallelQuery) -> tuple[str, tuple[str, ...]]:
    clauses = ["alignment_unit = ?"]
    parameters = [query.alignment_unit]
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
        column = f"{side}_normalized"
        for value in _unique_nonempty(positive[side]):
            clauses.append(f"instr({column}, ?) > 0")
            parameters.append(value.casefold())
        if negative[side]:
            clauses.append(f"instr({column}, ?) = 0")
            parameters.append(negative[side].casefold())
    return " AND ".join(clauses), tuple(parameters)


def _row_to_hit(
    row: tuple[object, ...],
    query: ParallelQuery,
    *,
    auto_target_highlights: tuple[str, ...] = (),
) -> ParallelHit:
    zh_text = str(row[3])
    en_text = str(row[4])
    zh_highlights = query.zh_highlights
    en_highlights = query.en_highlights
    if query.search_side == "zh":
        en_highlights = _merge_highlights(en_highlights, auto_target_highlights)
    else:
        zh_highlights = _merge_highlights(zh_highlights, auto_target_highlights)
    return ParallelHit(
        global_position=int(row[0]),
        pair_id=str(row[1]),
        pair_ordinal=int(row[2]),
        zh_text=zh_text,
        en_text=en_text,
        zh_fragments=highlight_fragments(zh_text, zh_highlights),
        en_fragments=highlight_fragments(en_text, en_highlights),
        alignment_unit=str(row[5]),
        method=str(row[6]),
        confidence=float(row[7]),
    )


def highlight_fragments(text: str, terms: tuple[str, ...]) -> tuple[HighlightFragment, ...]:
    ordered = sorted((term for term in terms if term), key=len, reverse=True)
    if not ordered:
        return (HighlightFragment(text=text, matched=False),)
    pattern = re.compile("|".join(re.escape(term) for term in ordered), flags=re.IGNORECASE)
    fragments: list[HighlightFragment] = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            fragments.append(HighlightFragment(text[position : match.start()], False))
        fragments.append(HighlightFragment(match.group(0), True))
        position = match.end()
    if position < len(text):
        fragments.append(HighlightFragment(text[position:], False))
    return tuple(fragments) or (HighlightFragment(text=text, matched=False),)


def _unique_nonempty(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _infer_target_highlights(
    connection: sqlite3.Connection,
    query: ParallelQuery,
    *,
    where_sql: str,
    parameters: tuple[str, ...],
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
