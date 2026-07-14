from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Q, QuerySet, Sum

from apps.accounts.models import UserRole
from apps.accounts.permissions import AccessScope, get_user_profile, workspace_access_scope
from apps.corpus_intake.classifiers import decode_text

from .scanners import scan_uploaded_file

from .models import (
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

if TYPE_CHECKING:
    from apps.processing.models import ProcessingTask


ROLE_ACCESS_LEVELS = {
    UserRole.JUNIOR: [CorpusAccessLevel.DEMO, CorpusAccessLevel.JUNIOR],
    UserRole.MIDDLE: [
        CorpusAccessLevel.DEMO,
        CorpusAccessLevel.JUNIOR,
        CorpusAccessLevel.MIDDLE,
    ],
    UserRole.ADVANCED: [
        CorpusAccessLevel.DEMO,
        CorpusAccessLevel.JUNIOR,
        CorpusAccessLevel.MIDDLE,
        CorpusAccessLevel.ADVANCED,
    ],
}


@dataclass(frozen=True, slots=True)
class PersonalCorpusData:
    name: str
    corpus_type: str
    language: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class CorpusFileData:
    path: Path
    detected_type: str
    language: str
    encoding: str = ""
    manifest_file_id: str = ""


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_file_bytes: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class UploadedCorpusData:
    name: str
    language: str
    description: str = ""


def visible_corpora_for(user: Any) -> QuerySet[Corpus]:
    queryset = Corpus.objects.select_related("owner").all()
    scope = workspace_access_scope(user)
    if scope == AccessScope.NONE:
        return queryset.none()
    if scope == AccessScope.ADMIN:
        return queryset

    queryset = queryset.exclude(status=CorpusStatus.DISABLED)
    if scope == AccessScope.DEMO_ONLY:
        return queryset.filter(
            Q(source_type=CorpusSourceType.DEMO)
            | Q(source_type=CorpusSourceType.USER, owner=user)
        ).distinct()

    profile = get_user_profile(user)
    allowed_levels = ROLE_ACCESS_LEVELS.get(profile.role if profile else "", [])
    return queryset.filter(
        Q(source_type=CorpusSourceType.DEMO)
        | Q(
            source_type=CorpusSourceType.TEACHER,
            access_level__in=allowed_levels,
        )
        | Q(source_type=CorpusSourceType.USER, owner=user)
    ).distinct()


def can_create_personal_corpus(user: Any) -> bool:
    return workspace_access_scope(user) in {AccessScope.STANDARD, AccessScope.ADMIN}


def can_upload_personal_corpus(user: Any) -> bool:
    return workspace_access_scope(user) in {
        AccessScope.DEMO_ONLY,
        AccessScope.STANDARD,
        AccessScope.ADMIN,
    }


def upload_limits_for(user: Any) -> UploadLimits:
    profile = get_user_profile(user)
    if profile and profile.role == UserRole.TEST:
        default_max_file = settings.TEST_UPLOAD_MAX_FILE_BYTES
        default_total = settings.TEST_UPLOAD_TOTAL_BYTES
    else:
        default_max_file = settings.USER_UPLOAD_MAX_FILE_BYTES
        default_total = settings.USER_UPLOAD_TOTAL_BYTES
    total_bytes = (
        profile.upload_total_bytes
        if profile and profile.upload_total_bytes is not None
        else default_total
    )
    max_file_bytes = (
        profile.upload_max_file_bytes
        if profile and profile.upload_max_file_bytes is not None
        else default_max_file
    )
    return UploadLimits(
        max_file_bytes=min(max_file_bytes, total_bytes),
        total_bytes=total_bytes,
    )


def uploaded_bytes_for(user: Any) -> int:
    value = CorpusFile.objects.filter(
        corpus__source_type=CorpusSourceType.USER,
        corpus__owner=user,
    ).aggregate(total=Sum("size_bytes"))["total"]
    return int(value or 0)


@transaction.atomic
def create_personal_corpus(
    *,
    user: AbstractBaseUser,
    data: PersonalCorpusData,
) -> Corpus:
    if not can_create_personal_corpus(user):
        raise PermissionDenied("当前账号不能登记个人语料库。")
    if data.corpus_type not in CorpusType.values:
        raise ValueError(f"Unsupported corpus type: {data.corpus_type}")
    if data.language not in CorpusLanguage.values:
        raise ValueError(f"Unsupported corpus language: {data.language}")

    corpus = Corpus.objects.create(
        name=data.name,
        source_type=CorpusSourceType.USER,
        corpus_type=data.corpus_type,
        language=data.language,
        owner=user,
        access_level=CorpusAccessLevel.PRIVATE,
        status=CorpusStatus.CREATED,
        stage="registered",
        description=data.description,
    )
    return corpus


def create_uploaded_corpus(
    *,
    user: AbstractBaseUser,
    data: UploadedCorpusData,
    uploaded_file: UploadedFile,
) -> tuple[Corpus, "ProcessingTask"]:
    """Persist one private text corpus and create its asynchronous processing task."""
    if not can_upload_personal_corpus(user):
        raise PermissionDenied("当前账号不能上传个人语料库。")
    if data.language not in {CorpusLanguage.ZH, CorpusLanguage.EN}:
        raise ValidationError("上传语料的语言必须是中文或英文。")

    corpus_type = CorpusType.RAW_ZH if data.language == CorpusLanguage.ZH else CorpusType.RAW_EN
    return _create_uploaded_corpus(
        user=user,
        data=data,
        corpus_type=corpus_type,
        files=((uploaded_file, data.language),),
    )


def create_uploaded_parallel_corpus(
    *,
    user: AbstractBaseUser,
    data: UploadedCorpusData,
    corpus_type: str,
    zh_file: UploadedFile,
    en_file: UploadedFile,
) -> tuple[Corpus, "ProcessingTask"]:
    """Persist an authoritative human-aligned bilingual TXT pair."""
    if corpus_type not in {CorpusType.PAIRED_RAW_ZH_EN, CorpusType.PAIRED_TAGGED_ZH_EN}:
        raise ValidationError("不支持的双语上传类型。")
    if data.language != CorpusLanguage.ZH_EN:
        raise ValidationError("双语上传必须使用中英双语语言标记。")
    return _create_uploaded_corpus(
        user=user,
        data=data,
        corpus_type=corpus_type,
        files=((zh_file, CorpusLanguage.ZH), (en_file, CorpusLanguage.EN)),
    )


def _create_uploaded_corpus(
    *,
    user: AbstractBaseUser,
    data: UploadedCorpusData,
    corpus_type: str,
    files: tuple[tuple[UploadedFile, str], ...],
) -> tuple[Corpus, "ProcessingTask"]:
    if not can_upload_personal_corpus(user):
        raise PermissionDenied("当前账号不能上传个人语料库。")
    limits = upload_limits_for(user)
    declared_total = sum(_validate_upload_declaration(item, limits) for item, _ in files)
    corpus_id = uuid.uuid4()
    upload_root = (settings.DATA_ROOT / "user_uploads").resolve()
    upload_dir = (upload_root / str(user.pk) / str(corpus_id)).resolve()
    if not upload_dir.is_relative_to(upload_root):
        raise ValidationError("上传路径无效。")
    persisted_paths: list[Path] = []
    temporary_paths: list[Path] = []

    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=user.pk)
            used_bytes = uploaded_bytes_for(user)
            if used_bytes + declared_total > limits.total_bytes:
                remaining = max(0, limits.total_bytes - used_bytes)
                raise ValidationError(f"账号上传总额不足，当前剩余 {remaining} 字节。")

            upload_dir.mkdir(parents=True, exist_ok=True)
            stored: list[tuple[UploadedFile, str, Path, int, str]] = []
            actual_total = 0
            for uploaded_file, language in files:
                final_path = upload_dir / f"{uuid.uuid4().hex}.txt"
                temporary_path = upload_dir / f".{uuid.uuid4().hex}.uploading"
                temporary_paths.append(temporary_path)
                actual_size, encoding = _persist_text_upload(
                    uploaded_file=uploaded_file,
                    temporary_path=temporary_path,
                    final_path=final_path,
                    max_file_bytes=limits.max_file_bytes,
                )
                persisted_paths.append(final_path)
                actual_total += actual_size
                if used_bytes + actual_total > limits.total_bytes:
                    raise ValidationError("账号上传总额不足。")
                stored.append((uploaded_file, language, final_path, actual_size, encoding))

            corpus = Corpus.objects.create(
                id=corpus_id,
                name=data.name,
                source_type=CorpusSourceType.USER,
                corpus_type=corpus_type,
                language=data.language,
                owner=user,
                access_level=CorpusAccessLevel.PRIVATE,
                status=CorpusStatus.CREATED,
                stage="uploaded",
                description=data.description,
            )
            for uploaded_file, language, final_path, actual_size, encoding in stored:
                CorpusFile.objects.create(
                    corpus=corpus,
                    original_filename=Path(str(uploaded_file.name)).name,
                    stored_path=str(final_path),
                    detected_type=corpus_type,
                    language=language,
                    size_bytes=actual_size,
                    encoding=encoding,
                    status=CorpusFileStatus.PENDING,
                )
            CorpusDocumentation.objects.update_or_create(
                corpus=corpus,
                defaults={"file_count": len(stored)},
            )
            from apps.processing.services import create_processing_task

            task = create_processing_task(corpus=corpus, requested_by=user)
        return corpus, task
    except Exception:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
        for path in persisted_paths:
            path.unlink(missing_ok=True)
        _remove_empty_parents(upload_dir, stop=upload_root)
        raise


