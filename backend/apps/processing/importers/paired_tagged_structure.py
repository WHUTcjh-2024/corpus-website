from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ..contracts import (
    DocumentRecord,
    ImportResult,
    ParagraphRecord,
    ParallelPairRecord,
    SentenceRecord,
    SourceFile,
    TokenRecord,
    stable_id,
)
from ..exceptions import ProcessingError
from ..text import normalize_token, read_source_text
from .base import BaseImporter


_ELEMENT_RE_TEMPLATE = r"<{tag}\b(?P<attrs>[^>]*)>(?P<body>.*?)<\s*/\s*{tag}\s*>"
_PARAGRAPH_RE = re.compile(
    _ELEMENT_RE_TEMPLATE.format(tag="p"),
    flags=re.IGNORECASE | re.DOTALL,
)
_SENTENCE_RE = re.compile(
    r"<s\b(?P<attrs>[^>]*)>(?P<body>.*?)(?:<\s*/\s*s\s*>|/\s*s\s*>)",
    flags=re.IGNORECASE | re.DOTALL,
)
_NUMBER_ATTRIBUTE_RE = re.compile(
    r"\bn\s*=\s*(?:[\"'](?P<quoted>\d+)[\"']|(?P<plain>\d+))",
    flags=re.IGNORECASE,
)
_HEAD_RE = re.compile(
    _ELEMENT_RE_TEMPLATE.format(tag="head"),
    flags=re.IGNORECASE | re.DOTALL,
)
_RESIDUAL_TAG_RE = re.compile(r"<[^>]*>")
_ZH_TAGGED_TOKEN_RE = re.compile(
    r"^(?P<word>.+)/(?P<pos>[A-Za-z][A-Za-z0-9_-]*)$"
)
_EN_TAGGED_TOKEN_RE = re.compile(r"^(?P<word>.+)_(?P<pos>[^_\s]+)$")
_LEADING_ALIGNMENT_MARK_RE = re.compile(r"^(?:--?>|<--?)")
_NO_SPACE_BEFORE = frozenset(",.!?;:%)]}，。！？；：％）】》、")
_NO_SPACE_AFTER = frozenset("([{（【《")


@dataclass(frozen=True, slots=True)
class ParsedToken:
    text: str
    pos: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ParsedSentence:
    number: int
    text: str
    tokens: tuple[ParsedToken, ...]


@dataclass(frozen=True, slots=True)
class ParsedParagraph:
    number: int
    text: str
    sentences: tuple[ParsedSentence, ...]


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str
    paragraphs: tuple[ParsedParagraph, ...]


@dataclass(frozen=True, slots=True)
class ImportedStructure:
    result: ImportResult
    document: ParsedDocument
    paragraph_ids: dict[int, str]
    sentence_ids: dict[int, str]


class PairedTaggedStructureImporter(BaseImporter):
    """Import an explicitly numbered, POS-tagged bilingual file pair.

    The teacher-provided ``p@n`` and ``s@n`` identifiers are authoritative.
    The parser tolerates harmless XML-like variants such as ``< /s>`` without
    changing the source files, but rejects missing, duplicate, or cross-language
    numbering mismatches.
    """

    name = "paired_tagged_structure_provided"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        zh_source, en_source = _select_language_pair(sources)
        zh = _import_tagged_source(zh_source)
        en = _import_tagged_source(en_source)
        _validate_alignment(zh.document, en.document)

        result = ImportResult(
            source_file_ids=[zh_source.id, en_source.id],
            documents=[*zh.result.documents, *en.result.documents],
            paragraphs=[*zh.result.paragraphs, *en.result.paragraphs],
            sentences=[*zh.result.sentences, *en.result.sentences],
            tokens=[*zh.result.tokens, *en.result.tokens],
            warnings=[*zh.result.warnings, *en.result.warnings],
        )

        sentence_ordinal = 0
        for zh_paragraph, en_paragraph in zip(
            zh.document.paragraphs,
            en.document.paragraphs,
            strict=True,
        ):
            for zh_sentence, en_sentence in zip(
                zh_paragraph.sentences,
                en_paragraph.sentences,
                strict=True,
            ):
                sentence_ordinal += 1
                result.parallel_pairs.append(
                    ParallelPairRecord(
                        id=stable_id(
                            "pair",
                            zh_source.id,
                            en_source.id,
                            "sentence",
                            zh_sentence.number,
                        ),
                        ordinal=sentence_ordinal,
                        zh_unit_id=zh.sentence_ids[zh_sentence.number],
                        en_unit_id=en.sentence_ids[en_sentence.number],
                        zh_text=zh_sentence.text,
                        en_text=en_sentence.text,
                        alignment_unit="sentence",
                        method="provided_structure_id",
                        confidence=1.0,
                    )
                )

        for paragraph_ordinal, (zh_paragraph, en_paragraph) in enumerate(
            zip(zh.document.paragraphs, en.document.paragraphs, strict=True),
            start=1,
        ):
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id(
                        "pair",
                        zh_source.id,
                        en_source.id,
                        "paragraph",
                        zh_paragraph.number,
                    ),
                    ordinal=paragraph_ordinal,
                    zh_unit_id=zh.paragraph_ids[zh_paragraph.number],
                    en_unit_id=en.paragraph_ids[en_paragraph.number],
                    zh_text=zh_paragraph.text,
                    en_text=en_paragraph.text,
                    alignment_unit="paragraph",
                    method="provided_structure_id",
                    confidence=1.0,
                )
            )
        yield result


