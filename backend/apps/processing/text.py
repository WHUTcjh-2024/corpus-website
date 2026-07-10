from __future__ import annotations

import re

from apps.corpus_intake.classifiers import decode_text

from .contracts import SourceFile


_ZH_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*")
_EN_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])(?:[\"'”’)]*)\s+")
_ZH_TOKEN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_EN_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:\.\d+)?")


def read_source_text(source: SourceFile) -> tuple[str, str]:
    data = source.path.read_bytes()
    if source.encoding and source.encoding not in {"unknown", "utf-8-replace"}:
        try:
            return data.decode(source.encoding), source.encoding
        except (LookupError, UnicodeDecodeError):
            pass
    return decode_text(data)


def split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    return paragraphs or [normalized]


def split_sentences(text: str, language: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    boundary = _ZH_SENTENCE_BOUNDARY if language == "zh" else _EN_SENTENCE_BOUNDARY
    sentences = [part.strip() for part in boundary.split(compact) if part.strip()]
    return sentences or [compact]


def token_matches(text: str, language: str):
    pattern = _ZH_TOKEN if language == "zh" else _EN_TOKEN
    yield from pattern.finditer(text)


def normalize_token(value: str, language: str) -> str:
    return value if language == "zh" else value.casefold()
