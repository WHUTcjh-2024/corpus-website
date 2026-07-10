from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


SUPPORTED_SUFFIXES = {".txt", ".tsv"}
UNKNOWN_ENCODING = "unknown"


@dataclass(frozen=True)
class ClassificationResult:
    detected_type: str
    detected_language: str
    encoding: str
    confidence: float
    title_guess: str = ""
    date_guess: str = ""
    author: str = ""
    stage_or_period: str = ""
    notes: list[str] = field(default_factory=list)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_XML_TAG_RE = re.compile(r"</?(head|p|s|date|author)(\s[^>]*)?>", re.IGNORECASE)
_ZH_POS_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+/[a-zA-Z]{1,8}\b")
_EN_POS_RE = re.compile(r"\b[A-Za-z][A-Za-z'’.-]*_[A-Z0-9$]{1,10}\b")
_DATE_RE = re.compile(
    r"(?P<ymd>\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?日?)|"
    r"(?P<cn>[一二三四五六七八九〇零]{4}年[一二三四五六七八九十〇零]{1,3}月(?:[一二三四五六七八九十〇零]{1,3}日)?)|"
    r"(?P<en>(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)
_STAGE_RE = re.compile(r"\bM\d+\b", re.IGNORECASE)

_AUTHORS = {
    "习近平": "习近平",
    "毛泽东": "毛泽东",
    "邓小平": "邓小平",
    "江泽民": "江泽民",
    "胡锦涛": "胡锦涛",
    "Xi Jinping": "Xi Jinping",
    "Mao Zedong": "Mao Zedong",
    "Deng Xiaoping": "Deng Xiaoping",
    "Jiang Zemin": "Jiang Zemin",
    "Hu Jintao": "Hu Jintao",
}


def classify_path(path: Path) -> ClassificationResult:
    data = path.read_bytes()
    text, encoding = decode_text(data)
    return classify_text(text, filename=path.name, encoding=encoding)


def classify_text(text: str, *, filename: str = "", encoding: str = "utf-8") -> ClassificationResult:
    if not text.strip():
        return ClassificationResult(
            detected_type="unknown",
            detected_language="unknown",
            encoding=encoding,
            confidence=0.1,
            notes=["empty_file"],
        )

    sample = text[:12000]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cjk_count = len(_CJK_RE.findall(sample))
    latin_count = len(_LATIN_RE.findall(sample))
    zh_pos_count = len(_ZH_POS_RE.findall(sample))
    en_pos_count = len(_EN_POS_RE.findall(sample))
    xml_tag_count = len(_XML_TAG_RE.findall(sample))
    tab_line_count = sum(1 for line in lines[:200] if "\t" in line)

    notes: list[str] = []
    if xml_tag_count:
        notes.append("xml_like_tags")

    detected_language = detect_language(cjk_count, latin_count)
    title_guess = guess_title(lines)
    date_guess = guess_date(filename, lines)
    author = guess_author(filename, lines[:8])
    stage_or_period = guess_stage(filename)

    if is_aligned_tsv(lines, tab_line_count):
        return ClassificationResult(
            detected_type="aligned_tsv",
            detected_language="zh_en",
            encoding=encoding,
            confidence=0.95,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes + ["tabular_parallel_text"],
        )

    if zh_pos_count >= 5 and zh_pos_count >= en_pos_count:
        return ClassificationResult(
            detected_type="tagged_zh",
            detected_language="zh",
            encoding=encoding,
            confidence=0.9,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes + [f"zh_pos_matches={zh_pos_count}"],
        )

    if en_pos_count >= 5:
        return ClassificationResult(
            detected_type="tagged_en",
            detected_language="en",
            encoding=encoding,
            confidence=0.9,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes + [f"en_pos_matches={en_pos_count}"],
        )

    if xml_tag_count >= 2:
        return ClassificationResult(
            detected_type="xml_like",
            detected_language=detected_language,
            encoding=encoding,
            confidence=0.82,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes,
        )

    if detected_language == "zh":
        return ClassificationResult(
            detected_type="raw_zh",
            detected_language="zh",
            encoding=encoding,
            confidence=0.78,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes,
        )

    if detected_language == "en":
        return ClassificationResult(
            detected_type="raw_en",
            detected_language="en",
            encoding=encoding,
            confidence=0.78,
            title_guess=title_guess,
            date_guess=date_guess,
            author=author,
            stage_or_period=stage_or_period,
            notes=notes,
        )

    return ClassificationResult(
        detected_type="unknown",
        detected_language="unknown",
        encoding=encoding,
        confidence=0.2,
        title_guess=title_guess,
        date_guess=date_guess,
        author=author,
        stage_or_period=stage_or_period,
        notes=notes + ["insufficient_language_signal"],
    )


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"

    candidates: list[tuple[float, str, str]] = []
    for encoding in ("utf-8-sig", "gb18030", "utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append((_readability_score(text), encoding, text))

    if not candidates:
        return data.decode("utf-8", errors="replace"), "utf-8-replace"

    _, encoding, text = max(candidates, key=lambda item: item[0])
    return text, encoding


def detect_language(cjk_count: int, latin_count: int) -> str:
    if cjk_count >= max(20, latin_count * 2):
        return "zh"
    if latin_count >= max(20, cjk_count * 2):
        return "en"
    if cjk_count and latin_count:
        return "mixed"
    return "unknown"


def is_aligned_tsv(lines: list[str], tab_line_count: int) -> bool:
    if not lines:
        return False
    first = lines[0].lower().replace(" ", "")
    if "\t" in lines[0] and (first in {"zh\ten", "cn\ten", "chinese\tenglish"} or ("zh" in first and "en" in first)):
        return True
    if tab_line_count < 2:
        return False
    checked = [line.split("\t", 1) for line in lines[:50] if "\t" in line]
    if len(checked) < 2:
        return False
    bilingual_rows = 0
    for left, right in checked:
        if _CJK_RE.search(left) and _LATIN_RE.search(right):
            bilingual_rows += 1
    return bilingual_rows >= max(2, len(checked) // 2)


def guess_title(lines: list[str]) -> str:
    for line in lines:
        cleaned = strip_inline_tags(strip_pos_marks(line)).strip()
        if cleaned and not _looks_like_date(cleaned) and len(cleaned) <= 120:
            return cleaned
    return ""


def guess_date(filename: str, lines: list[str]) -> str:
    source = "\n".join([filename, *lines[:6]])
    match = _DATE_RE.search(source)
    return match.group(0) if match else ""


def guess_stage(filename: str) -> str:
    match = _STAGE_RE.search(filename)
    return match.group(0).upper() if match else ""


def guess_author(filename: str, lines: list[str]) -> str:
    source = "\n".join([filename, *lines])
    for token, author in _AUTHORS.items():
        if token in source:
            return author
    return ""


def strip_inline_tags(value: str) -> str:
    return re.sub(r"</?[^>]+>", "", value)


def strip_pos_marks(value: str) -> str:
    value = re.sub(r"/[a-zA-Z]{1,8}\b", "", value)
    value = re.sub(r"_([A-Z$]{1,10})\b", "", value)
    return value


def _looks_like_date(value: str) -> bool:
    return bool(_DATE_RE.fullmatch(value.strip("（）() ")))


def _readability_score(text: str) -> float:
    if not text:
        return -1.0
    sample = text[:4000]
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
    controls = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\r\n\t")
    replacements = sample.count("\ufffd")
    letters = len(_LATIN_RE.findall(sample)) + len(_CJK_RE.findall(sample))
    common_spacing = sample.count(" ") + sample.count("\n")
    return printable / max(len(sample), 1) + letters * 0.001 + common_spacing * 0.0002 - controls * 0.02 - replacements * 0.5
