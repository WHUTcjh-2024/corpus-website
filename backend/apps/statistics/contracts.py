from __future__ import annotations

from dataclasses import dataclass


WORDCLOUD_WIDTH = 1000
WORDCLOUD_HEIGHT = 560


class StatisticsIndexUnavailable(RuntimeError):
    pass


class StatisticsIndexCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrequencyRow:
    rank: int
    term: str
    frequency: int
    document_range: int
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
    min_frequency: int
    min_range: int
    sort_by: str
    include_punctuation: bool
    display_type: str = "type"
    case_sensitive: bool = False
    invert_order: bool = False

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
    document_range: int
    slot_type_count: int = 0
    slot_type_token_ratio: float = 0.0
    slot_entropy: float = 0.0


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
    min_range: int
    filter_text: str
    sort_by: str
    include_punctuation: bool
    open_slot: int = 0

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages


@dataclass(frozen=True, slots=True)
class ClusterRow:
    rank: int
    cluster: str
    frequency: int
    document_range: int
    per_million: float
    transition_probability: float


@dataclass(frozen=True, slots=True)
class ClusterPage:
    rows: tuple[ClusterRow, ...]
    total_types: int
    total_tokens: int
    page: int
    page_size: int
    num_pages: int
    query: str
    language: str
    cluster_size: int
    query_position: str
    min_frequency: int
    min_range: int
    sort_by: str
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
    left_frequency: int
    right_frequency: int
    document_range: int
    corpus_frequency: int
    mutual_information: float
    t_score: float
    log_dice: float
    dice: float
    mi2: float
    mi3: float
    minimum_sensitivity: float
    mu: float
    rrf: float
    drf: float
    z_score: float
    log_ratio: float
    log_likelihood: float
    chi_square: float
    p_value: float


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
    min_range: int
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
    overlay_count: int = 0
    overlay_opacity: float = 0.0


@dataclass(frozen=True, slots=True)
class PlotDocument:
    document_id: str
    filename: str
    hit_count: int
    cells: tuple[PlotCell, ...]
    token_count: int = 0
    normalized_frequency: float = 0.0
    dispersion: float = 0.0


@dataclass(frozen=True, slots=True)
class ConcordancePlot:
    query: str
    language: str
    total: int
    documents: tuple[PlotDocument, ...]
    overlay_query: str = ""
    overlay_total: int = 0


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
