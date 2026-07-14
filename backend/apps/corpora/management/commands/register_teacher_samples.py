from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.corpus_intake.classifiers import classify_path
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
from apps.processing.services import create_processing_task, process_task


@dataclass(frozen=True, slots=True)
class SampleFile:
    filename: str
    language: str


@dataclass(frozen=True, slots=True)
class SampleCorpus:
    name: str
    corpus_type: str
    language: str
    files: tuple[SampleFile, ...]


SAMPLES = (
    SampleCorpus(
        name="老师语料·单语中文·中国社会各阶级的分析",
        corpus_type=CorpusType.RAW_ZH,
        language=CorpusLanguage.ZH,
        files=(
            SampleFile(
                "1-毛泽东1-中1-1925-12-1-中国社会各阶级的分析.txt",
                CorpusLanguage.ZH,
            ),
        ),
    ),
    SampleCorpus(
        name="老师语料·单语英文·人民对美好生活的向往就是我们的奋斗目标",
        corpus_type=CorpusType.RAW_EN,
        language=CorpusLanguage.EN,
        files=(
            SampleFile(
                "1-习近平1-官译1-2012-11-15-人民对美好生活的向往就是我们的奋斗目标.txt",
                CorpusLanguage.EN,
            ),
        ),
    ),
    SampleCorpus(
        name="老师语料·双语段对齐·人民对美好生活的向往就是我们的奋斗目标",
        corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
        language=CorpusLanguage.ZH_EN,
        files=(
            SampleFile(
                "1-习近平1-中1-2012-11-15-人民对美好生活的向往就是我们的奋斗目标.txt",
                CorpusLanguage.ZH,
            ),
            SampleFile(
                "1-习近平1-官译1-2012-11-15-人民对美好生活的向往就是我们的奋斗目标.txt",
                CorpusLanguage.EN,
            ),
        ),
    ),
    SampleCorpus(
        name="老师语料·双语标注·湖南农民运动考察报告",
        corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
        language=CorpusLanguage.ZH_EN,
        files=(
            SampleFile(
                "2-M1-2-2-毛泽东1-中2-1927-3-0-湖南农民运动考察报告.txt",
                CorpusLanguage.ZH,
            ),
            SampleFile(
                "2-M1-2-毛泽东1-官译2-1927-3-0-湖南农民运动考察报告-pos.txt",
                CorpusLanguage.EN,
            ),
        ),
    ),
)


class Command(BaseCommand):
    help = "从老师 test_conc 目录登记四个精选只读语料，并可同步重建索引。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source-root", required=True, type=Path)
        parser.add_argument(
            "--source-type",
            choices=(CorpusSourceType.DEMO, CorpusSourceType.TEACHER),
            default=CorpusSourceType.DEMO,
            help="登记为 demo 或受分级保护的 teacher；默认 demo。",
        )
        parser.add_argument(
            "--access-level",
            choices=(
                CorpusAccessLevel.JUNIOR,
                CorpusAccessLevel.MIDDLE,
                CorpusAccessLevel.ADVANCED,
            ),
            help="teacher 的访问等级；未指定时为 advanced。",
        )
        parser.add_argument(
            "--process",
            action="store_true",
            help="登记后同步加工；用于本地验收和可重复构建 demo。",
        )

    def handle(self, *args, **options) -> None:
        source_root = options["source_root"].resolve()
        if not source_root.is_dir():
            raise CommandError(f"Teacher corpus root does not exist: {source_root}")
        source_type = options["source_type"]
        if source_type == CorpusSourceType.DEMO and options["access_level"]:
            raise CommandError("--access-level 只能与 --source-type teacher 一起使用。")
        access_level = (
            options["access_level"] or CorpusAccessLevel.ADVANCED
            if source_type == CorpusSourceType.TEACHER
            else CorpusAccessLevel.DEMO
        )

        registered: list[Corpus] = []
        for sample in SAMPLES:
            paths = [
                _find_unique_file(source_root, sample_file.filename)
                for sample_file in sample.files
            ]
            corpus = _register_sample(
                sample,
                paths,
                source_type=source_type,
                access_level=access_level,
            )
            registered.append(corpus)
            self.stdout.write(f"Registered: {corpus.name} ({corpus.pk})")

        if options["process"]:
            for corpus in registered:
                task = create_processing_task(corpus=corpus)
                report = process_task(task.pk)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processed: {corpus.name}; counts={report['counts']}"
                    )
                )
        self.stdout.write(self.style.SUCCESS(f"Teacher samples ready: {len(registered)}"))


def _find_unique_file(source_root: Path, filename: str) -> Path:
    matches = [path.resolve() for path in source_root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise CommandError(
            f"Expected exactly one teacher file named {filename}, found {len(matches)}."
        )
    path = matches[0]
    if not path.is_relative_to(source_root):
        raise CommandError(f"Teacher file escapes source root: {path}")
    return path


@transaction.atomic
def _register_sample(
    sample: SampleCorpus,
    paths: list[Path],
    *,
    source_type: str,
    access_level: str,
) -> Corpus:
    corpus, _ = Corpus.objects.update_or_create(
        name=sample.name,
        source_type=source_type,
        defaults={
            "corpus_type": sample.corpus_type,
            "language": sample.language,
            "owner": None,
            "access_level": access_level,
            "status": CorpusStatus.CREATED,
            "stage": "teacher_sample_registered",
            "description": "精选自老师提供的 test_conc；源文件只读。",
        },
    )
    kept_file_ids: list[int] = []
    for sample_file, path in zip(sample.files, paths, strict=True):
        classification = classify_path(path)
        corpus_file, _ = CorpusFile.objects.update_or_create(
            corpus=corpus,
            language=sample_file.language,
            defaults={
                "original_filename": path.name,
                "stored_path": str(path),
                "detected_type": sample.corpus_type,
                "size_bytes": path.stat().st_size,
                "encoding": classification.encoding,
                "status": CorpusFileStatus.PENDING,
                "error_message": "",
            },
        )
        kept_file_ids.append(corpus_file.pk)
    corpus.files.exclude(pk__in=kept_file_ids).delete()
    CorpusDocumentation.objects.update_or_create(
        corpus=corpus,
        defaults={"file_count": len(kept_file_ids)},
    )
    return corpus