def _select_language_pair(sources: Sequence[SourceFile]) -> tuple[SourceFile, SourceFile]:
    zh_sources = [source for source in sources if source.language == "zh"]
    en_sources = [source for source in sources if source.language == "en"]
    if len(zh_sources) != 1 or len(en_sources) != 1:
        raise ProcessingError(
            "PairedTaggedStructureImporter requires exactly one zh file and one en file."
        )
    return zh_sources[0], en_sources[0]


def _import_tagged_source(source: SourceFile) -> ImportedStructure:
    text, _ = read_source_text(source)
    document = _parse_document(text, source)
    document_id = stable_id("doc", source.id)
    result = ImportResult(
        source_file_ids=[source.id],
        documents=[
            DocumentRecord(
                id=document_id,
                source_file_id=source.id,
                filename=source.filename,
                language=source.language,
                title=document.title[:200] or source.filename,
                text_length=len(text),
            )
        ],
    )
    unknown_pos_count = sum(
        token.pos == "UNK"
        for paragraph in document.paragraphs
        for sentence in paragraph.sentences
        for token in sentence.tokens
    )
    if unknown_pos_count:
        result.warnings.append(
            f"{source.filename}: preserved {unknown_pos_count} untagged token(s) with POS=UNK."
        )
    paragraph_ids: dict[int, str] = {}
    sentence_ids: dict[int, str] = {}
    sentence_ordinal = 0
    for paragraph in document.paragraphs:
        paragraph_id = stable_id("para", source.id, paragraph.number)
        paragraph_ids[paragraph.number] = paragraph_id
        result.paragraphs.append(
            ParagraphRecord(
                id=paragraph_id,
                document_id=document_id,
                ordinal=paragraph.number,
                language=source.language,
                text=paragraph.text,
            )
        )
        for sentence in paragraph.sentences:
            sentence_ordinal += 1
            sentence_id = stable_id("sent", source.id, sentence.number)
            sentence_ids[sentence.number] = sentence_id
            result.sentences.append(
                SentenceRecord(
                    id=sentence_id,
                    document_id=document_id,
                    paragraph_id=paragraph_id,
                    ordinal=sentence_ordinal,
                    language=source.language,
                    text=sentence.text,
                )
            )
            for token_ordinal, token in enumerate(sentence.tokens, start=1):
                result.tokens.append(
                    TokenRecord(
                        id=stable_id("tok", source.id, sentence.number, token_ordinal),
                        document_id=document_id,
                        sentence_id=sentence_id,
                        ordinal=token_ordinal,
                        language=source.language,
                        text=token.text,
                        normalized=normalize_token(token.text, source.language),
                        pos=token.pos,
                        start=token.start,
                        end=token.end,
                    )
                )
    return ImportedStructure(
        result=result,
        document=document,
        paragraph_ids=paragraph_ids,
        sentence_ids=sentence_ids,
    )


