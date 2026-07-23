from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.corpora.models import Corpus, CorpusStatus

from .contracts import SCHEMA_VERSION
from .exceptions import ProcessingAlreadyQueued, ProcessingError
from .models import ProcessingTask, ProcessingTaskStatus
from .services import create_processing_task, dispatch_processing_task


REQUIRED_PROCESSED_FILES = (
    "meta.json",
    "documents.jsonl",
    "paragraphs.jsonl",
    "sentences.jsonl",
    "tokens.jsonl",
    "parallel_pairs.jsonl",
)

REQUIRED_TABLE_COLUMNS = {
    "documents": {"document_id", "filename", "language"},
    "document_streams": {"document_id", "language", "text"},
    "tokens": {
        "global_position",
        "stream_position",
        "token_id",
        "normalized",
        "surface",
        "lemma",
        "pos",
        "language",
        "document_id",
        "sentence_id",
        "sentence_position",
        "document_start",
        "document_end",
        "is_punctuation",
    },
    "ngrams": {
        "language",
        "n",
        "normalized",
        "display",
        "frequency",
        "document_range",
        "contains_punctuation",
    },
    "parallel_pairs": {
        "global_position",
        "pair_id",
        "pair_ordinal",
        "zh_unit_id",
        "en_unit_id",
        "zh_text",
        "en_text",
        "zh_normalized",
        "en_normalized",
        "zh_document_id",
        "en_document_id",
        "zh_filename",
        "en_filename",
        "zh_token_spans",
        "en_token_spans",
        "alignment_unit",
        "method",
        "confidence",
    },
    "word_totals": {
        "language",
        "normalized",
        "display",
        "frequency",
        "document_range",
        "is_punctuation",
    },
    "word_frequencies": {
        "language",
        "normalized",
        "display",
        "pos",
        "frequency",
        "document_range",
        "is_punctuation",
    },
}


class IndexHealthState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    OUTDATED = "outdated"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class IndexHealth:
    state: IndexHealthState
    detail: str = ""
    schema_version: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == IndexHealthState.READY

    @property
    def reader_label(self) -> str:
        return {
            IndexHealthState.READY: "索引可用",
            IndexHealthState.MISSING: "检索索引尚未生成",
            IndexHealthState.OUTDATED: "检测到旧版检索索引",
            IndexHealthState.CORRUPT: "检索索引完整性异常",
        }[self.state]


@dataclass(frozen=True, slots=True)
class IndexRepairNotice:
    state: str
    message: str
    task_id: str = ""
    progress: int = 0
    retry_after_seconds: int = 3

    @property
    def is_active(self) -> bool:
        return self.state in {
            ProcessingTaskStatus.PENDING,
            ProcessingTaskStatus.RUNNING,
        }


def inspect_corpus_index(
    corpus_id: str,
    *,
    data_root: Path | None = None,
) -> IndexHealth:
    root = Path(data_root or settings.DATA_ROOT).resolve()
    processed_root = root / "processed" / str(corpus_id)
    index_path = root / "indexes" / str(corpus_id) / "kwic_index.sqlite"
    tracked_paths = (
        index_path,
        *(processed_root / filename for filename in REQUIRED_PROCESSED_FILES),
    )
    fingerprint = tuple(_path_fingerprint(path) for path in tracked_paths)
    return _inspect_corpus_index_snapshot(str(corpus_id), str(root), fingerprint)


def _path_fingerprint(path: Path) -> tuple[str, bool, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), False, 0, 0)
    return (str(path), path.is_file(), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=2048)
