from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, QuerySet

from apps.accounts.models import UserRole
from apps.accounts.permissions import AccessScope, get_user_profile, workspace_access_scope

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


def visible_corpora_for(user: Any) -> QuerySet[Corpus]:
    queryset = Corpus.objects.select_related("owner").all()
    scope = workspace_access_scope(user)
    if scope == AccessScope.NONE:
        return queryset.none()
    if scope == AccessScope.ADMIN:
        return queryset

    queryset = queryset.exclude(status=CorpusStatus.DISABLED)
    if scope == AccessScope.DEMO_ONLY:
        return queryset.filter(source_type=CorpusSourceType.DEMO)

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
