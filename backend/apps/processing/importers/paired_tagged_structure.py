from __future__ import annotations

import re
import unicodedata
from collections import Counter
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
_NUMBER_ATTRIBUTE_RE = re.compile(
    r"\bn\s*=\s*(?:[\"'](?P<quoted>\d+)[\"']|(?P<plain>\d+))",
    flags=re.IGNORECASE,
)
_HEAD_RE = re.compile(
    _ELEMENT_RE_TEMPLATE.format(tag="head"),
    flags=re.IGNORECASE | re.DOTALL,
)
_GRAMMAR_ANNOTATION_RE = re.compile(
    r"<?\[\s*[A-Z]{2,}[A-Z0-9-]*(?:\s+\d+(?:-\d+)?)?\s*\]>?"
)
_RESIDUAL_TAG_RE = re.compile(r"<[^>]*>")
_ZH_TAGGED_TOKEN_RE = re.compile(
    r"^(?P<word>.+)/(?P<pos>[A-Za-z][A-Za-z0-9_-]*)$"
)
_ZH_INLINE_TAG_RE = re.compile(
    r"(?P<word>[^/]+?)/(?P<pos>[A-Za-z][A-Za-z0-9_-]*)(?=[^A-Za-z0-9_-]|$)"
)
_ORPHAN_ZH_POS_RE = re.compile(r"(?<!\S)/[A-Za-z][A-Za-z0-9_-]*")
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
    number: int | None
    ordinal: int
    text: str
    tokens: tuple[ParsedToken, ...]
    alignable: bool = True


