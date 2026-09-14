from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


GOLD_FOLDER = "抽样对齐-马克思主义中国化经典文献汉英平行语料库"
TEACHER_CORPUS_ID = "71d92f26-c5e5-485f-ac83-3ebccb6a9acc"
REFERENCE_ZH_CORPUS_ID = "36b657e1-2362-42d6-8908-6d88b7c53083"
SENTENCE_ID_RE = re.compile(r"<s\s+n\s*=\s*[\"'](\d+)[\"']", re.IGNORECASE)
PARAGRAPH_ID_RE = re.compile(r"<p\s+n\s*=\s*[\"'](\d+)[\"']", re.IGNORECASE)
DATE_IN_NAME_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
ZH_FILENAME_RE = re.compile(r"(?:^|-)中\s*\d+-")
EN_FILENAME_RE = re.compile(r"(?:^|-)(?:官译|英译|外译|英)\s*\d+-")


@dataclass(slots=True)
class Check:
    category: str
    name: str
    expected: str
    actual: str
    passed: bool
    evidence: str = ""


def configure_backend(project_root: Path) -> None:
    backend = project_root / "backend"
    sys.path.insert(0, str(backend))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test_sqlite")


def percentage(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) * 100.0 / float(denominator), 4)


def nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 4)


def expected_language(relative_path: str) -> str:
    path = Path(relative_path)
    filename = path.name
    if ZH_FILENAME_RE.search(filename):
        return "zh"
    if EN_FILENAME_RE.search(filename):
        return "en"
    for part in reversed(path.parts[:-1]):
        if "中文" in part and "+" not in part:
            return "zh"
        if any(marker in part for marker in ("英文", "官译", "英译", "外译")):
            return "en"
    return ""


def decode_record(root: Path, record: Any) -> tuple[str, bool, int]:
    data = (root / Path(record.original_path)).read_bytes()
    encoding = record.encoding
    try:
        if encoding == "utf-8-replace":
            text = data.decode("utf-8", errors="replace")
        else:
            text = data.decode(encoding)
        return text, True, text.count("\ufffd")
    except (LookupError, UnicodeDecodeError):
        return "", False, 0


def balanced_tag(text: str, tag: str) -> bool:
    opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", text, flags=re.IGNORECASE))
    closes = len(re.findall(rf"<\s*/\s*{tag}\s*>", text, flags=re.IGNORECASE))
    return opens == closes


def numeric_ids(text: str, pattern: re.Pattern[str]) -> list[int]:
    return [int(value) for value in pattern.findall(text)]


def ordered_unique(values: list[int]) -> bool:
    return len(values) == len(set(values)) and values == sorted(values)


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_check(
    checks: list[Check],
    *,
    category: str,
    name: str,
    expected: Any,
    actual: Any,
    passed: bool,
    evidence: str = "",
) -> None:
    checks.append(
        Check(
            category=category,
            name=name,
            expected=str(expected),
            actual=str(actual),
            passed=bool(passed),
            evidence=evidence,
        )
    )


