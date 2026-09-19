from __future__ import annotations

import math
import sqlite3
import unicodedata

from apps.processing.text import normalize_token

from .contracts import (
    WORDCLOUD_HEIGHT,
    WORDCLOUD_WIDTH,
    WordcloudTerm,
)


SUPPORTED_LANGUAGES = ("zh", "en")
PAGE_SIZES = (20, 50, 100)
WORDCLOUD_THEMES = {
    "ocean": ("#0f4c81", "#1261a0", "#1778b5", "#1597a5", "#2d9cdb", "#58b6d9"),
    "forest": ("#174c3c", "#236b4e", "#2f855a", "#479f76", "#68b984", "#8acb88"),
    "sunset": ("#713c67", "#9a3f62", "#c94c5c", "#e76f51", "#f49d5b", "#efbd68"),
}


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


def _plot_bins(
    connection: sqlite3.Connection,
    language: str,
    terms: tuple[str, ...],
    bin_count: int,
) -> tuple[list[tuple], int]:
    node_sql, node_parameters = _node_match_sql(language, terms)
    total = int(
        connection.execute(
            f"SELECT COUNT(*) FROM ({node_sql}) node",
            node_parameters,
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
                    ELSE MIN(?, CAST(
                        (node.global_position - bounds.first_position) * ? * 1.0 /
                        (bounds.last_position - bounds.first_position)
                        AS INTEGER
                    ))
               END AS bin_number,
               COUNT(*) AS hit_count,
               bounds.first_position
        FROM node
        JOIN bounds ON bounds.document_id = node.document_id
        GROUP BY node.document_id, bin_number
        ORDER BY bounds.first_position, bin_number
        """,
        [*node_parameters, language, bin_count - 1, bin_count - 1],
    ).fetchall()
    return rows, total


def _positional_dispersion(bins: dict[int, int], bin_count: int) -> float:
    total = sum(bins.values())
    if total <= 1 or bin_count <= 1:
        return 0.0
    uniform = 1 / bin_count
    deviation = sum(
        abs(bins.get(index, 0) / total - uniform)
        for index in range(bin_count)
    )
    maximum_deviation = 2 * (1 - uniform)
    return max(0.0, min(1.0, 1 - deviation / maximum_deviation))


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
