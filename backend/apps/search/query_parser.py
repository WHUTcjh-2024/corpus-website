from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from apps.processing.text import normalize_token, token_matches

from .filters import MatchOperator, QueryAttribute, TokenFilter
from .kwic import MAX_QUERY_TERMS


MAX_QUERY_LENGTH = 500
MAX_FILTER_VALUE_LENGTH = 100
_FUNCTIONS = {
    "starts_with": MatchOperator.STARTS_WITH,
    "ends_with": MatchOperator.ENDS_WITH,
    "contains": MatchOperator.CONTAINS,
}
_BRACKET_PATTERN = re.compile(
    r'^\[\s*(word|lemma|pos)\s*=\s*"([^"\r\n]+)"\s*\]$',
    re.IGNORECASE,
)
_FUNCTION_PATTERN = re.compile(
    r'^(starts_with|ends_with|contains)\((?:"([^"\r\n]+)"|([^()\s]+))\)$',
    re.IGNORECASE,
)


class QuerySyntaxError(ValueError):
    """A safe, user-visible error raised for an unsupported query expression."""


@dataclass(frozen=True, slots=True)
class QueryPlan:
    source: str
    language: str
    filters: tuple[TokenFilter, ...]

    @property
    def description(self) -> str:
        return " + ".join(token_filter.describe() for token_filter in self.filters)


def parse_query(query: str, *, language: str) -> QueryPlan:
    source = " ".join(query.split())
    if not source:
        raise QuerySyntaxError("查询表达式不能为空。")
    if len(source) > MAX_QUERY_LENGTH:
        raise QuerySyntaxError(f"查询表达式不能超过 {MAX_QUERY_LENGTH} 个字符。")
    if language not in {"zh", "en"}:
        raise QuerySyntaxError("查询语言必须是中文或英文。")
    if any(ord(character) < 32 for character in source):
        raise QuerySyntaxError("查询表达式不能包含控制字符。")

    filters: list[TokenFilter] = []
    for item_type, value in _scan_items(source):
        if item_type == "quoted":
            filters.extend(_phrase_filters(value, language))
        elif item_type == "bracket":
            filters.append(_bracket_filter(value))
        elif item_type == "function":
            filters.append(_function_filter(value))
        else:
            filters.extend(_bare_filters(value, language))
        if len(filters) > MAX_QUERY_TERMS:
            raise QuerySyntaxError(f"查询最多包含 {MAX_QUERY_TERMS} 个 Token 条件。")
    if not filters:
        raise QuerySyntaxError("查询表达式没有可执行的 Token 条件。")
    return QueryPlan(source=source, language=language, filters=tuple(filters))


def _scan_items(source: str) -> Iterator[tuple[str, str]]:
    index = 0
    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        if index >= len(source):
            return
        character = source[index]
        if character == '"':
            value, index = _read_quoted(source, index)
            yield "quoted", value
            continue
        if character == "[":
            value, index = _read_group(source, index, "]", "方括号")
            yield "bracket", value
            continue
        function_match = re.match(r"[A-Za-z_]+\(", source[index:])
        if function_match:
            value, index = _read_group(source, index, ")", "函数括号")
            yield "function", value
            continue
        end = index
        while end < len(source) and not source[end].isspace():
            if source[end] in {'"', "[", "]", "(", ")"}:
                raise QuerySyntaxError(f"位置 {end + 1} 存在未转义的保留符号。")
            end += 1
        yield "bare", source[index:end]
        index = end


def _read_quoted(source: str, start: int) -> tuple[str, int]:
    end = source.find('"', start + 1)
    if end < 0:
        raise QuerySyntaxError(f"位置 {start + 1} 的双引号没有闭合。")
    value = source[start + 1 : end]
    if not value.strip():
        raise QuerySyntaxError("引号中的短语不能为空。")
    if end + 1 < len(source) and not source[end + 1].isspace():
        raise QuerySyntaxError(f"位置 {end + 2} 前缺少空格。")
    return value, end + 1