@transaction.atomic
def retry_user_corpus(*, corpus: Corpus, user: AbstractBaseUser) -> "ProcessingTask":
    if corpus.source_type != CorpusSourceType.USER or corpus.owner_id != user.pk:
        raise PermissionDenied("只能重试本人上传的语料库。")
    if corpus.status != CorpusStatus.FAILED:
        raise ValidationError("只有加工失败的语料库可以重试。")
    if not all(Path(item.stored_path).is_file() for item in corpus.files.all()):
        raise ValidationError("源文件缺失，无法重试；请删除后重新上传。")
    corpus.files.update(status=CorpusFileStatus.PENDING, error_message="")
    from apps.processing.services import create_processing_task

    return create_processing_task(corpus=corpus, requested_by=user)


@transaction.atomic
def delete_user_corpus(*, corpus: Corpus, user: AbstractBaseUser) -> None:
    locked = Corpus.objects.select_for_update().get(pk=corpus.pk)
    if locked.source_type != CorpusSourceType.USER or locked.owner_id != user.pk:
        raise PermissionDenied("只能删除本人上传的语料库。")
    if locked.processing_tasks.filter(status__in=["pending", "running"]).exists():
        raise ValidationError("语料仍在排队或加工中，暂不能删除。")

    corpus_id = str(locked.pk)
    owner_id = str(locked.owner_id)
    locked.delete()
    transaction.on_commit(
        lambda: _delete_user_corpus_files(owner_id, corpus_id),
        robust=True,
    )


