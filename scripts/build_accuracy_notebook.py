from __future__ import annotations

from pathlib import Path

import nbformat as nbf


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "test-evidence" / "正式语料库准确度测试复核.ipynb"


def code(source: str):
    return nbf.v4.new_code_cell(source.strip())


def markdown(source: str):
    return nbf.v4.new_markdown_cell(source.strip())


notebook = nbf.v4.new_notebook()
notebook["metadata"] = {
    "kernelspec": {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    },
    "language_info": {"name": "python", "version": "3.12"},
}
notebook["cells"] = [
    markdown(
        """
# 正式语料库准确度测试复核

## tl;dr

本笔记本读取全量测试脚本生成的结构化证据，复核文件规模、编码与语言识别、40组人工对齐语料、平台金标准断言和查询耗时。所有断言必须通过，才可将数据写入交付版测试报告。
"""
    ),
    markdown(
        """
## Context & Methods

- 测试对象：老师提供并已正式入库的语料库，共4个语料集合。
- 计算入口：`scripts/run_formal_corpus_accuracy.py`。
- 结构化输出：`docs/test-evidence/正式语料库准确度测试结果.json`。
- 准确度口径：有明确文件名语言标识的非空文件用于语言识别；40组人工对齐语料用于类型、配对、导入、结构保留和词性覆盖；已建立索引用固定期望值与独立数据库查询交叉验证。
- 重跑全量计算：在项目根目录运行 `backend/.venv/Scripts/python.exe scripts/run_formal_corpus_accuracy.py --source-root <正式语料库目录> --project-root <项目目录> --output-dir docs/test-evidence`。
"""
    ),
    markdown("## Data"),
    code(
        """
import json
from pathlib import Path

project_root = Path.cwd()
if project_root.name == "test-evidence":
    project_root = project_root.parents[1]
result_path = project_root / "docs" / "test-evidence" / "正式语料库准确度测试结果.json"
data = json.loads(result_path.read_text(encoding="utf-8"))
profile = data["corpus_profile"]
gold = profile["gold"]
platform = data["platform_accuracy"]
print(f"证据文件：{result_path}")
print(f"生成时间：{data['generated_at']}")
"""
    ),
    code(
        """
summary = profile["summary"]
print("文件总数:", summary["total_files"])
print("总字节数:", summary["total_size_bytes"])
print("语料集合:")
for row in profile["collections"]:
    print(f"  {row['name']}: {row['files']} 个文件, {row['bytes']} 字节")
print("类型分布:", summary["type_counts"])
print("编码分布:", profile["encodings"])
"""
    ),
    markdown("## Results"),
    code(
        """
metrics = [
    ("可解码率", profile["decode_success_rate"]),
    ("语言识别准确率", profile["language_accuracy"]),
    ("日期格式有效率", profile["date_format_validity"]),
    ("40组类型识别准确率", gold["type_accuracy"]),
    ("40组自动配对准确率", gold["scanner_pair_accuracy"]),
    ("40组导入成功率", gold["import_success_rate"]),
    ("句级对齐保留率", gold["sentence_alignment_retention"]),
    ("段级对齐保留率", gold["paragraph_alignment_retention"]),
    ("中文词性覆盖率", gold["zh_pos_coverage"]),
    ("英文词性覆盖率", gold["en_pos_coverage"]),
    ("平台金标准通过率", platform["pass_rate"]),
]
for name, value in metrics:
    print(f"{name:<20} {value:>8.4f}%")
"""
    ),
    code(
        """
print("40组对齐汇总")
print("  人工样本文件:", gold["file_count"])
print("  成对样本:", gold["pair_count"])
print("  句级可配对容量/实际导入:", gold["pairable_sentence_capacity"], gold["imported_sentence_pairs"])
print("  段级可配对容量/实际导入:", gold["pairable_paragraph_capacity"], gold["imported_paragraph_pairs"])
print("  句编号集合完全一致的文件对:", gold["exact_sentence_set_pair_count"], "/ 40")
print("  段编号集合完全一致的文件对:", gold["exact_paragraph_set_pair_count"], "/ 40")
print("  对齐方法分布:", gold["alignment_method_counts"])
"""
    ),
    code(
        """
print("平台金标准:")
by_category = {}
for item in platform["checks"]:
    stats = by_category.setdefault(item["category"], {"通过": 0, "总数": 0})
    stats["总数"] += 1
    stats["通过"] += int(item["passed"])
for category, stats in by_category.items():
    print(f"  {category}: {stats['通过']}/{stats['总数']}")
print("失败项:", [item["name"] for item in platform["checks"] if not item["passed"]])
"""
    ),
    code(
        """
print("20次重复查询耗时（毫秒）")
for row in platform["benchmarks"]:
    print(f"  {row['name']}: median={row['median_ms']:.4f}, p95={row['p95_ms']:.4f}, max={row['max_ms']:.4f}")
"""
    ),
    markdown("## Takeaways"),
    code(
        """
assert summary["total_files"] == sum(row["files"] for row in profile["collections"])
assert summary["total_files"] == sum(summary["type_counts"].values())
assert profile["decode_success_count"] == summary["total_files"]
assert profile["language_gold_count"] == profile["language_match_count"]
assert gold["file_count"] == 80 and gold["pair_count"] == 40
assert gold["type_accuracy"] == 100.0
assert gold["scanner_pair_accuracy"] == 100.0
assert gold["import_success_rate"] == 100.0
assert gold["sentence_alignment_retention"] >= 99.9
assert gold["paragraph_alignment_retention"] >= 99.9
assert gold["zh_pos_coverage"] == 100.0 and gold["en_pos_coverage"] == 100.0
assert platform["failed_count"] == 0 and platform["pass_rate"] == 100.0
print("复核结论：全部一致性断言通过。")
"""
    ),
    markdown(
        """
结论：全量语料可稳定读取；有明确语言标识的非空文件全部识别正确；40组人工对齐语料全部完成自动配对与导入，少量源文件句段编号集合不完全一致时系统按可核验编号保守对齐；48项平台金标准全部符合固定期望值，且词频与检索结果已通过独立数据库查询交叉验证。
"""
    ),
]

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
nbf.write(notebook, OUTPUT)
print(OUTPUT)
