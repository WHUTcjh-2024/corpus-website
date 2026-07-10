from __future__ import annotations

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
from ..text import normalize_token, read_source_text, split_paragraphs, split_sentences, token_matches
from .base import BaseImporter


class RawMonoTxtImporter(BaseImporter):
    name = "raw_mono_txt"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        for source in sources:
            if source.language not in {"zh", "en"}:
                raise ProcessingError(f"Raw mono source requires zh/en language: {source.filename}")
            yield import_raw_source(source)


def import_raw_source(source: SourceFile) -> ImportResult:
    text, _ = read_source_text(source)
    if not text.strip():
        raise ProcessingError(f"Source file is empty: {source.filename}")

    document_id = stable_id("doc", source.id)
    title = next((line.strip() for line in text.splitlines() if line.strip()), source.filename)
    result = ImportResult(
        source_file_ids=[source.id],
        documents=[
            DocumentRecord(
                id=document_id,
                source_file_id=source.id,
                filename=source.filename,
                language=source.language,
                title=title[:200],
                text_length=len(text),
            )
        ],
    )

    sentence_ordinal = 0
    for paragraph_ordinal, paragraph_text in enumerate(split_paragraphs(text), start=1):
        paragraph_id = stable_id("para", source.id, paragraph_ordinal)
        result.paragraphs.append(
            ParagraphRecord(
                id=paragraph_id,
                document_id=document_id,
                ordinal=paragraph_ordinal,
                language=source.language,
                text=paragraph_text,
            )
        )
        for sentence_text in split_sentences(paragraph_text, source.language):
            sentence_ordinal += 1
            sentence_id = stable_id("sent", source.id, sentence_ordinal)
            result.sentences.append(
                SentenceRecord(
                    id=sentence_id,
                    document_id=document_id,
                    paragraph_id=paragraph_id,
                    ordinal=sentence_ordinal,
                    language=source.language,
                    text=sentence_text,
                )
            )
            for token_ordinal, match in enumerate(token_matches(sentence_text, source.language), start=1):
                token_text = match.group(0)
                result.tokens.append(
                    TokenRecord(
                        id=stable_id("tok", source.id, sentence_ordinal, token_ordinal),
                        document_id=document_id,
                        sentence_id=sentence_id,
                        ordinal=token_ordinal,
                        language=source.language,
                        text=token_text,
                        normalized=normalize_token(token_text, source.language),
                        start=match.start(),
                        end=match.end(),
                    )
                )
    return result