def _validate_upload_declaration(uploaded_file: UploadedFile, limits: UploadLimits) -> int:
    original_filename = Path(str(uploaded_file.name)).name
    if Path(original_filename).suffix.lower() != ".txt":
        raise ValidationError("当前仅支持 .txt 文本语料。")
    declared_size = int(uploaded_file.size or 0)
    if declared_size <= 0:
        raise ValidationError("不能上传空文件。")
    if declared_size > limits.max_file_bytes:
        raise ValidationError(f"单个文件不能超过 {limits.max_file_bytes // (1024 * 1024)} MB。")
    return declared_size


def _persist_text_upload(
    *,
    uploaded_file: UploadedFile,
    temporary_path: Path,
    final_path: Path,
    max_file_bytes: int,
) -> tuple[int, str]:
    actual_size = 0
    with temporary_path.open("xb") as destination:
        for chunk in uploaded_file.chunks():
            actual_size += len(chunk)
            if actual_size > max_file_bytes:
                raise ValidationError(
                    f"单个文件不能超过 {max_file_bytes // (1024 * 1024)} MB。"
                )
            destination.write(chunk)
        destination.flush()
        os.fsync(destination.fileno())
    if actual_size <= 0:
        raise ValidationError("不能上传空文件。")
    encoding = _validate_text_content(temporary_path.read_bytes())
    scan_uploaded_file(temporary_path)
    os.replace(temporary_path, final_path)
    return actual_size, encoding


