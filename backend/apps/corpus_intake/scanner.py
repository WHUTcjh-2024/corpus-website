from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .classifiers import SUPPORTED_SUFFIXES, ClassificationResult, classify_path


@dataclass
class ManifestRecord:
    file_id: str
    original_path: str
    filename: str
    size_bytes: int
    encoding: str
    detected_language: str
    detected_type: str
    confidence: float
    probable_pair_id: str
    stage_or_period: str
    author: str
    title_guess: str
    date_guess: str
    notes: str
    status: str = "pending_review"

    def as_dict(self) -> dict[str, object]:
        return {
            "file_id": self.file_id,
            "original_path": self.original_path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "encoding": self.encoding,
            "detected_language": self.detected_language,
            "detected_type": self.detected_type,
            "confidence": round(self.confidence, 3),
            "probable_pair_id": self.probable_pair_id,
            "stage_or_period": self.stage_or_period,
            "author": self.author,
            "title_guess": self.title_guess,
            "date_guess": self.date_guess,
            "notes": self.notes,
            "status": self.status,
        }


@dataclass(frozen=True)
class ScanResult:
    inbox_root: Path
    records: list[ManifestRecord]
    summary: dict[str, object]


def scan_inbox(inbox_root: Path) -> ScanResult:
    root = inbox_root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Corpus inbox does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Corpus inbox is not a directory: {root}")

    records: list[ManifestRecord] = []
    for path in sorted(_iter_supported_files(root), key=lambda p: p.relative_to(root).as_posix().lower()):
        classification = classify_path(path)
        records.append(_to_record(root, path, classification))

    _apply_pair_detection(records)
    summary = summarize_records(records)
    return ScanResult(inbox_root=root, records=records, summary=summary)


def summarize_records(records: list[ManifestRecord]) -> dict[str, object]:
    type_counts = Counter(record.detected_type for record in records)
    language_counts = Counter(record.detected_language for record in records)
    pair_ids = {record.probable_pair_id for record in records if record.probable_pair_id}
    total_size = sum(record.size_bytes for record in records)
    return {
        "total_files": len(records),
        "total_size_bytes": total_size,
        "type_counts": dict(sorted(type_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "probable_pair_count": len(pair_ids),
        "unknown_file_count": type_counts.get("unknown", 0),
    }


def _iter_supported_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path


def _to_record(root: Path, path: Path, classification: ClassificationResult) -> ManifestRecord:
    relative = path.relative_to(root).as_posix()
    return ManifestRecord(
        file_id=_file_id(relative),
        original_path=relative,
        filename=path.name,
        size_bytes=path.stat().st_size,
        encoding=classification.encoding,
        detected_language=classification.detected_language,
        detected_type=classification.detected_type,
        confidence=classification.confidence,
        probable_pair_id="",
        stage_or_period=classification.stage_or_period,
        author=classification.author,
        title_guess=classification.title_guess,
        date_guess=classification.date_guess,
        notes=";".join(classification.notes),
    )


def _apply_pair_detection(records: list[ManifestRecord]) -> None:
    buckets: dict[str, list[ManifestRecord]] = defaultdict(list)
    for record in records:
        if record.detected_type not in {"raw_zh", "raw_en"}:
            continue
        buckets[_pair_key(record.original_path, record.filename)].append(record)

    pair_index = 1
    for key in sorted(buckets):
        bucket = buckets[key]
        languages = {record.detected_language for record in bucket}
        if not {"zh", "en"}.issubset(languages):
            continue
        pair_id = f"pair-{pair_index:04d}"
        pair_index += 1
        for record in bucket:
            if record.detected_language in {"zh", "en"}:
                record.probable_pair_id = pair_id
                record.detected_type = "paired_raw_zh_en"
                record.confidence = max(record.confidence, 0.86)
                record.notes = _append_note(record.notes, f"pair_key={key}")


def _file_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()
    return digest[:12]


def _pair_key(original_path: str, filename: str) -> str:
    path_parts = Path(original_path).parts[:-1]
    parent_key = "/".join(_normalize_name(part) for part in path_parts)
    stem = _normalize_name(Path(filename).stem)
    return f"{parent_key}/{stem}" if parent_key else stem


def _normalize_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\b(raw|txt|pos|untagged|unalignment|alignment)\b", "", value)
    value = re.sub(r"(中文|中译|中|官译|英文|英语|英译|外译|译文|原文|段对齐)", "", value)
    value = re.sub(r"[_\-\s]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def _append_note(notes: str, note: str) -> str:
    return f"{notes};{note}" if notes else note
