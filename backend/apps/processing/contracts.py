from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.5"
RECORD_NAMESPACE = uuid.UUID("f304f9dd-a234-4d25-9e56-4eea2aeb6028")


def stable_id(prefix: str, *parts: object) -> str:
    value = ":".join(str(part) for part in parts)
    return f"{prefix}-{uuid.uuid5(RECORD_NAMESPACE, value).hex}"


@dataclass(frozen=True, slots=True)
class SourceFile:
    id: str
    filename: str
    path: Path
    detected_type: str
    language: str
    encoding: str = ""
    size_bytes: int = 0


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: str
    source_file_id: str
    filename: str
    language: str
    title: str
    text_length: int


@dataclass(frozen=True, slots=True)
class ParagraphRecord:
    id: str
    document_id: str
    ordinal: int
    language: str
    text: str


@dataclass(frozen=True, slots=True)
class SentenceRecord:
    id: str
    document_id: str
    paragraph_id: str
    ordinal: int
    language: str
    text: str


@dataclass(frozen=True, slots=True)
class TokenRecord:
    id: str
    document_id: str
    sentence_id: str
    ordinal: int
    language: str
    text: str
    normalized: str
    lemma: str = ""
    pos: str = ""
    start: int = 0
    end: int = 0


@dataclass(frozen=True, slots=True)
class ParallelPairRecord:
    id: str
    ordinal: int
    zh_unit_id: str
    en_unit_id: str
    zh_text: str
    en_text: str
    alignment_unit: str = "sentence"
    method: str = "provided"
    confidence: float = 1.0


@dataclass(slots=True)
class ImportResult:
    source_file_ids: list[str]
    documents: list[DocumentRecord] = field(default_factory=list)
    paragraphs: list[ParagraphRecord] = field(default_factory=list)
    sentences: list[SentenceRecord] = field(default_factory=list)
    tokens: list[TokenRecord] = field(default_factory=list)
    parallel_pairs: list[ParallelPairRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def record_dict(record: object) -> dict[str, Any]:
    return asdict(record)
