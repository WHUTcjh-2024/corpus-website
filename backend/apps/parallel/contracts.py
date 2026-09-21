from __future__ import annotations

from dataclasses import dataclass


ALIGNMENT_UNITS = ("sentence", "paragraph")
SEARCH_SIDES = ("zh", "en")
DISPLAY_MODES = ("search", "browse")
SORT_POSITIONS = (
    "",
    "L5",
    "L4",
    "L3",
    "L2",
    "L1",
    "CEN",
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
)
MAX_CONDITION_LENGTH = 200


class ParallelIndexUnavailable(RuntimeError):
    pass


class ParallelIndexCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParallelQuery:
    mode: str = "search"
    q: str = ""
    search_side: str = "zh"
    zh_contains: str = ""
    en_contains: str = ""
    zh_not_contains: str = ""
    en_not_contains: str = ""
    filename_contains: str = ""
    min_confidence: float = 0.0
    alignment_unit: str = "sentence"
    whole_words: bool = False
    case_sensitive: bool = False
    infer_target_highlights: bool = False
    sort_1: str = ""
    sort_2: str = ""
    sort_3: str = ""
    context_size: int = 20
    nth_entry: int = 1

    def validate(self) -> None:
        if self.mode not in DISPLAY_MODES:
            raise ValueError("mode must be search or browse.")
        if self.search_side not in SEARCH_SIDES:
            raise ValueError("search_side must be zh or en.")
        if self.alignment_unit not in ALIGNMENT_UNITS:
            raise ValueError("alignment_unit must be sentence or paragraph.")
        if not 1 <= self.nth_entry <= 1_000:
            raise ValueError("nth_entry must be between 1 and 1000.")
        if not 5 <= self.context_size <= 100:
            raise ValueError("context_size must be between 5 and 100.")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1.")
        if any(value not in SORT_POSITIONS for value in self.sort_positions):
            raise ValueError("sort positions must be CEN, L1-L5, R1-R5, or blank.")
        values = (
            self.q,
            self.zh_contains,
            self.en_contains,
            self.zh_not_contains,
            self.en_not_contains,
            self.filename_contains,
        )
        if (
            self.mode == "search"
            and not self.q
            and not self.zh_contains
            and not self.en_contains
        ):
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

    @property
    def sort_positions(self) -> tuple[str, ...]:
        return tuple(
            value for value in (self.sort_1, self.sort_2, self.sort_3) if value
        )


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
    zh_filename: str = ""
    en_filename: str = ""
    occurrence_ordinal: int = 1

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
            "provided_structure_order": "人工结构顺序",
            "automatic_length_dp_1_1": "自动长度对齐 1:1",
            "automatic_length_dp_1_2": "自动长度对齐 1:2",
            "automatic_length_dp_2_1": "自动长度对齐 2:1",
            "automatic_length_dp_1_0": "自动对齐缺失英文",
            "automatic_length_dp_0_1": "自动对齐缺失中文",
        }.get(self.method, self.method)

    @property
    def zh_is_gap(self) -> bool:
        return not self.zh_text.strip()

    @property
    def en_is_gap(self) -> bool:
        return not self.en_text.strip()

    @property
    def palette_class(self) -> str:
        """Give both sides of one pair the same deterministic reading color."""
        return f"parallel-track--{(self.global_position - 1) % 6 + 1}"

    @property
    def quality_class(self) -> str:
        if self.zh_is_gap or self.en_is_gap:
            return "gap"
        if self.method in {"provided", "provided_structure_id"}:
            return "verified"
        if self.method in {"provided_paragraph_order", "provided_structure_order"}:
            return "ordered"
        if self.confidence >= 0.85:
            return "high"
        if self.confidence >= 0.65:
            return "review"
        return "low"

    @property
    def quality_label(self) -> str:
        return {
            "gap": "单边缺口",
            "verified": "编号核验",
            "ordered": "顺序核验",
            "high": "自动高置信",
            "review": "自动待抽检",
            "low": "自动低置信",
        }[self.quality_class]


@dataclass(frozen=True, slots=True)
class AlignmentQualitySummary:
    pair_total: int = 0
    verified_count: int = 0
    ordered_count: int = 0
    automatic_count: int = 0
    review_count: int = 0
    gap_count: int = 0

    @property
    def verified_percent(self) -> float:
        if not self.pair_total:
            return 0.0
        return (self.verified_count / self.pair_total) * 100


@dataclass(frozen=True, slots=True)
class ParallelSearchResult:
    query: ParallelQuery
    hits: tuple[ParallelHit, ...]
    total: int
    raw_total: int
    page: int
    page_size: int
    num_pages: int
    auto_target_highlights: tuple[str, ...] = ()
    alignment_quality: AlignmentQualitySummary = AlignmentQualitySummary()

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


def _unique_nonempty(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