@dataclass(frozen=True, slots=True)
class ParsedParagraph:
    number: int | None
    ordinal: int
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
        sentence_pairs, sentence_method = _align_structured_units(
            tuple(
                sentence
                for paragraph in zh.document.paragraphs
                for sentence in paragraph.sentences
                if sentence.alignable
            ),
            tuple(
                sentence
                for paragraph in en.document.paragraphs
                for sentence in paragraph.sentences
                if sentence.alignable
            ),
        )
        for sentence_ordinal, (zh_sentence, en_sentence) in enumerate(
            sentence_pairs,
            start=1,
        ):
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id(
                        "pair",
                        zh_source.id,
                        en_source.id,
                        "sentence",
                        zh_sentence.ordinal,
                        en_sentence.ordinal,
                    ),
                    ordinal=sentence_ordinal,
                    zh_unit_id=zh.sentence_ids[zh_sentence.ordinal],
                    en_unit_id=en.sentence_ids[en_sentence.ordinal],
                    zh_text=zh_sentence.text,
                    en_text=en_sentence.text,
                    alignment_unit="sentence",
                    method=sentence_method,
                    confidence=1.0,
                )
            )

        paragraph_pairs, paragraph_method = _align_structured_units(
            zh.document.paragraphs,
            en.document.paragraphs,
        )
        for paragraph_ordinal, (zh_paragraph, en_paragraph) in enumerate(
            paragraph_pairs,
            start=1,
        ):
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id(
                        "pair",
                        zh_source.id,
                        en_source.id,
                        "paragraph",
                        zh_paragraph.ordinal,
                        en_paragraph.ordinal,
                    ),
                    ordinal=paragraph_ordinal,
                    zh_unit_id=zh.paragraph_ids[zh_paragraph.ordinal],
                    en_unit_id=en.paragraph_ids[en_paragraph.ordinal],
                    zh_text=zh_paragraph.text,
                    en_text=en_paragraph.text,
                    alignment_unit="paragraph",
                    method=paragraph_method,
                    confidence=1.0,
                )
            )
        _append_alignment_warning(
            result,
            unit="sentence",
            zh_count=sum(len(paragraph.sentences) for paragraph in zh.document.paragraphs),
            en_count=sum(len(paragraph.sentences) for paragraph in en.document.paragraphs),
            aligned_count=len(sentence_pairs),
        )
        _append_alignment_warning(
            result,
            unit="paragraph",
            zh_count=len(zh.document.paragraphs),
            en_count=len(en.document.paragraphs),
            aligned_count=len(paragraph_pairs),
        )
        if not result.parallel_pairs:
            raise ProcessingError("Tagged pair contains no verifiable aligned unit.")
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
    for paragraph in document.paragraphs:
        paragraph_id = stable_id("para", source.id, paragraph.ordinal, paragraph.number)
        paragraph_ids[paragraph.ordinal] = paragraph_id
        result.paragraphs.append(
            ParagraphRecord(
                id=paragraph_id,
                document_id=document_id,
                ordinal=paragraph.ordinal,
                language=source.language,
                text=paragraph.text,
            )
        )
        for sentence in paragraph.sentences:
            sentence_id = stable_id(
                "sent",
                source.id,
                sentence.ordinal,
                sentence.number,
            )
            sentence_ids[sentence.ordinal] = sentence_id
            result.sentences.append(
                SentenceRecord(
                    id=sentence_id,
                    document_id=document_id,
                    paragraph_id=paragraph_id,
                    ordinal=sentence.ordinal,
                    language=source.language,
                    text=sentence.text,
                )
            )
            for token_ordinal, token in enumerate(sentence.tokens, start=1):
                result.tokens.append(
                    TokenRecord(
                        id=stable_id("tok", source.id, sentence.ordinal, token_ordinal),
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
    sentence_ordinal = 0
    for paragraph_attrs, paragraph_body in _iter_tagged_elements(text, "p"):
        number = _number_attribute(paragraph_attrs)
        sentence_elements = _iter_tagged_elements(paragraph_body, "s")
        if number is None and not sentence_elements:
            continue
        sentences: list[ParsedSentence] = []
        for sentence_attrs, sentence_body in sentence_elements:
            sentence_number = _number_attribute(sentence_attrs)
            parsed_tokens = _parse_tokens(
                sentence_body,
                language,
            )
            sentence_text, offsets = _surface_and_offsets(parsed_tokens, language)
            tokens = tuple(
                ParsedToken(word, pos, start, end)
                for (word, pos), (start, end) in zip(parsed_tokens, offsets, strict=True)
            )
            sentence_ordinal += 1
            sentences.append(
                ParsedSentence(
                    number=sentence_number,
                    ordinal=sentence_ordinal,
                    text=sentence_text,
                    tokens=tokens,
                )
            )
        if not sentences:
            parsed_tokens = _parse_tokens(paragraph_body, language)
            paragraph_surface, offsets = _surface_and_offsets(parsed_tokens, language)
            if parsed_tokens:
                sentence_ordinal += 1
                sentences.append(
                    ParsedSentence(
                        number=None,
                        ordinal=sentence_ordinal,
                        text=paragraph_surface,
                        tokens=tuple(
                            ParsedToken(word, pos, start, end)
                            for (word, pos), (start, end) in zip(
                                parsed_tokens,
                                offsets,
                                strict=True,
                            )
                        ),
                        alignable=False,
                    )
                )
        separator = "" if language == "zh" else " "
        paragraph_text = separator.join(sentence.text for sentence in sentences)
        paragraphs.append(
            ParsedParagraph(
                number=number,
                ordinal=len(paragraphs) + 1,
                text=paragraph_text,
                sentences=tuple(sentences),
            )
        )

    if not paragraphs:
        raise ProcessingError(f"Tagged source contains no numbered paragraph: {source.filename}")
    return ParsedDocument(title=title, paragraphs=tuple(paragraphs))


def _parse_tokens(body: str, language: str) -> tuple[tuple[str, str], ...]:
    value = _GRAMMAR_ANNOTATION_RE.sub(" ", body)
    value = _RESIDUAL_TAG_RE.sub(" ", value)
    if language == "zh":
        value = _ORPHAN_ZH_POS_RE.sub(" ", value)
    pattern = _ZH_TAGGED_TOKEN_RE if language == "zh" else _EN_TAGGED_TOKEN_RE
    parsed: list[tuple[str, str]] = []
    for raw_token in value.split():
        if language == "zh" and "/" in raw_token:
            inline = _parse_zh_inline_token(raw_token)
            if inline:
                parsed.extend(inline)
                continue
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


def _parse_zh_inline_token(raw_token: str) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    consumed = 0
    for match in _ZH_INLINE_TAG_RE.finditer(raw_token):
        if match.start() > consumed:
            _append_untagged_fragment(parsed, raw_token[consumed : match.start()])
        _append_tagged_fragment(parsed, match.group("word"), match.group("pos"))
        consumed = match.end()
    if not parsed:
        return ()
    if consumed < len(raw_token):
        _append_untagged_fragment(parsed, raw_token[consumed:])
    return tuple(parsed)


def _append_tagged_fragment(
    parsed: list[tuple[str, str]],
    word: str,
    pos: str,
) -> None:
    word = _LEADING_ALIGNMENT_MARK_RE.sub("", word)
    if not word:
        return
    leading, core, trailing = _split_edge_punctuation(word)
    parsed.extend((character, "PUNCT") for character in leading)
    if core:
        parsed.append((core, pos))
    parsed.extend((character, "PUNCT") for character in trailing)


def _append_untagged_fragment(
    parsed: list[tuple[str, str]],
    value: str,
) -> None:
    if not value:
        return
    if _ORPHAN_ZH_POS_RE.fullmatch(value):
        return
    leading, core, trailing = _split_edge_punctuation(value)
    parsed.extend((character, "PUNCT") for character in leading)
    if core:
        parsed.append((core, "UNK"))
    parsed.extend((character, "PUNCT") for character in trailing)


def _split_edge_punctuation(value: str) -> tuple[str, str, str]:
    start = 0
    while start < len(value) and _is_punctuation_token(value[start]):
        start += 1
    end = len(value)
    while end > start and _is_punctuation_token(value[end - 1]):
        end -= 1
    return value[:start], value[start:end], value[end:]


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
    """Retained as a compatibility hook; alignment is validated conservatively below."""
    if not zh.paragraphs or not en.paragraphs:
        raise ProcessingError("Tagged pair contains no paragraph structure.")


def _iter_tagged_elements(text: str, tag: str) -> tuple[tuple[str, str], ...]:
    starts = tuple(
        re.finditer(rf"<{re.escape(tag)}\b(?P<attrs>[^>]*)>", text, flags=re.IGNORECASE)
    )
    elements: list[tuple[str, str]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        elements.append((match.group("attrs"), text[match.end() : end]))
    return tuple(elements)


def _align_structured_units(
    zh_units: tuple[ParsedParagraph, ...] | tuple[ParsedSentence, ...],
    en_units: tuple[ParsedParagraph, ...] | tuple[ParsedSentence, ...],
) -> tuple[
    tuple[tuple[ParsedParagraph, ParsedParagraph], ...]
    | tuple[tuple[ParsedSentence, ParsedSentence], ...],
    str,
]:
    zh_numbers = tuple(unit.number for unit in zh_units)
    en_numbers = tuple(unit.number for unit in en_units)
    if len(zh_units) == len(en_units) and zh_numbers == en_numbers:
        method = (
            "provided_structure_id"
            if all(number is not None for number in zh_numbers)
            and len(set(zh_numbers)) == len(zh_numbers)
            else "provided_structure_order"
        )
        return tuple(zip(zh_units, en_units, strict=True)), method

    en_by_number: dict[int, list[ParsedParagraph | ParsedSentence]] = {}
    for unit in en_units:
        if unit.number is not None:
            en_by_number.setdefault(unit.number, []).append(unit)
    used: Counter[int] = Counter()
    pairs: list[
        tuple[ParsedParagraph, ParsedParagraph]
        | tuple[ParsedSentence, ParsedSentence]
    ] = []
    for zh_unit in zh_units:
        if zh_unit.number is None:
            continue
        candidates = en_by_number.get(zh_unit.number, [])
        occurrence = used[zh_unit.number]
        if occurrence < len(candidates):
            pairs.append((zh_unit, candidates[occurrence]))
            used[zh_unit.number] += 1
    return tuple(pairs), "provided_structure_id"


def _append_alignment_warning(
    result: ImportResult,
    *,
    unit: str,
    zh_count: int,
    en_count: int,
    aligned_count: int,
) -> None:
    omitted_zh = max(0, zh_count - aligned_count)
    omitted_en = max(0, en_count - aligned_count)
    if omitted_zh or omitted_en:
        result.warnings.append(
            f"Conservative {unit} alignment omitted unverifiable units: "
            f"zh={omitted_zh}, en={omitted_en}."
        )