def _parse_document(text: str, source: SourceFile) -> ParsedDocument:
    language = source.language
    if language not in {"zh", "en"}:
        raise ProcessingError(f"Tagged structure requires zh/en language: {source.filename}")

    head_match = _HEAD_RE.search(text)
    title = ""
    if head_match:
        title_tokens = _parse_tokens(head_match.group("body"), language)
        title, _ = _surface_and_offsets(title_tokens, language)

    paragraphs: list[ParsedParagraph] = []
    seen_paragraphs: set[int] = set()
    seen_sentences: set[int] = set()
    for paragraph_match in _PARAGRAPH_RE.finditer(text):
        number = _number_attribute(paragraph_match.group("attrs"))
        if number is None:
            continue
        if number in seen_paragraphs:
            raise ProcessingError(
                f"Duplicate paragraph n={number} in tagged source: {source.filename}"
            )
        seen_paragraphs.add(number)
        sentences: list[ParsedSentence] = []
        for sentence_match in _SENTENCE_RE.finditer(paragraph_match.group("body")):
            sentence_number = _number_attribute(sentence_match.group("attrs"))
            if sentence_number is None:
                raise ProcessingError(
                    f"Sentence without n attribute in tagged source: {source.filename}"
                )
            if sentence_number in seen_sentences:
                raise ProcessingError(
                    f"Duplicate sentence n={sentence_number} in tagged source: {source.filename}"
                )
            seen_sentences.add(sentence_number)
            parsed_tokens = _parse_tokens(
                sentence_match.group("body"),
                language,
            )
            sentence_text, offsets = _surface_and_offsets(parsed_tokens, language)
            tokens = tuple(
                ParsedToken(word, pos, start, end)
                for (word, pos), (start, end) in zip(parsed_tokens, offsets, strict=True)
            )
            sentences.append(ParsedSentence(sentence_number, sentence_text, tokens))
        if not sentences:
            raise ProcessingError(
                f"Paragraph n={number} has no numbered sentence: {source.filename}"
            )
        separator = "" if language == "zh" else " "
        paragraph_text = separator.join(sentence.text for sentence in sentences)
        paragraphs.append(ParsedParagraph(number, paragraph_text, tuple(sentences)))

    if not paragraphs:
        raise ProcessingError(f"Tagged source contains no numbered paragraph: {source.filename}")
    return ParsedDocument(title=title, paragraphs=tuple(paragraphs))


def _parse_tokens(body: str, language: str) -> tuple[tuple[str, str], ...]:
    value = _RESIDUAL_TAG_RE.sub(" ", body)
    pattern = _ZH_TAGGED_TOKEN_RE if language == "zh" else _EN_TAGGED_TOKEN_RE
    parsed: list[tuple[str, str]] = []
    for raw_token in value.split():
        match = pattern.match(raw_token)
        if not match:
            if raw_token in {"-->", "->"}:
                continue
            parsed.append(
                (raw_token, "PUNCT" if _is_punctuation_token(raw_token) else "UNK")
            )
            continue
        word = _LEADING_ALIGNMENT_MARK_RE.sub("", match.group("word"))
        if word:
            parsed.append((word, match.group("pos")))
    return tuple(parsed)


def _is_punctuation_token(value: str) -> bool:
    return bool(value) and all(
        unicodedata.category(character).startswith(("P", "S")) for character in value
    )


def _surface_and_offsets(
    tokens: tuple[tuple[str, str], ...],
    language: str,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    surface = ""
    offsets: list[tuple[int, int]] = []
    previous = ""
    for word, _ in tokens:
        needs_space = (
            language == "en"
            and bool(surface)
            and word[0] not in _NO_SPACE_BEFORE
            and (not previous or previous[-1] not in _NO_SPACE_AFTER)
        )
        if needs_space:
            surface += " "
        start = len(surface)
        surface += word
        offsets.append((start, len(surface)))
        previous = word
    return surface, tuple(offsets)


def _number_attribute(attrs: str) -> int | None:
    match = _NUMBER_ATTRIBUTE_RE.search(attrs)
    if match is None:
        return None
    return int(match.group("quoted") or match.group("plain"))


def _validate_alignment(zh: ParsedDocument, en: ParsedDocument) -> None:
    zh_paragraph_numbers = tuple(paragraph.number for paragraph in zh.paragraphs)
    en_paragraph_numbers = tuple(paragraph.number for paragraph in en.paragraphs)
    if zh_paragraph_numbers != en_paragraph_numbers:
        raise ProcessingError("Tagged paragraph n identifiers differ between zh and en files.")
    for zh_paragraph, en_paragraph in zip(zh.paragraphs, en.paragraphs, strict=True):
        zh_sentence_numbers = tuple(sentence.number for sentence in zh_paragraph.sentences)
        en_sentence_numbers = tuple(sentence.number for sentence in en_paragraph.sentences)
        if zh_sentence_numbers != en_sentence_numbers:
            raise ProcessingError(
                "Tagged sentence n identifiers differ in paragraph "
                f"{zh_paragraph.number}: zh={zh_sentence_numbers}, en={en_sentence_numbers}."
            )
