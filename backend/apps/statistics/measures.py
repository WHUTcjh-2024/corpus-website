from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContingencyTable:
    """A validated 2x2 table used by AntConc-compatible statistics."""

    o11: float
    o12: float
    o21: float
    o22: float

    def __post_init__(self) -> None:
        if any(value < 0 or not math.isfinite(value) for value in self.observed):
            raise ValueError("contingency-table values must be finite and non-negative")
        if self.total <= 0:
            raise ValueError("contingency-table total must be greater than zero")

    @classmethod
    def from_marginals(
        cls,
        *,
        cooccurrence: int,
        node_opportunities: int,
        collocate_frequency: int,
        corpus_size: int,
    ) -> "ContingencyTable":
        if cooccurrence > min(node_opportunities, collocate_frequency):
            raise ValueError("cooccurrence exceeds a marginal total")
        if node_opportunities > corpus_size or collocate_frequency > corpus_size:
            raise ValueError("a marginal total exceeds corpus_size")
        o22 = corpus_size - node_opportunities - collocate_frequency + cooccurrence
        return cls(
            float(cooccurrence),
            float(node_opportunities - cooccurrence),
            float(collocate_frequency - cooccurrence),
            float(o22),
        )

    @property
    def observed(self) -> tuple[float, float, float, float]:
        return self.o11, self.o12, self.o21, self.o22

    @property
    def row1(self) -> float:
        return self.o11 + self.o12

    @property
    def row2(self) -> float:
        return self.o21 + self.o22

    @property
    def column1(self) -> float:
        return self.o11 + self.o21

    @property
    def column2(self) -> float:
        return self.o12 + self.o22

    @property
    def total(self) -> float:
        return sum(self.observed)

    @property
    def expected(self) -> tuple[float, float, float, float]:
        total = self.total
        return (
            self.row1 * self.column1 / total,
            self.row1 * self.column2 / total,
            self.row2 * self.column1 / total,
            self.row2 * self.column2 / total,
        )


def association_measures(table: ContingencyTable) -> dict[str, float]:
    """Return the likelihood and effect-size measures listed by AntConc 4.4.2."""

    o11 = table.o11
    e11 = table.expected[0]
    row1 = table.row1
    row2 = table.row2
    column1 = table.column1
    adjusted_o21 = table.o21 if table.o21 else 0.5

    dice = _divide(2 * o11, row1 + column1)
    log_dice = 14 + _log2(dice)
    log_ratio = _log2(
        _divide(_divide(o11, row1), _divide(adjusted_o21, row2))
    )
    mi = _log2(_divide(o11, e11))
    measures = {
        "dice": dice,
        "log_dice": log_dice,
        "log_ratio": log_ratio,
        "mi": mi,
        "mi2": _log2(_divide(o11**2, e11)),
        "mi3": _log2(_divide(o11**3, e11)),
        "minimum_sensitivity": min(_divide(o11, row1), _divide(o11, column1)),
        "mu": _divide(o11, e11),
        "rrf": _divide(row2 * o11, row1 * adjusted_o21),
        "drf": _divide(o11, row1) - _divide(table.o21, row2),
        "z_score": _divide(o11 - e11, math.sqrt(e11)) if e11 else 0.0,
        "t_score": _divide(o11 - e11, math.sqrt(o11)) if o11 else 0.0,
        "chi_square": chi_square(table),
        "chi_square_yates": chi_square(table, yates=True),
        "log_likelihood": log_likelihood(table),
        "log_likelihood_simple": log_likelihood(table, simplified=True),
    }
    measures["p_value"] = chi_square_p_value(measures["log_likelihood"])
    return measures


def chi_square(table: ContingencyTable, *, yates: bool = False) -> float:
    correction = 0.5 if yates else 0.0
    return sum(
        max(0.0, abs(observed - expected) - correction) ** 2 / expected
        for observed, expected in zip(table.observed, table.expected, strict=True)
        if expected > 0
    )


def log_likelihood(table: ContingencyTable, *, simplified: bool = False) -> float:
    pairs = zip(table.observed, table.expected, strict=True)
    if simplified:
        pairs = zip(
            (table.o11, table.o21),
            (table.expected[0], table.expected[2]),
            strict=True,
        )
    return 2 * sum(
        observed * math.log(observed / expected)
        for observed, expected in pairs
        if observed > 0 and expected > 0
    )


def chi_square_p_value(statistic: float) -> float:
    """Survival function for chi-square with one degree of freedom."""

    if statistic < 0 or not math.isfinite(statistic):
        raise ValueError("statistic must be finite and non-negative")
    return math.erfc(math.sqrt(statistic / 2))


def _divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _log2(value: float) -> float:
    return math.log2(value) if value > 0 else 0.0
