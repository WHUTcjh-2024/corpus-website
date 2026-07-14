from __future__ import annotations

from collections.abc import Iterator, Sequence

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
from ..text import normalize_token, read_source_text, token_matches
from .base import BaseImporter


class AlignedTsvImporter(BaseImporter):
    name = "aligned_tsv"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        for source in sources:
            yield self._import_source(source)

    def _import_source(self, source: SourceFile) -> ImportResult:
        text, _ = read_source_text(source)
        lines = [line for line in text.splitlines() if line.strip()]
        if lines and _is_header(lines[0]):
            lines = lines[1:]

        document_id = stable_id("doc", source.id)
        result = ImportResult(
            source_file_ids=[source.id],
            documents=[
                DocumentRecord(
                    id=document_id,
                    source_file_id=source.id,
                    filename=source.filename,
                    language="zh_en",
                    title=source.filename,
                    text_length=len(text),
                )
            ],
        )
        for pair_ordinal, line in enumerate(lines, start=1):
            columns = line.split("\t")
            if len(columns) < 2 or not columns[0].strip() or not columns[1].strip():
                raise ProcessingError(
                    f"Invalid aligned TSV row {pair_ordinal}: {source.filename}"
                )
            zh_text, en_text = columns[0].strip(), columns[1].strip()
            zh_sentence = self._append_side(result, source, document_id, pair_ordinal, "zh", zh_text)
            en_sentence = self._append_side(result, source, document_id, pair_ordinal, "en", en_text)
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id("pair", source.id, pair_ordinal),
                    ordinal=pair_ordinal,
                    zh_unit_id=zh_sentence.id,
                    en_unit_id=en_sentence.id,
                    zh_text=zh_text,
                    en_text=en_text,
                )
            )
        if not result.parallel_pairs:
            raise ProcessingError(f"Aligned TSV contains no sentence pairs: {source.filename}")
        return result

    @staticmethod
    def _append_side(
        result: ImportResult,
        source: SourceFile,
        document_id: str,
        ordinal: int,
        language: str,
        text: str,
    ) -> SentenceRecord:
        paragraph_id = stable_id("para", source.id, ordinal, language)
        sentence_id = stable_id("sent", source.id, ordinal, language)
        result.paragraphs.append(
            ParagraphRecord(paragraph_id, document_id, ordinal, language, text)
        )
        sentence = SentenceRecord(sentence_id, document_id, paragraph_id, ordinal, language, text)
        result.sentences.append(sentence)
        for token_ordinal, match in enumerate(token_matches(text, language), start=1):
            value = match.group(0)
            result.tokens.append(
                TokenRecord(
                    id=stable_id("tok", source.id, ordinal, language, token_ordinal),
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
        return sentence


def _is_header(line: str) -> bool:
    compact = line.lower().replace(" ", "")
    return compact in {"zh\ten", "cn\ten", "chinese\tenglish"}
