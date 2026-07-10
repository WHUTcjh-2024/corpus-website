from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from .scanner import ManifestRecord, ScanResult


MANIFEST_FIELDS = [
    "file_id",
    "original_path",
    "filename",
    "size_bytes",
    "encoding",
    "detected_language",
    "detected_type",
    "confidence",
    "probable_pair_id",
    "stage_or_period",
    "author",
    "title_guess",
    "date_guess",
    "notes",
    "status",
]


def write_manifest(scan_result: ScanResult, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "corpus_manifest.csv"
    json_path = output_dir / "corpus_manifest.json"

    write_manifest_csv(scan_result.records, csv_path)
    write_manifest_json(scan_result, json_path)
    return csv_path, json_path


def write_manifest_csv(records: list[ManifestRecord], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_dict())


def write_manifest_json(scan_result: ScanResult, path: Path) -> None:
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "inbox_root": str(scan_result.inbox_root),
        "summary": scan_result.summary,
        "records": [record.as_dict() for record in scan_result.records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
