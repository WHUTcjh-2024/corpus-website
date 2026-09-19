from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import regex as safe_regex

from apps.processing.text import normalize_token, token_matches

from .contracts import (
    KwicIndexCorrupt,
    KwicIndexUnavailable,
    KwicMatch,
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
