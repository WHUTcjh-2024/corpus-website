from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence

from ..contracts import (
    DocumentRecord,
    ImportResult,
    ParagraphRecord,
    SentenceRecord,
    SourceFile,
    TokenRecord,
    stable_id,
)
from ..exceptions import ProcessingError
from ..text import normalize_token, read_source_text, split_sentences, token_matches
from .base import BaseImporter


class XmlLikeImporter(BaseImporter):
    name = "xml_like"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        for source in sources:
            yield self._import_source(source)

    def _import_source(self, source: SourceFile) -> ImportResult:
        text, _ = read_source_text(source)
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1, flags=re.IGNORECASE)
        try:
            root = ET.fromstring(f"<corpus_root>{text}</corpus_root>")
        except ET.ParseError as exc:
            raise ProcessingError(f"Invalid XML-like source {source.filename}: {exc}") from exc

        language = source.language if source.language in {"zh", "en"} else "zh"
        title = next(
            (self._element_text(element) for element in root.iter("head") if self._element_text(element)),
            source.filename,
        )
        document_id = stable_id("doc", source.id)
        result = ImportResult(
            source_file_ids=[source.id],
            documents=[
                DocumentRecord(
                    document_id,
                    source.id,
                    source.filename,
                    language,
                    title[:200],
                    len(text),
                )
            ],
        )

        paragraph_elements = list(root.iter("p"))
        if not paragraph_elements:
            paragraph_elements = [root]
        sentence_ordinal = 0
        for paragraph_ordinal, paragraph_element in enumerate(paragraph_elements, start=1):
            paragraph_text = self._element_text(paragraph_element)
            if not paragraph_text:
                continue
            paragraph_id = stable_id("para", source.id, paragraph_ordinal)
            result.paragraphs.append(
                ParagraphRecord(
                    paragraph_id,
                    document_id,
                    paragraph_ordinal,
                    language,
                    paragraph_text,
                )
            )
            sentence_elements = list(paragraph_element.iter("s"))
            sentence_texts = [self._element_text(element) for element in sentence_elements]
            sentence_texts = [value for value in sentence_texts if value]
            if not sentence_texts:
                sentence_texts = split_sentences(paragraph_text, language)
            for sentence_text in sentence_texts:
                sentence_ordinal += 1
                sentence_id = stable_id("sent", source.id, sentence_ordinal)
                result.sentences.append(
                    SentenceRecord(
                        sentence_id,
                        document_id,
                        paragraph_id,
                        sentence_ordinal,
                        language,
                        sentence_text,
                    )
                )
                for token_ordinal, match in enumerate(token_matches(sentence_text, language), start=1):
                    value = match.group(0)
                    result.tokens.append(
                        TokenRecord(
                            id=stable_id("tok", source.id, sentence_ordinal, token_ordinal),
                            document_id=document_id,
                            sentence_id=sentence_id,
                            ordinal=token_ordinal,
                            language=language,
                            text=value,
                            normalized=normalize_token(value, language),
                            start=match.start(),
                            end=match.end(),
                        )
                    )
        if not result.sentences:
            raise ProcessingError(f"XML-like source contains no text: {source.filename}")
        return result

    @staticmethod
    def _element_text(element: ET.Element) -> str:
        return " ".join("".join(element.itertext()).split())