def profile_corpus(source_root: Path, project_root: Path) -> dict[str, Any]:
    configure_backend(project_root)
    from apps.corpus_intake.scanner import scan_inbox
    from apps.processing.contracts import SourceFile
    from apps.processing.importers.paired_tagged_structure import PairedTaggedStructureImporter

    started = time.perf_counter()
    scan = scan_inbox(source_root)
    scan_seconds = time.perf_counter() - started
    records = scan.records

    collection_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
    encoding_counts: Counter[str] = Counter()
    confidence_bands: Counter[str] = Counter()
    decode_failures: list[str] = []
    replacement_files: list[str] = []
    empty_files: list[str] = []
    language_cases = 0
    language_matches = 0
    language_mismatches: list[dict[str, str]] = []
    texts: dict[str, str] = {}
    hashes: dict[str, list[str]] = defaultdict(list)
    stage_counts: Counter[str] = Counter()
    valid_dates = 0
    invalid_dates: list[str] = []
    date_candidates = 0

    for record in records:
        relative = record.original_path
        path = source_root / Path(relative)
        top_level = Path(relative).parts[0]
        collection_counts[top_level]["files"] += 1
        collection_counts[top_level]["bytes"] += record.size_bytes
        encoding_counts[record.encoding] += 1
        stage_counts[record.stage_or_period or "未标注"] += 1
        if record.size_bytes == 0:
            empty_files.append(relative)

        confidence = float(record.confidence)
        if confidence >= 0.9:
            confidence_bands[">=0.90"] += 1
        elif confidence >= 0.8:
            confidence_bands["0.80-0.89"] += 1
        elif confidence >= 0.7:
            confidence_bands["0.70-0.79"] += 1
        else:
            confidence_bands["<0.70"] += 1

        text, decoded, replacements = decode_record(source_root, record)
        if decoded:
            texts[relative] = text
        else:
            decode_failures.append(relative)
        if replacements:
            replacement_files.append(relative)

        expected = expected_language(relative)
        if expected and record.size_bytes:
            language_cases += 1
            if record.detected_language == expected:
                language_matches += 1
            else:
                language_mismatches.append(
                    {
                        "path": relative,
                        "expected": expected,
                        "actual": record.detected_language,
                    }
                )

        name_date = DATE_IN_NAME_RE.search(record.filename)
        if name_date:
            date_candidates += 1
            year, month, day = map(int, name_date.groups())
            if 1900 <= year <= 2026 and 0 <= month <= 12 and 0 <= day <= 31:
                valid_dates += 1
            else:
                invalid_dates.append(relative)

        hashes[checksum(path)].append(relative)

    duplicate_groups = [paths for paths in hashes.values() if len(paths) > 1]
    duplicate_instances = sum(len(paths) - 1 for paths in duplicate_groups)

    gold_records = [
        record for record in records if Path(record.original_path).parts[0] == GOLD_FOLDER
    ]
    gold_type_matches = sum(
        record.detected_type == "paired_tagged_zh_en" for record in gold_records
    )
    pair_groups: dict[int, dict[str, Any]] = defaultdict(dict)
    for record in gold_records:
        relative = Path(record.original_path)
        match = re.match(r"^(\d+)-", record.filename)
        if not match:
            continue
        side = "zh" if "中文1-40" in relative.parts else "en" if "英文1-40" in relative.parts else ""
        if side:
            pair_groups[int(match.group(1))][side] = record

    paired_cases = 0
    paired_matches = 0
    pair_detection_failures: list[dict[str, Any]] = []
    structure_files = 0
    structure_balanced = 0
    ordered_sentence_files = 0
    ordered_paragraph_files = 0
    import_success = 0
    import_failures: list[dict[str, str]] = []
    pairable_sentence_capacity = 0
    imported_sentence_pairs = 0
    pairable_paragraph_capacity = 0
    imported_paragraph_pairs = 0
    alignment_method_counts: Counter[str] = Counter()
    zh_tokens = 0
    zh_pos_tokens = 0
    en_tokens = 0
    en_pos_tokens = 0
    exact_sentence_set_pairs = 0
    exact_paragraph_set_pairs = 0
    alignment_rows: list[dict[str, Any]] = []

    for pair_number in sorted(pair_groups):
        sides = pair_groups[pair_number]
        if set(sides) != {"zh", "en"}:
            pair_detection_failures.append(
                {"pair": pair_number, "reason": "gold folder lacks one language side"}
            )
            continue
        zh_record = sides["zh"]
        en_record = sides["en"]
        paired_cases += 1
        scanner_pair_ok = bool(
            zh_record.probable_pair_id
            and zh_record.probable_pair_id == en_record.probable_pair_id
        )
        paired_matches += scanner_pair_ok
        if not scanner_pair_ok:
            pair_detection_failures.append(
                {
                    "pair": pair_number,
                    "reason": "scanner pair identifiers differ",
                    "zh_pair_id": zh_record.probable_pair_id,
                    "en_pair_id": en_record.probable_pair_id,
                }
            )

        zh_text = texts.get(zh_record.original_path, "")
        en_text = texts.get(en_record.original_path, "")
        zh_sentence_ids = numeric_ids(zh_text, SENTENCE_ID_RE)
        en_sentence_ids = numeric_ids(en_text, SENTENCE_ID_RE)
        zh_paragraph_ids = numeric_ids(zh_text, PARAGRAPH_ID_RE)
        en_paragraph_ids = numeric_ids(en_text, PARAGRAPH_ID_RE)
        common_sentences = len(set(zh_sentence_ids) & set(en_sentence_ids))
        common_paragraphs = len(set(zh_paragraph_ids) & set(en_paragraph_ids))
        exact_sentence_set_pairs += set(zh_sentence_ids) == set(en_sentence_ids)
        exact_paragraph_set_pairs += set(zh_paragraph_ids) == set(en_paragraph_ids)

        for text in (zh_text, en_text):
            structure_files += 1
            if all(balanced_tag(text, tag) for tag in ("head", "p", "s", "date", "author")):
                structure_balanced += 1
        ordered_sentence_files += ordered_unique(zh_sentence_ids)
        ordered_sentence_files += ordered_unique(en_sentence_ids)
        ordered_paragraph_files += ordered_unique(zh_paragraph_ids)
        ordered_paragraph_files += ordered_unique(en_paragraph_ids)

        try:
            sources = (
                SourceFile(
                    id=f"gold-{pair_number}-zh",
                    filename=zh_record.filename,
                    path=source_root / Path(zh_record.original_path),
                    detected_type="paired_tagged_zh_en",
                    language="zh",
                    encoding=zh_record.encoding,
                    size_bytes=zh_record.size_bytes,
                ),
                SourceFile(
                    id=f"gold-{pair_number}-en",
                    filename=en_record.filename,
                    path=source_root / Path(en_record.original_path),
                    detected_type="paired_tagged_zh_en",
                    language="en",
                    encoding=en_record.encoding,
                    size_bytes=en_record.size_bytes,
                ),
            )
            result = next(PairedTaggedStructureImporter().iter_import(sources))
            import_success += 1
            sentence_pairs = [
                pair for pair in result.parallel_pairs if pair.alignment_unit == "sentence"
            ]
            paragraph_pairs = [
                pair for pair in result.parallel_pairs if pair.alignment_unit == "paragraph"
            ]
            result_zh_sentences = [
                sentence for sentence in result.sentences if sentence.language == "zh"
            ]
            result_en_sentences = [
                sentence for sentence in result.sentences if sentence.language == "en"
            ]
            result_zh_paragraphs = [
                paragraph for paragraph in result.paragraphs if paragraph.language == "zh"
            ]
            result_en_paragraphs = [
                paragraph for paragraph in result.paragraphs if paragraph.language == "en"
            ]
            pairable_sentence_capacity += min(
                len(result_zh_sentences), len(result_en_sentences)
            )
            pairable_paragraph_capacity += min(
                len(result_zh_paragraphs), len(result_en_paragraphs)
            )
            imported_sentence_pairs += len(sentence_pairs)
            imported_paragraph_pairs += len(paragraph_pairs)
            alignment_method_counts.update(pair.method for pair in result.parallel_pairs)
            pair_zh_tokens = [token for token in result.tokens if token.language == "zh"]
            pair_en_tokens = [token for token in result.tokens if token.language == "en"]
            zh_tokens += len(pair_zh_tokens)
            zh_pos_tokens += sum(bool(token.pos) for token in pair_zh_tokens)
            en_tokens += len(pair_en_tokens)
            en_pos_tokens += sum(bool(token.pos) for token in pair_en_tokens)
            alignment_rows.append(
                {
                    "pair": pair_number,
                    "zh_sentences": len(zh_sentence_ids),
                    "en_sentences": len(en_sentence_ids),
                    "common_sentence_ids": common_sentences,
                    "pairable_sentence_capacity": min(
                        len(result_zh_sentences), len(result_en_sentences)
                    ),
                    "imported_sentence_pairs": len(sentence_pairs),
                    "zh_paragraphs": len(zh_paragraph_ids),
                    "en_paragraphs": len(en_paragraph_ids),
                    "common_paragraph_ids": common_paragraphs,
                    "pairable_paragraph_capacity": min(
                        len(result_zh_paragraphs), len(result_en_paragraphs)
                    ),
                    "imported_paragraph_pairs": len(paragraph_pairs),
                    "warnings": len(result.warnings),
                }
            )
        except Exception as exc:  # noqa: BLE001 - preserve corpus-specific importer failures
            import_failures.append({"pair": str(pair_number), "error": str(exc)})

    return {
        "source_root": str(source_root),
        "scan_seconds": round(scan_seconds, 3),
        "scan_throughput_files_per_second": round(len(records) / scan_seconds, 3),
        "summary": scan.summary,
        "collections": [
            {"name": name, **values}
            for name, values in sorted(collection_counts.items())
        ],
        "encodings": dict(encoding_counts.most_common()),
        "confidence_bands": dict(confidence_bands),
        "decode_success_count": len(records) - len(decode_failures),
        "decode_success_rate": percentage(len(records) - len(decode_failures), len(records)),
        "decode_failures": decode_failures,
        "replacement_character_files": replacement_files,
        "empty_files": empty_files,
        "language_gold_count": language_cases,
        "language_match_count": language_matches,
        "language_accuracy": percentage(language_matches, language_cases),
        "language_mismatches": language_mismatches[:100],
        "date_candidate_count": date_candidates,
        "valid_date_count": valid_dates,
        "date_format_validity": percentage(valid_dates, date_candidates),
        "invalid_dates": invalid_dates[:100],
        "stage_counts": dict(sorted(stage_counts.items())),
        "unique_content_count": len(hashes),
        "exact_duplicate_group_count": len(duplicate_groups),
        "exact_duplicate_instance_count": duplicate_instances,
        "exact_duplicate_rate": percentage(duplicate_instances, len(records)),
        "duplicate_group_samples": duplicate_groups[:20],
        "gold": {
            "file_count": len(gold_records),
            "type_match_count": gold_type_matches,
            "type_accuracy": percentage(gold_type_matches, len(gold_records)),
            "pair_count": paired_cases,
            "scanner_pair_match_count": paired_matches,
            "scanner_pair_accuracy": percentage(paired_matches, paired_cases),
            "pair_detection_failures": pair_detection_failures,
            "structure_file_count": structure_files,
            "balanced_structure_count": structure_balanced,
            "balanced_structure_rate": percentage(structure_balanced, structure_files),
            "ordered_unique_sentence_id_files": ordered_sentence_files,
            "ordered_unique_paragraph_id_files": ordered_paragraph_files,
            "import_success_count": import_success,
            "import_success_rate": percentage(import_success, paired_cases),
            "import_failures": import_failures,
            "pairable_sentence_capacity": pairable_sentence_capacity,
            "imported_sentence_pairs": imported_sentence_pairs,
            "sentence_alignment_retention": percentage(
                imported_sentence_pairs, pairable_sentence_capacity
            ),
            "pairable_paragraph_capacity": pairable_paragraph_capacity,
            "imported_paragraph_pairs": imported_paragraph_pairs,
            "paragraph_alignment_retention": percentage(
                imported_paragraph_pairs, pairable_paragraph_capacity
            ),
            "alignment_method_counts": dict(alignment_method_counts),
            "exact_sentence_set_pair_count": exact_sentence_set_pairs,
            "exact_paragraph_set_pair_count": exact_paragraph_set_pairs,
            "zh_token_count": zh_tokens,
            "zh_pos_tag_count": zh_pos_tokens,
            "zh_pos_coverage": percentage(zh_pos_tokens, zh_tokens),
            "en_token_count": en_tokens,
            "en_pos_tag_count": en_pos_tokens,
            "en_pos_coverage": percentage(en_pos_tokens, en_tokens),
            "alignment_rows": alignment_rows,
        },
    }


