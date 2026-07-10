# 阶段 4：语料加工流水线

## 范围

本阶段只实现处理器、管理命令、Celery 任务入口、文件型标准输出和数据库任务状态，不实现检索页面、KWIC 查询引擎或后续统计功能。

## 分层

- `contracts.py`：稳定的文档、段落、句子、token、句对记录契约。
- `importers/`：五类无 ORM 依赖的输入解析器。
- `artifacts.py`：流式写 JSONL、SQLite 索引和原子目录发布。
- `services.py`：源文件路径策略、任务状态机、加工编排和 Documentation 更新。
- `tasks.py`：Celery 异步入口。
- `process_corpus`：默认异步、可用 `--sync` 本地验收的管理命令。

Importer 不读写数据库，管理命令不包含业务逻辑，PostgreSQL 不保存全文和全量 token。

## 模型

`CorpusFile` 保存原始文件名、存储路径、检测类型、语言、大小、编码、SHA-256、状态和错误信息。

`ProcessingTask` 保存任务类型、pending/running/success/failed 状态、进度、错误、输出路径及开始/结束时间。PostgreSQL 条件唯一约束保证同一 corpus 最多只有一个活动任务。

## 输出

`data/processed/<corpus_id>/` 包含：

- `meta.json`
- `documents.jsonl`
- `paragraphs.jsonl`
- `sentences.jsonl`
- `tokens.jsonl`
- `parallel_pairs.jsonl`
- `documentation.json`
- `processing_report.json`

`data/indexes/<corpus_id>/` 包含：

- `kwic_index.sqlite`
- `token_position_index`
- `word_frequency.json`
- `ngram_frequency.json`
- `collocate_cache.json`
- `concordance_plot.json`
- `wordcloud_terms.json`

本阶段只构建 token 位置基础索引和词频；后四类统计文件使用明确的 `deferred` 状态，不伪造分析结果。

## 失败与数据保护

所有源文件以只读方式打开。用户语料源路径必须位于 `DATA_ROOT/user_uploads`；教师和 demo 语料可由管理员登记外部只读绝对路径。

写入先进入按 task 隔离的 staging 目录，processed 和 indexes 两个结果目录通过备份、目录替换和失败回滚发布。加工异常会写入 `ProcessingTask.error_message`、`CorpusFile.error_message`，并将 Corpus 标记为 failed。

## 自动测试

阶段 4 fixtures 覆盖中文原文、英文原文、TSV、中文 POS、英文 POS、XML-like、中英原文配对和错误 TSV。

```powershell
cd backend
.\.venv\Scripts\pytest apps\processing\tests.py
```

验收点包括句子数、token 数、句对数、token/POS、结构标签、基础自动对齐、标准 artifact 集合、SQLite 索引、Documentation 更新、失败错误、源文件不变、活动任务约束、Celery task 入口和同步管理命令。

## 人工验收

1. 用 `add_corpus_file` 或 manifest 为 corpus 登记只读小样本。
2. 使用 `process_corpus --sync` 执行本地加工。
3. 核对任务状态为 success、进度为 100，Corpus 状态为 ready。
4. 核对 processed 8 个文件和 indexes 7 个文件完整。
5. 使用 SQLite 打开 `kwic_index.sqlite`，核对 token 行数与 Documentation 一致。
6. 使用错误 TSV 重复测试，确认失败状态和 error_message。
7. 对比源文件字节、大小和修改时间，确认没有被修改。
