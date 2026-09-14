from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402

from apps.corpora.models import Corpus, CorpusStatus  # noqa: E402


FORMAL_PREFIX = "teacher-formal-v1-"
DISPLAY_NAMES = {
    "paired_tagged_zh_en": "汉英人工对齐标注语料",
    "paired_raw_zh_en": "汉英原文候选配对语料",
    "raw_zh": "中文原文语料",
    "raw_en": "英文译文语料",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出正式教师语料入库验收证据。")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=settings.DATA_ROOT
        / "manifests"
        / "formal_teacher_corpus"
        / "corpus_manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "test-evidence" / "正式语料导入验证结果.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = manifest["records"]
    corpora = list(
        Corpus.objects.filter(manifest_file_id__startswith=FORMAL_PREFIX)
        .select_related("documentation")
        .order_by("manifest_file_id")
    )
    if len(corpora) != 4:
        raise RuntimeError(f"正式语料库数量应为4，实际为{len(corpora)}。")
    not_ready = [corpus.name for corpus in corpora if corpus.status != CorpusStatus.READY]
    if not_ready:
        raise RuntimeError("存在未完成索引的正式语料库：" + "、".join(not_ready))

    validation_output = io.StringIO()
    call_command(
        "validate_corpus_indexes",
        corpus_ids=[str(corpus.pk) for corpus in corpora],
        stdout=validation_output,
    )

    corpus_rows: list[dict[str, object]] = []
    for corpus in corpora:
        documentation = corpus.documentation
        index_directory = settings.DATA_ROOT / "indexes" / str(corpus.pk)
        index_path = index_directory / "kwic_index.sqlite"
        with closing(sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)) as db:
            sqlite_document_count = int(
                db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            sqlite_token_count = int(
                db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            )
            parallel_pair_count = int(
                db.execute("SELECT COUNT(*) FROM parallel_pairs").fetchone()[0]
            )
        if sqlite_document_count != documentation.document_count:
            raise RuntimeError(f"{corpus.name}文档数与索引不一致。")
        if sqlite_token_count != documentation.token_count:
            raise RuntimeError(f"{corpus.name}词元数与索引不一致。")
        corpus_rows.append(
            {
                "corpus_id": str(corpus.pk),
                "corpus_type": corpus.corpus_type,
                "display_name": DISPLAY_NAMES[corpus.corpus_type],
                "status": corpus.status,
                "file_count": documentation.file_count,
                "document_count": documentation.document_count,
                "paragraph_count": documentation.paragraph_count,
                "sentence_count": documentation.sentence_count,
                "token_count": documentation.token_count,
                "type_count": documentation.type_count,
                "parallel_pair_count": parallel_pair_count,
                "index_size_bytes": sum(
                    path.stat().st_size for path in index_directory.rglob("*") if path.is_file()
                ),
                "validation_passed": True,
            }
        )

    numeric_keys = (
        "file_count",
        "document_count",
        "paragraph_count",
        "sentence_count",
        "token_count",
        "type_count",
        "parallel_pair_count",
        "index_size_bytes",
    )
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_file_count": len(records),
        "registered_file_count": sum(
            1 for record in records if record["status"] != "quarantined"
        ),
        "quarantined_file_count": sum(
            1 for record in records if record["status"] == "quarantined"
        ),
        "corpora": corpus_rows,
        "totals": {
            key: sum(int(row[key]) for row in corpus_rows) for key in numeric_keys
        },
        "validation_command_output": validation_output.getvalue().strip().splitlines(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
