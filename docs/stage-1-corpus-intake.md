# 阶段 1：语料自动分类与 manifest 生成

## 范围

本阶段只实现 `corpus_intake` 模块，不实现检索、用户上传、KWIC 或 UI 美化。

已实现：

- `apps/corpus_intake/classifiers.py`：语料内容分类。
- `apps/corpus_intake/scanner.py`：目录扫描、记录生成、疑似中英配对识别。
- `apps/corpus_intake/manifest.py`：CSV/JSON manifest 写入。
- `scan_corpus_inbox` 管理命令。
- 极小 fixtures 和 expected 测试数据。

## 支持类型

- `raw_zh`
- `raw_en`
- `aligned_tsv`
- `paired_raw_zh_en`
- `tagged_zh`
- `tagged_en`
- `xml_like`
- `unknown`

## 命令

默认扫描：

```powershell
cd backend
.\.venv\Scripts\python manage.py scan_corpus_inbox
```

指定输入和输出目录：

```powershell
.\.venv\Scripts\python manage.py scan_corpus_inbox --inbox D:\Desktop\CONC\test_conc --output-dir D:\Desktop\CONC\corpus-platform\data\manifests
```

## 测试结果摘要

- `python manage.py check`：通过。
- `pytest`：13 项通过。
- fixtures 扫描：9 个文件，8 类目标类型均覆盖，1 个 unknown，1 组疑似配对。
- 老师测试语料库扫描：4370 个文件，总大小 52702144 bytes，941 组疑似配对，1 个 unknown。

## 数据保护

扫描过程只读输入目录，不删除、不改名、不移动原始文件。`data/manifests/` 已被 `.gitignore` 忽略，生成的 manifest 不进入 Git。
