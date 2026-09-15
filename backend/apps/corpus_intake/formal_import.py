from __future__ import annotations

from dataclasses import dataclass
from django.db import transaction

from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusDocumentation,
    CorpusFile,
    CorpusFileStatus,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)

from .scanner import ManifestRecord, ScanResult


@dataclass(frozen=True, slots=True)
class FormalCorpusGroup:
    corpus_type: str
    name_suffix: str
    language: str


@dataclass(frozen=True, slots=True)
class FormalRegistrationResult:
    corpora: tuple[Corpus, ...]
    registered_file_count: int
    quarantined_records: tuple[ManifestRecord, ...]


FORMAL_GROUPS = (
    FormalCorpusGroup(
        CorpusType.PAIRED_TAGGED_ZH_EN,
        "汉英人工对齐标注语料",
        CorpusLanguage.ZH_EN,
    ),
    FormalCorpusGroup(
        CorpusType.PAIRED_RAW_ZH_EN,
        "汉英原文候选配对语料",
        CorpusLanguage.ZH_EN,
    ),
    FormalCorpusGroup(CorpusType.RAW_ZH, "中文原文语料", CorpusLanguage.ZH),
    FormalCorpusGroup(CorpusType.RAW_EN, "英文译文语料", CorpusLanguage.EN),
)


def register_formal_teacher_corpora(
    *,
    scan_result: ScanResult,
    access_level: str = CorpusAccessLevel.ADVANCED,
    name_prefix: str = "马克思主义中国化经典文献",
) -> FormalRegistrationResult:
    """Register the teacher-delivered corpus as four processable collections."""

    if access_level not in {
        CorpusAccessLevel.JUNIOR,
        CorpusAccessLevel.MIDDLE,
        CorpusAccessLevel.ADVANCED,
    }:
        raise ValueError("正式教师语料必须使用初级、中级或高级访问等级。")

    quarantined = tuple(
        record for record in scan_result.records if record.status == "quarantined"
    )
    eligible = [
        record for record in scan_result.records if record.status != "quarantined"
    ]
    supported_types = {group.corpus_type for group in FORMAL_GROUPS}
    unsupported = [
        record for record in eligible if record.detected_type not in supported_types
    ]
    if unsupported:
        names = ", ".join(record.filename for record in unsupported[:5])
        raise ValueError(f"存在无法归入正式语料集合的文件：{names}")

    by_type: dict[str, list[ManifestRecord]] = {
        group.corpus_type: [] for group in FORMAL_GROUPS
    }
    for record in eligible:
        by_type[record.detected_type].append(record)
    for corpus_type in {
        CorpusType.PAIRED_RAW_ZH_EN,
        CorpusType.PAIRED_TAGGED_ZH_EN,
    }:
        _validate_pair_groups(by_type[corpus_type], corpus_type=corpus_type)

    corpora: list[Corpus] = []
    for group in FORMAL_GROUPS:
        records = by_type[group.corpus_type]
        if not records:
            continue
        corpora.append(
            _register_group(
                scan_result=scan_result,
                group=group,
                records=records,
                access_level=access_level,
                name_prefix=name_prefix,
                quarantined_count=len(quarantined),
            )
        )
    return FormalRegistrationResult(
        corpora=tuple(corpora),
        registered_file_count=sum(len(records) for records in by_type.values()),
        quarantined_records=quarantined,
    )


@transaction.atomic
def _register_group(
    *,
    scan_result: ScanResult,
    group: FormalCorpusGroup,
    records: list[ManifestRecord],
    access_level: str,
    name_prefix: str,
    quarantined_count: int,
) -> Corpus:
    corpus, _ = Corpus.objects.update_or_create(
        manifest_file_id=f"teacher-formal-v1-{group.corpus_type}",
        defaults={
            "name": f"{name_prefix}·{group.name_suffix}",
            "source_type": CorpusSourceType.TEACHER,
            "corpus_type": group.corpus_type,
            "language": group.language,
            "owner": None,
            "access_level": access_level,
            "status": CorpusStatus.CREATED,
            "stage": "formal_manifest_registered",
            "description": (
                f"教师提供的正式语料；本集合登记 {len(records)} 个文件。"
                f"全库隔离文件 {quarantined_count} 个，未进入检索索引。"
            ),
            "manifest_relative_path": f"formal/{group.corpus_type}",
            "manifest_size_bytes": sum(record.size_bytes for record in records),
            "manifest_encoding": ";".join(
                sorted({record.encoding for record in records if record.encoding})
            ),
        },
    )

    root = scan_result.inbox_root.resolve()
    kept_ids: list[int] = []
    for record in records:
        source_path = (root / record.original_path).resolve()
        if not source_path.is_relative_to(root):
            raise ValueError(f"正式语料文件超出源目录：{record.original_path}")
        corpus_file, _ = CorpusFile.objects.update_or_create(
            corpus=corpus,
            stored_path=str(source_path),
            defaults={
                "original_filename": record.filename,
                "manifest_file_id": record.file_id,
                "pair_id": record.probable_pair_id,
                "detected_type": group.corpus_type,
                "language": record.detected_language,
                "size_bytes": record.size_bytes,
                "encoding": record.encoding,
                "status": CorpusFileStatus.PENDING,
                "error_message": "",
            },
        )
        kept_ids.append(corpus_file.pk)
    corpus.files.exclude(pk__in=kept_ids).delete()
    CorpusDocumentation.objects.update_or_create(
        corpus=corpus,
        defaults={
            "file_count": len(records),
            "processing_notes": "正式语料已完成清单登记，等待或正在执行索引加工。",
        },
    )
    return corpus


def _validate_pair_groups(records: list[ManifestRecord], *, corpus_type: str) -> None:
    by_pair: dict[str, list[ManifestRecord]] = {}
    for record in records:
        if not record.probable_pair_id:
            raise ValueError(f"{record.filename} 缺少双语配对 ID。")
        by_pair.setdefault(record.probable_pair_id, []).append(record)
    for pair_id, pair_records in by_pair.items():
        languages = {record.detected_language for record in pair_records}
        if len(pair_records) != 2 or languages != {CorpusLanguage.ZH, CorpusLanguage.EN}:
            raise ValueError(
                f"{corpus_type} 的配对 {pair_id} 必须恰好包含一个中文文件和一个英文文件。"
            )