def _inspect_corpus_index_snapshot(
    corpus_id: str,
    data_root: str,
    _fingerprint: tuple[tuple[str, bool, int, int], ...],
) -> IndexHealth:
    root = Path(data_root)
    processed_root = root / "processed" / corpus_id
    index_path = root / "indexes" / corpus_id / "kwic_index.sqlite"

    missing_processed = [
        filename
        for filename in REQUIRED_PROCESSED_FILES
        if not (processed_root / filename).is_file()
    ]
    if not index_path.is_file() or missing_processed:
        missing = ["kwic_index.sqlite"] if not index_path.is_file() else []
        missing.extend(missing_processed)
        return IndexHealth(
            IndexHealthState.MISSING,
            detail=f"missing={','.join(missing)}",
        )

    try:
        metadata = json.loads((processed_root / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return IndexHealth(IndexHealthState.CORRUPT, detail=f"meta={type(exc).__name__}")

    schema_version = str(metadata.get("schema_version", ""))
    if schema_version != SCHEMA_VERSION:
        return IndexHealth(
            IndexHealthState.OUTDATED,
            detail=f"schema={schema_version or 'unknown'}, expected={SCHEMA_VERSION}",
            schema_version=schema_version,
        )

    try:
        with closing(
            sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)
        ) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                return IndexHealth(
                    IndexHealthState.CORRUPT,
                    detail=f"quick_check={quick_check[0] if quick_check else 'empty'}",
                    schema_version=schema_version,
                )

            existing_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing_tables = set(REQUIRED_TABLE_COLUMNS) - existing_tables
            if missing_tables:
                return IndexHealth(
                    IndexHealthState.CORRUPT,
                    detail=f"missing_tables={','.join(sorted(missing_tables))}",
                    schema_version=schema_version,
                )

            for table, required_columns in REQUIRED_TABLE_COLUMNS.items():
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                missing_columns = required_columns - columns
                if missing_columns:
                    return IndexHealth(
                        IndexHealthState.CORRUPT,
                        detail=(
                            f"missing_columns={table}:"
                            f"{','.join(sorted(missing_columns))}"
                        ),
                        schema_version=schema_version,
                    )
    except (OSError, sqlite3.Error) as exc:
        return IndexHealth(
            IndexHealthState.CORRUPT,
            detail=f"sqlite={type(exc).__name__}",
            schema_version=schema_version,
        )

    return IndexHealth(
        IndexHealthState.READY,
        schema_version=schema_version,
    )


def ensure_corpus_index_ready(
    corpus: Corpus,
    *,
    force: bool = False,
) -> IndexRepairNotice | None:
    active_task = _active_task(corpus)
    if active_task is not None:
        return _active_notice(active_task)

    if corpus.status != CorpusStatus.READY:
        return None

    health = inspect_corpus_index(str(corpus.pk))
    if health.is_ready and not force:
        return None
    if force and health.is_ready:
        health = IndexHealth(
            IndexHealthState.CORRUPT,
            detail="runtime_validation_failed",
            schema_version=health.schema_version,
        )

    try:
        task = create_processing_task(corpus=corpus)
        dispatch_processing_task(task)
    except ProcessingAlreadyQueued:
        task = _active_task(corpus)
        return _active_notice(task) if task is not None else _failure_notice()
    except ProcessingError:
        return _failure_notice()

    task.refresh_from_db()
    corpus.refresh_from_db(fields=["status", "stage"])
    if task.status == ProcessingTaskStatus.SUCCESS:
        repaired_health = inspect_corpus_index(str(corpus.pk))
        if repaired_health.is_ready:
            return None
    if task.status == ProcessingTaskStatus.FAILED:
        return _failure_notice(task)
    return IndexRepairNotice(
        state=task.status,
        message=(
            f"{health.reader_label}，系统已自动创建修复任务。"
            "修复采用原子替换，不会修改或覆盖原始语料文件。"
        ),
        task_id=str(task.pk),
        progress=task.progress,
    )


def _active_task(corpus: Corpus) -> ProcessingTask | None:
    return (
        ProcessingTask.objects.filter(
            corpus=corpus,
            status__in=[
                ProcessingTaskStatus.PENDING,
                ProcessingTaskStatus.RUNNING,
            ],
        )
        .order_by("-created_at")
        .first()
    )


def _active_notice(task: ProcessingTask) -> IndexRepairNotice:
    return IndexRepairNotice(
        state=task.status,
        message=(
            "检索索引正在后台自动构建。完成后当前页面会自动恢复，"
            "无需重新上传或手动加工语料。"
        ),
        task_id=str(task.pk),
        progress=task.progress,
    )


def _failure_notice(task: ProcessingTask | None = None) -> IndexRepairNotice:
    return IndexRepairNotice(
        state=ProcessingTaskStatus.FAILED,
        message=(
            "索引自动修复未能完成，故障信息已保留供管理员处理。"
            "原始语料文件未受影响。"
        ),
        task_id=str(task.pk) if task else "",
        progress=task.progress if task else 0,
        retry_after_seconds=0,
    )
