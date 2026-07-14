from __future__ import annotations

from apps.corpora.models import CorpusType

from ..exceptions import ProcessingError
from .aligned_tsv import AlignedTsvImporter
from .base import BaseImporter
from .paired_paragraphs import PairedParagraphImporter
from .paired_tagged_structure import PairedTaggedStructureImporter
from .raw_mono import RawMonoTxtImporter
from .tagged import TaggedCorpusImporter
from .xml_like import XmlLikeImporter


_IMPORTERS: dict[str, type[BaseImporter]] = {
    CorpusType.RAW_ZH: RawMonoTxtImporter,
    CorpusType.RAW_EN: RawMonoTxtImporter,
    CorpusType.ALIGNED_TSV: AlignedTsvImporter,
    CorpusType.PAIRED_RAW_ZH_EN: PairedParagraphImporter,
    CorpusType.PAIRED_TAGGED_ZH_EN: PairedTaggedStructureImporter,
    CorpusType.TAGGED_ZH: TaggedCorpusImporter,
    CorpusType.TAGGED_EN: TaggedCorpusImporter,
    CorpusType.XML_LIKE: XmlLikeImporter,
}


def get_importer(corpus_type: str) -> BaseImporter:
    importer_class = _IMPORTERS.get(corpus_type)
    if importer_class is None:
        raise ProcessingError(f"No importer is registered for corpus type: {corpus_type}")
    return importer_class()
