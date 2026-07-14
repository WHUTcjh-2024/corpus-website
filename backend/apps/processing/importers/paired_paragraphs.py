from __future__ import annotations

from collections.abc import Iterator, Sequence

from ..contracts import ImportResult, ParallelPairRecord, SourceFile, stable_id
from ..exceptions import ProcessingError
from .base import BaseImporter
from .raw_mono import import_raw_source


class PairedParagraphImporter(BaseImporter):
    """Import a manually paragraph-aligned Chinese/English file pair.

    Paragraph order is part of the source contract. Sentences and tokens are
    still derived for monolingual KWIC, but they must never be used to rebuild
    the bilingual alignment because translation sentence boundaries may differ.
    """

    name = "paired_paragraphs_provided"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        zh_sources = [source for source in sources if source.language == "zh"]
        en_sources = [source for source in sources if source.language == "en"]
        if len(zh_sources) != 1 or len(en_sources) != 1:
            raise ProcessingError(
                "PairedParagraphImporter requires exactly one zh file and one en file."
            )

        zh_result = import_raw_source(zh_sources[0])
        en_result = import_raw_source(en_sources[0])
        zh_count = len(zh_result.paragraphs)
        en_count = len(en_result.paragraphs)
        if zh_count != en_count:
            raise ProcessingError(
                "Provided paragraph alignment is invalid: "
                f"zh has {zh_count} paragraphs while en has {en_count}."
            )
        if zh_count == 0:
            raise ProcessingError("Provided paragraph alignment contains no paragraph pair.")

        result = ImportResult(
            source_file_ids=[zh_sources[0].id, en_sources[0].id],
            documents=[*zh_result.documents, *en_result.documents],
            paragraphs=[*zh_result.paragraphs, *en_result.paragraphs],
            sentences=[*zh_result.sentences, *en_result.sentences],
            tokens=[*zh_result.tokens, *en_result.tokens],
        )
        for ordinal, (zh_paragraph, en_paragraph) in enumerate(
            zip(zh_result.paragraphs, en_result.paragraphs, strict=True),
            start=1,
        ):
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id(
                        "pair",
                        zh_sources[0].id,
                        en_sources[0].id,
                        "paragraph",
                        ordinal,
                    ),
                    ordinal=ordinal,
                    zh_unit_id=zh_paragraph.id,
                    en_unit_id=en_paragraph.id,
                    zh_text=zh_paragraph.text,
                    en_text=en_paragraph.text,
                    alignment_unit="paragraph",
                    method="provided_paragraph_order",
                    confidence=1.0,
                )
            )
        yield result