def timed(name: str, action: Callable[[], Any], iterations: int = 20) -> dict[str, Any]:
    action()
    durations: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        action()
        durations.append((time.perf_counter() - started) * 1000)
    return {
        "name": name,
        "iterations": iterations,
        "mean_ms": round(statistics.fmean(durations), 4),
        "median_ms": round(statistics.median(durations), 4),
        "p95_ms": nearest_rank(durations, 0.95),
        "max_ms": round(max(durations), 4),
    }


def run_platform_accuracy(project_root: Path) -> dict[str, Any]:
    configure_backend(project_root)
    from apps.parallel.contracts import ParallelQuery
    from apps.parallel.engine import ParallelSearchEngine
    from apps.search.kwic import KwicSearchEngine
    from apps.search.query_engine import ComplexQueryEngine
    from apps.statistics.engine import StatisticsEngine

    data_root = project_root / "data"
    statistics_engine = StatisticsEngine(data_root=data_root, corpus_id=TEACHER_CORPUS_ID)
    kwic = KwicSearchEngine(data_root=data_root, corpus_id=TEACHER_CORPUS_ID)
    parallel_engine = ParallelSearchEngine(data_root=data_root, corpus_id=TEACHER_CORPUS_ID)
    cqp_engine = ComplexQueryEngine(data_root=data_root, corpus_id=TEACHER_CORPUS_ID)
    checks: list[Check] = []

    zh_word = statistics_engine.word_list(language="zh", page_size=20)
    en_word = statistics_engine.word_list(language="en", page_size=20)
    add_check(checks, category="Word", name="中文有效词次", expected=9908, actual=zh_word.total_tokens, passed=zh_word.total_tokens == 9908)
    add_check(checks, category="Word", name="中文词形数", expected=2323, actual=zh_word.total_types, passed=zh_word.total_types == 2323)
    add_check(checks, category="Word", name="中文最高频词", expected="的/570", actual=f"{zh_word.rows[0].term}/{zh_word.rows[0].frequency}", passed=(zh_word.rows[0].term, zh_word.rows[0].frequency) == ("的", 570))
    add_check(checks, category="Word", name="英文有效词次", expected=13601, actual=en_word.total_tokens, passed=en_word.total_tokens == 13601)
    add_check(checks, category="Word", name="英文词形数", expected=2542, actual=en_word.total_types, passed=en_word.total_types == 2542)
    add_check(checks, category="Word", name="英文最高频词", expected="the/1398", actual=f"{en_word.rows[0].term}/{en_word.rows[0].frequency}", passed=(en_word.rows[0].term.casefold(), en_word.rows[0].frequency) == ("the", 1398))

    zh_kwic = kwic.search("农民", language="zh", sort_keys=("R1",))
    en_regex = kwic.search(r"\bpeasant\b", language="en", full_regex=True)
    sample_a = kwic.search("农民", language="zh", sample_size=25, sample_seed=20260803)
    sample_b = kwic.search("农民", language="zh", sample_size=25, sample_seed=20260803)
    add_check(checks, category="KWIC", name="中文农民命中数", expected=259, actual=zh_kwic.total, passed=zh_kwic.total == 259)
    add_check(checks, category="KWIC", name="英文 peasant 正则命中数", expected=158, actual=en_regex.total, passed=en_regex.total == 158)
    add_check(checks, category="KWIC", name="固定随机种子可重复", expected="首条行号一致", actual=f"{sample_a.hits[0].row_id}/{sample_b.hits[0].row_id}", passed=sample_a.hits[0].row_id == sample_b.hits[0].row_id)
    add_check(checks, category="KWIC", name="随机结果集规模", expected="25/259", actual=f"{sample_a.total}/{sample_a.available_total}", passed=(sample_a.total, sample_a.available_total) == (25, 259))
    add_check(checks, category="KWIC", name="KPF 计数有效", expected="全部 >= 1", actual=min(hit.kpf_count for hit in zh_kwic.hits), passed=all(hit.kpf_count >= 1 for hit in zh_kwic.hits))

    file_view = kwic.file_view(document_id=zh_kwic.hits[0].document_id, language="zh", row_id=zh_kwic.hits[0].row_id, query="农民")
    add_check(checks, category="File View", name="全文命中数", expected=259, actual=file_view.hit_count, passed=file_view.hit_count == 259)
    add_check(checks, category="File View", name="全文词次词形", expected="12061/2340", actual=f"{file_view.token_count}/{file_view.type_count}", passed=(file_view.token_count, file_view.type_count) == (12061, 2340))

    clusters = statistics_engine.clusters("农民", language="zh", cluster_size=2, query_position="left", min_frequency=2, page_size=20)
    add_check(checks, category="Cluster", name="农民二词簇类型数", expected=28, actual=clusters.total_types, passed=clusters.total_types == 28)
    add_check(checks, category="Cluster", name="最高频词簇", expected="农民协会/49", actual=f"{clusters.rows[0].cluster}/{clusters.rows[0].frequency}", passed=(clusters.rows[0].cluster, clusters.rows[0].frequency) == ("农民协会", 49))

    ngrams = statistics_engine.ngrams(language="en", n=3, open_slot=2, min_frequency=10, page_size=20)
    first_ngram = ngrams.rows[0]
    add_check(checks, category="N gram", name="开放槽类型数", expected=51, actual=ngrams.total_types, passed=ngrams.total_types == 51)
    add_check(checks, category="N gram", name="最高频开放槽", expected="the <*> of/207/136", actual=f"{first_ngram.ngram.casefold()}/{first_ngram.frequency}/{first_ngram.slot_type_count}", passed=(first_ngram.ngram.casefold(), first_ngram.frequency, first_ngram.slot_type_count) == ("the <*> of", 207, 136))
    add_check(checks, category="N gram", name="开放槽熵", expected=6.771328, actual=round(first_ngram.slot_entropy, 6), passed=round(first_ngram.slot_entropy, 6) == 6.771328)

    collocates = statistics_engine.collocates("农民", language="zh", left_span=2, right_span=2, min_frequency=2, page_size=20)
    add_check(checks, category="Collocate", name="节点频次", expected=259, actual=collocates.node_frequency, passed=collocates.node_frequency == 259)
    add_check(checks, category="Collocate", name="最高频搭配", expected="协会/50", actual=f"{collocates.rows[0].term}/{collocates.rows[0].frequency}", passed=(collocates.rows[0].term, collocates.rows[0].frequency) == ("协会", 50))
    finite_statistics = all(math.isfinite(value) for value in (collocates.rows[0].mutual_information, collocates.rows[0].log_likelihood, collocates.rows[0].chi_square, collocates.rows[0].p_value))
    add_check(checks, category="Collocate", name="统计量有限", expected="MI LL 卡方 p 均为有限数", actual=finite_statistics, passed=finite_statistics)

    plot = statistics_engine.concordance_plot("农民", language="zh")
    add_check(checks, category="Plot", name="命中分布守恒", expected=259, actual=sum(row.hit_count for row in plot.documents), passed=plot.total == 259 and sum(row.hit_count for row in plot.documents) == 259)
    cloud = statistics_engine.wordcloud(language="zh", min_frequency=2, max_words=25)
    add_check(checks, category="Wordcloud", name="词云词数和首词", expected="25/的", actual=f"{len(cloud.terms)}/{cloud.terms[0].term}", passed=(len(cloud.terms), cloud.terms[0].term) == (25, "的"))
    cqp = cqp_engine.search('[word="the"] [] [word="of"]', language="en")
    add_check(checks, category="CQP", name="三词模式命中数", expected=207, actual=cqp.total, passed=cqp.total == 207)
    parallel = parallel_engine.search(ParallelQuery(q="农民", search_side="zh", alignment_unit="sentence"))
    add_check(checks, category="Parallel", name="平行句命中数", expected=259, actual=parallel.total, passed=parallel.total == 259)
    add_check(checks, category="Parallel", name="首条英译对应词", expected="包含 peasant", actual="peasant" in parallel.hits[0].en_text.casefold(), passed="peasant" in parallel.hits[0].en_text.casefold())

    index_path = data_root / "indexes" / TEACHER_CORPUS_ID / "kwic_index.sqlite"
    with sqlite3.connect(index_path) as connection:
        for language in ("zh", "en"):
            rows = connection.execute(
                """
                SELECT display, frequency
                FROM word_totals
                WHERE language = ? AND is_punctuation = 0
                ORDER BY frequency DESC, normalized ASC
                LIMIT 10
                """,
                (language,),
            ).fetchall()
            for term, expected_frequency in rows:
                actual_frequency = kwic.search(term, language=language).total
                add_check(
                    checks,
                    category="SQL 交叉核对",
                    name=f"{language} 词项 {term}",
                    expected=expected_frequency,
                    actual=actual_frequency,
                    passed=actual_frequency == expected_frequency,
                    evidence="word_totals 与 KWIC 独立路径核对",
                )

    reference_engine = StatisticsEngine(data_root=data_root, corpus_id=REFERENCE_ZH_CORPUS_ID)
    keywords = statistics_engine.keywords(reference=reference_engine, reference_name="中国社会各阶级的分析", language="zh", min_frequency=2, include_negative=True, page_size=50)
    keyword_finite = all(
        math.isfinite(value)
        for row in keywords.rows
        for value in (row.log_likelihood, row.chi_square, row.log_ratio)
    )
    keyword_sorted = all(
        keywords.rows[index].log_likelihood >= keywords.rows[index + 1].log_likelihood
        for index in range(len(keywords.rows) - 1)
    )
    add_check(checks, category="Keyword", name="关键词统计量有效", expected="全部有限", actual=keyword_finite, passed=keyword_finite)
    add_check(checks, category="Keyword", name="关键词排序有效", expected="LL 降序", actual=keyword_sorted, passed=keyword_sorted)

    benchmarks = [
        timed("KWIC 中文农民", lambda: kwic.search("农民", language="zh")),
        timed("KWIC 英文正则 peasant", lambda: kwic.search(r"\bpeasant\b", language="en", full_regex=True)),
        timed("中文词表", lambda: statistics_engine.word_list(language="zh", page_size=50)),
        timed("英文开放槽 N gram", lambda: statistics_engine.ngrams(language="en", n=3, open_slot=2, min_frequency=10, page_size=20)),
        timed("双语平行检索", lambda: parallel_engine.search(ParallelQuery(q="农民", search_side="zh", alignment_unit="sentence"))),
    ]
    passed = sum(check.passed for check in checks)
    return {
        "teacher_corpus_id": TEACHER_CORPUS_ID,
        "check_count": len(checks),
        "passed_count": passed,
        "failed_count": len(checks) - passed,
        "pass_rate": percentage(passed, len(checks)),
        "checks": [asdict(check) for check in checks],
        "benchmarks": benchmarks,
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible accuracy checks against the teacher-provided formal corpus.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_profile = profile_corpus(source_root, project_root)
    platform_accuracy = run_platform_accuracy(project_root)
    result = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "corpus_profile": corpus_profile,
        "platform_accuracy": platform_accuracy,
    }

    json_path = output_dir / "正式语料库准确度测试结果.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(
        output_dir / "平台准确度测试明细.csv",
        ["category", "name", "expected", "actual", "passed", "evidence"],
        platform_accuracy["checks"],
    )
    write_csv(
        output_dir / "四十组平行语料结构测试明细.csv",
        [
            "pair",
            "zh_sentences",
            "en_sentences",
            "common_sentence_ids",
            "pairable_sentence_capacity",
            "imported_sentence_pairs",
            "zh_paragraphs",
            "en_paragraphs",
            "common_paragraph_ids",
            "pairable_paragraph_capacity",
            "imported_paragraph_pairs",
            "warnings",
        ],
        corpus_profile["gold"]["alignment_rows"],
    )
    print(json_path)
    print(json.dumps({"profile": {k: corpus_profile[k] for k in ("scan_seconds", "decode_success_rate", "language_accuracy")}, "gold": {k: corpus_profile["gold"][k] for k in ("type_accuracy", "scanner_pair_accuracy", "import_success_rate", "sentence_alignment_retention", "paragraph_alignment_retention", "zh_pos_coverage", "en_pos_coverage")}, "platform": {k: platform_accuracy[k] for k in ("check_count", "passed_count", "failed_count", "pass_rate")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
