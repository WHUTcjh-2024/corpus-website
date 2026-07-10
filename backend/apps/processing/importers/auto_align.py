from __future__ import annotations

from collections.abc import Iterator, Sequence

from ..contracts import ImportResult, ParallelPairRecord, SourceFile, stable_id
from ..exceptions import ProcessingError
from .base import BaseImporter
from .raw_mono import import_raw_source


class AutoAlignImporter(BaseImporter):
    name = "auto_align_ordinal_baseline"

    def iter_import(self, sources: Sequence[SourceFile]) -> Iterator[ImportResult]:
        zh_sources = [source for source in sources if source.language == "zh"]
        en_sources = [source for source in sources if source.language == "en"]
        if len(zh_sources) != 1 or len(en_sources) != 1:
            raise ProcessingError(
                "AutoAlignImporter basic version requires exactly one zh file and one en file."
            )

        zh_result = import_raw_source(zh_sources[0])
        en_result = import_raw_source(en_sources[0])
        result = ImportResult(
            source_file_ids=[zh_sources[0].id, en_sources[0].id],
            documents=[*zh_result.documents, *en_result.documents],
            paragraphs=[*zh_result.paragraphs, *en_result.paragraphs],
            sentences=[*zh_result.sentences, *en_result.sentences],
            tokens=[*zh_result.tokens, *en_result.tokens],
        )
        pair_count = min(len(zh_result.sentences), len(en_result.sentences))
        for index in range(pair_count):
            zh_sentence = zh_result.sentences[index]
            en_sentence = en_result.sentences[index]
            result.parallel_pairs.append(
                ParallelPairRecord(
                    id=stable_id("pair", zh_sources[0].id, en_sources[0].id, index + 1),
                    ordinal=index + 1,
                    zh_sentence_id=zh_sentence.id,
                    en_sentence_id=en_sentence.id,
                    zh_text=zh_sentence.text,
                    en_text=en_sentence.text,
                    method="ordinal_baseline",
                    confidence=0.5,
                )
            )
        if pair_count == 0:
            raise ProcessingError("AutoAlignImporter could not produce any sentence pair.")
        if len(zh_result.sentences) != len(en_result.sentences):
            result.warnings.append(
                "Sentence counts differ; the basic ordinal aligner kept only paired positions."
            )
        yield result
