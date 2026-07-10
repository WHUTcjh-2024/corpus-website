from __future__ import annotations

import re
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
from ..text import normalize_token, read_source_text, split_paragraphs
from .base import BaseImporter


_ZH_TAGGED = re.compile(r"^(?P<word>.+)/(?P<pos>[A-Za-z][A-Za-z0-9_-]*)$")
_EN_TAGGED = re.compile(r"^(?P<word>.+)_(?P<pos>[A-Z0-9$-]+)$")


class TaggedCorpusImporter(BaseImporter):
    name = "tagged_corpus"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        for source in sources:
            if source.language not in {"zh", "en"}:
                raise ProcessingError(f"Tagged source requires zh/en language: {source.filename}")
            yield self._import_source(source)

    def _import_source(self, source: SourceFile) -> ImportResult:
        text, _ = read_source_text(source)
        document_id = stable_id("doc", source.id)
        result = ImportResult(
            source_file_ids=[source.id],
            documents=[
                DocumentRecord(
                    document_id,
                    source.id,
                    source.filename,
                    source.language,
                    source.filename,
                    len(text),
                )
            ],
        )
        sentence_ordinal = 0
        parsed_count = 0
        pattern = _ZH_TAGGED if source.language == "zh" else _EN_TAGGED
        for paragraph_ordinal, block in enumerate(split_paragraphs(text), start=1):
            for line in (line.strip() for line in block.splitlines() if line.strip()):
                parsed_tokens = []
                skipped = []
                for raw_token in line.split():
                    match = pattern.match(raw_token)
                    if match:
                        parsed_tokens.append((match.group("word"), match.group("pos")))
                    else:
                        skipped.append(raw_token)
                if not parsed_tokens:
                    continue
                sentence_ordinal += 1
                paragraph_id = stable_id("para", source.id, paragraph_ordinal, sentence_ordinal)
                sentence_id = stable_id("sent", source.id, sentence_ordinal)
                separator = "" if source.language == "zh" else " "
                sentence_text = separator.join(word for word, _ in parsed_tokens)
                result.paragraphs.append(
                    ParagraphRecord(paragraph_id, document_id, paragraph_ordinal, source.language, sentence_text)
                )
                result.sentences.append(
                    SentenceRecord(
                        sentence_id,
                        document_id,
                        paragraph_id,
                        sentence_ordinal,
                        source.language,
                        sentence_text,
                    )
                )
                cursor = 0
                for token_ordinal, (word, pos) in enumerate(parsed_tokens, start=1):
                    start = cursor
                    end = start + len(word)
                    cursor = end + len(separator)
                    result.tokens.append(
                        TokenRecord(
                            id=stable_id("tok", source.id, sentence_ordinal, token_ordinal),
                            document_id=document_id,
                            sentence_id=sentence_id,
                            ordinal=token_ordinal,
                            language=source.language,
                            text=word,
                            normalized=normalize_token(word, source.language),
                            pos=pos,
                            start=start,
                            end=end,
                        )
                    )
                    parsed_count += 1
                if skipped:
                    result.warnings.append(
                        f"{source.filename}: skipped {len(skipped)} untagged token(s)"
                    )
        if parsed_count == 0:
            raise ProcessingError(f"Tagged source contains no valid token/POS pairs: {source.filename}")
        return result
