from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.processing.text import normalize_token


class QueryAttribute(StrEnum):
    WORD = "word"
    LEMMA = "lemma"
    POS = "pos"


class MatchOperator(StrEnum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"


@dataclass(frozen=True, slots=True)
class TokenFilter:
    attribute: QueryAttribute
    operator: MatchOperator
    value: str

    def describe(self) -> str:
        if self.operator == MatchOperator.EXACT:
            return f'{self.attribute}="{self.value}"'
        return f"{self.attribute}.{self.operator}({self.value})"


def compile_token_filter(
    token_filter: TokenFilter,
    *,
    alias: str,
    language: str,
) -> tuple[str, list[str]]:
    """Compile one validated filter into parameterized SQLite SQL."""
    expression = _attribute_expression(token_filter.attribute, alias, language)
    value = _normalized_value(token_filter, language)
    if token_filter.operator == MatchOperator.EXACT:
        return f"{expression} = ?", [value]

    escaped = _escape_like(value)
    if token_filter.operator == MatchOperator.WILDCARD:
        pattern = escaped.replace("*", "%").replace("?", "_")
    elif token_filter.operator == MatchOperator.STARTS_WITH:
        pattern = f"{escaped}%"
    elif token_filter.operator == MatchOperator.ENDS_WITH:
        pattern = f"%{escaped}"
    elif token_filter.operator == MatchOperator.CONTAINS:
        pattern = f"%{escaped}%"
    else:  # pragma: no cover - enum construction prevents this branch
        raise ValueError("Unsupported match operator.")
    return f"{expression} LIKE ? ESCAPE '\\'", [pattern]


def _attribute_expression(
    attribute: QueryAttribute,
    alias: str,
    language: str,
) -> str:
    if not alias.startswith("t") or not alias[1:].isdigit():
        raise ValueError("Invalid SQL alias.")
    if attribute == QueryAttribute.WORD:
        return f"{alias}.normalized"
    if attribute == QueryAttribute.POS:
        return f"{alias}.pos"
    lemma = f"COALESCE(NULLIF({alias}.lemma, ''), {alias}.normalized)"
    return f"LOWER({lemma})" if language == "en" else lemma


def _normalized_value(token_filter: TokenFilter, language: str) -> str:
    if token_filter.attribute == QueryAttribute.POS:
        return token_filter.value
    return normalize_token(token_filter.value, language)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