def _validate_text_content(data: bytes) -> str:
    has_unicode_bom = data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    if not has_unicode_bom:
        raw_controls = sum(byte < 32 and byte not in {9, 10, 13} for byte in data[:20000])
        if raw_controls > max(2, min(len(data), 20000) // 100):
            raise ValidationError("文件内容不像纯文本，请检查编码或文件类型。")
    text, encoding = decode_text(data)
    if not text.strip():
        raise ValidationError("文件不包含可加工的文本内容。")
    sample = text[:20000]
    controls = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
    replacements = sample.count("\ufffd")
    if controls > max(2, len(sample) // 1000) or replacements > max(2, len(sample) // 100):
        raise ValidationError("文件内容不像纯文本，请检查编码或文件类型。")
    return encoding


def _delete_user_corpus_files(owner_id: str, corpus_id: str) -> None:
    data_root = settings.DATA_ROOT.resolve()
    targets = (
        (data_root / "user_uploads", data_root / "user_uploads" / owner_id / corpus_id),
        (data_root / "processed", data_root / "processed" / corpus_id),
        (data_root / "indexes", data_root / "indexes" / corpus_id),
        (data_root / "exports", data_root / "exports" / corpus_id),
    )
    for root, target in targets:
        root = root.resolve()
        target = target.resolve()
        if target != root and target.is_relative_to(root) and target.exists():
            shutil.rmtree(target)
    _remove_empty_parents(data_root / "user_uploads" / owner_id, stop=data_root / "user_uploads")


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path.resolve()
    stop = stop.resolve()
    while current != stop and current.is_relative_to(stop):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _load_manifest_payload(manifest_path: Path) -> tuple[Path, dict[str, Any]]:
    path = manifest_path.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Manifest does not exist: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Manifest is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Manifest JSON must contain an object.")
    return path, payload


def load_manifest_record(manifest_path: Path, file_id: str) -> dict[str, Any]:
    _, payload = _load_manifest_payload(manifest_path)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Manifest JSON must contain a records list.")
    for record in records:
        if isinstance(record, dict) and record.get("file_id") == file_id:
            return record
    raise LookupError(f"Manifest record not found: {file_id}")


@transaction.atomic
def register_manifest_corpus(
    *,
    manifest_path: Path,
    file_id: str,
    source_type: str,
    access_level: str,
    name: str | None = None,
) -> tuple[Corpus, bool]:
    if source_type not in {CorpusSourceType.TEACHER, CorpusSourceType.DEMO}:
        raise ValueError("Manifest registration supports teacher or demo corpora only.")
    if access_level not in {
        CorpusAccessLevel.DEMO,
        CorpusAccessLevel.JUNIOR,
        CorpusAccessLevel.MIDDLE,
        CorpusAccessLevel.ADVANCED,
    }:
        raise ValueError(f"Unsupported corpus access level: {access_level}")

    manifest_file, payload = _load_manifest_payload(manifest_path)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Manifest JSON must contain a records list.")
    record = next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("file_id") == file_id
        ),
        None,
    )
    if record is None:
        raise LookupError(f"Manifest record not found: {file_id}")
    detected_type = str(record.get("detected_type", CorpusType.UNKNOWN))
    if detected_type not in CorpusType.values:
        detected_type = CorpusType.UNKNOWN
    language = _normalize_language(str(record.get("detected_language", "unknown")))
    corpus_name = name or str(record.get("title_guess") or record.get("filename") or file_id)

    corpus, created = Corpus.objects.update_or_create(
        manifest_file_id=file_id,
        defaults={
            "name": corpus_name,
            "source_type": source_type,
            "corpus_type": detected_type,
            "language": language,
            "owner": None,
            "access_level": access_level,
            "status": CorpusStatus.CREATED,
            "stage": "manifest_registered",
            "description": str(record.get("notes", "")),
            "manifest_relative_path": str(record.get("original_path", "")),
            "manifest_size_bytes": _non_negative_int(record.get("size_bytes")),
            "manifest_encoding": str(record.get("encoding", "")),
        },
    )
    CorpusDocumentation.objects.get_or_create(corpus=corpus)
    inbox_root = Path(str(payload.get("inbox_root", "")))
    if not inbox_root.is_absolute():
        inbox_root = (manifest_file.parent / inbox_root).resolve()
    source_path = (inbox_root / str(record.get("original_path", ""))).resolve()
    CorpusFile.objects.update_or_create(
        corpus=corpus,
        manifest_file_id=file_id,
        defaults={
            "original_filename": str(record.get("filename") or source_path.name),
            "stored_path": str(source_path),
            "detected_type": detected_type,
            "language": language,
            "size_bytes": _non_negative_int(record.get("size_bytes")),
            "encoding": str(record.get("encoding", "")),
            "status": CorpusFileStatus.PENDING,
            "error_message": "",
        },
    )
    CorpusDocumentation.objects.filter(corpus=corpus).update(file_count=corpus.files.count())
    return corpus, created


@transaction.atomic
def register_corpus_file(*, corpus: Corpus, data: CorpusFileData) -> tuple[CorpusFile, bool]:
    path = data.path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Corpus source does not exist: {path}")
    if path.suffix.lower() not in {".txt", ".tsv"}:
        raise ValueError("Corpus source must be a txt or tsv file.")
    if data.detected_type not in CorpusType.values:
        raise ValueError(f"Unsupported corpus type: {data.detected_type}")
    if data.language not in CorpusLanguage.values:
        raise ValueError(f"Unsupported corpus language: {data.language}")

    corpus_file, created = CorpusFile.objects.update_or_create(
        corpus=corpus,
        stored_path=str(path),
        defaults={
            "original_filename": path.name,
            "manifest_file_id": data.manifest_file_id,
            "detected_type": data.detected_type,
            "language": data.language,
            "size_bytes": path.stat().st_size,
            "encoding": data.encoding,
            "status": CorpusFileStatus.PENDING,
            "error_message": "",
        },
    )
    CorpusDocumentation.objects.filter(corpus=corpus).update(file_count=corpus.files.count())
    return corpus_file, created


def _normalize_language(value: str) -> str:
    if value in CorpusLanguage.values:
        return value
    if value in {"bilingual", "zh-en", "en-zh"}:
        return CorpusLanguage.ZH_EN
    return CorpusLanguage.UNKNOWN


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