def _read_group(source: str, start: int, closing: str, label: str) -> tuple[str, int]:
    in_quotes = False
    for index in range(start, len(source)):
        character = source[index]
        if character == '"':
            in_quotes = not in_quotes
        elif character == closing and not in_quotes:
            if index + 1 < len(source) and not source[index + 1].isspace():
                raise QuerySyntaxError(f"位置 {index + 2} 前缺少空格。")
            return source[start : index + 1], index + 1
    raise QuerySyntaxError(f"位置 {start + 1} 的{label}没有闭合。")


def _phrase_filters(value: str, language: str) -> list[TokenFilter]:
    _validate_value(value, allow_wildcards=False)
    terms = [match.group(0) for match in token_matches(value, language)]
    if not terms:
        raise QuerySyntaxError("短语中没有可检索的词项。")
    return [_exact_word(term, language) for term in terms]


def _bare_filters(value: str, language: str) -> list[TokenFilter]:
    if not value:
        raise QuerySyntaxError("查询中存在空词项。")
    if "*" in value or "?" in value:
        _validate_value(value, allow_wildcards=True)
        if not value.replace("*", "").replace("?", ""):
            raise QuerySyntaxError("通配符必须至少包含一个普通字符。")
        return [
            TokenFilter(
                QueryAttribute.WORD,
                MatchOperator.WILDCARD,
                normalize_token(value, language),
            )
        ]
    _validate_value(value, allow_wildcards=False)
    terms = [match.group(0) for match in token_matches(value, language)]
    if not terms:
        raise QuerySyntaxError(f"词项“{value}”不包含可检索字符。")
    return [_exact_word(term, language) for term in terms]


def _bracket_filter(value: str) -> TokenFilter:
    match = _BRACKET_PATTERN.fullmatch(value)
    if not match:
        raise QuerySyntaxError(
            '属性条件格式应为 [word="value"]、[lemma="value"] 或 [pos="value"]。'
        )
    attribute = QueryAttribute(match.group(1).lower())
    raw_value = match.group(2).strip()
    _validate_value(raw_value, allow_wildcards=True)
    operator = (
        MatchOperator.WILDCARD
        if "*" in raw_value or "?" in raw_value
        else MatchOperator.EXACT
    )
    if operator == MatchOperator.WILDCARD and not raw_value.replace("*", "").replace("?", ""):
        raise QuerySyntaxError("属性通配符必须至少包含一个普通字符。")
    return TokenFilter(attribute, operator, raw_value)


def _function_filter(value: str) -> TokenFilter:
    match = _FUNCTION_PATTERN.fullmatch(value)
    if not match:
        raise QuerySyntaxError(
            "函数格式应为 starts_with(value)、ends_with(value) 或 contains(value)。"
        )
    function = match.group(1).lower()
    raw_value = (match.group(2) or match.group(3) or "").strip()
    _validate_value(raw_value, allow_wildcards=False)
    return TokenFilter(QueryAttribute.WORD, _FUNCTIONS[function], raw_value)


def _exact_word(value: str, language: str) -> TokenFilter:
    normalized = normalize_token(value, language)
    _validate_value(normalized, allow_wildcards=False)
    return TokenFilter(QueryAttribute.WORD, MatchOperator.EXACT, normalized)


def _validate_value(value: str, *, allow_wildcards: bool) -> None:
    if not value or value.isspace():
        raise QuerySyntaxError("查询值不能为空。")
    if len(value) > MAX_FILTER_VALUE_LENGTH:
        raise QuerySyntaxError(f"单个查询值不能超过 {MAX_FILTER_VALUE_LENGTH} 个字符。")
    if any(ord(character) < 32 for character in value):
        raise QuerySyntaxError("查询值不能包含控制字符。")
    forbidden = {'"', "[", "]", "(", ")", ";"}
    if not allow_wildcards:
        forbidden.update({"*", "?"})
    invalid = next((character for character in value if character in forbidden), "")
    if invalid:
        raise QuerySyntaxError(f"查询值包含不允许的符号：{invalid}")
