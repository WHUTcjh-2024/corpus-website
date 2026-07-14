# 阶段 9：AntConc 统计工具与老师语料重建

> 历史快照：本文件记录阶段 9 前四项统计工具完成时的 88 项测试基线。Keyword、SVG Wordcloud、中文词级分词、索引 1.5 与当前阶段 10 的 114 项测试结果已合并到 `在线语料库平台-阶段0-10合并测试报告.md`，发布判断以合并报告为准。

## 交付结论

阶段 9 已完成：

- Word List：Rank、Word、Frequency、Per million；
- 语言、POS、词项过滤和 Frequency/Word 排序；
- N-Gram / Clusters：2–5 gram、最小频次、文本过滤和分页；
- Collocates：独立 L/R span、最小共现、POS、Frequency、MI、T-score、LogDice；
- Concordance Plot：按文档归一化为 100 个位置槽；
- 标注中文 KWIC 支持词级查询和首词 POS 条件；
- 默认排除标点，可显式选择包含标点；
- 统计、KWIC、ParaConc 复用相同后端权限；
- 运行环境旧测试语料已清除，只保留 4 个老师源语料样本。

## 与 AntConc / ParaConc 的功能对应

| 平台工具 | 对应语料分析用途 |
|---|---|
| KWIC | 中心词上下文、来源、L/R 排序、POS 限定 |
| Word List | 词频、排名、标准化频率、词性过滤 |
| N-Gram / Clusters | 2–5 Token 连续序列及最低频次 |
| Collocates | 节点词窗口搭配、MI/T-score/LogDice |
| Concordance Plot | 命中在文档中的归一化分布 |
| ParaConc | 老师人工句/段编号下的中英同步检索 |

平台不会把两侧语言重新自动对齐：无标注双语使用老师段落顺序；标注双语使用老师的 `p@n` 与 `s@n`。

## 统计口径

### Word List

```text
Per million = Frequency × 1,000,000 / 当前语言 Token 总数
```

英文按 `casefold` 统计，展示保留实际表层形式。标注语料默认排除 Unicode 标点/符号 Token；POS 条件是精确匹配。

### N-Gram

N-Gram 只在同一句内生成，不跨越句界。加工期把 2–5 gram 聚合到 SQLite `ngrams` 表，Web 请求只做过滤、排序与分页。

### Collocates

```text
Expected = Node Frequency × Collocate Corpus Frequency / Corpus Size
MI       = log2(Observed / Expected)
T-score  = (Observed - Expected) / sqrt(Observed)
LogDice  = 14 + log2(2 × Observed / (Node Frequency + Collocate Corpus Frequency))
```

左右窗口分别限制在 0–10，不能同时为 0。上下文不跨句，节点短语自身不计入搭配窗口。

### Concordance Plot

每个文档以 Token 起止位置归一化为 100 个槽，在 SQLite 中直接按槽聚合。因此即使命中很多，浏览器每个文档最多接收 100 个单元，不传输全量位置列表。

## 索引与代码

索引 schema：`1.3`。

```text
kwic_index.sqlite
├── tokens
│   ├── language / normalized / surface / pos
│   ├── document_id / sentence_id / sentence_position
│   └── is_punctuation
├── parallel_pairs
└── ngrams
    ├── language / n / normalized / display / frequency
    └── contains_punctuation
```

关键代码：

```text
backend/apps/statistics/engine.py
backend/apps/statistics/forms.py
backend/apps/statistics/views.py
backend/templates/statistics/
backend/apps/processing/artifacts.py
backend/apps/search/kwic.py
backend/apps/corpora/management/commands/register_teacher_samples.py
```

## 精选老师语料

运行环境原有 8 个测试/demo/测试上传语料已从数据库和运行时加工目录清除。自动化 fixtures 保留，它们不是平台展示语料。

当前只登记以下 4 个只读老师语料：

| Corpus ID | 类型 | 文章 | 加工规模 |
|---|---|---|---:|
| `36b657e1-2362-42d6-8908-6d88b7c53083` | 中文单语 | 中国社会各阶级的分析 | 3,338 Token |
| `b06b8ecf-2e1b-4507-9c8c-3e26f87d1fda` | 英文单语 | 人民对美好生活的向往就是我们的奋斗目标 | 847 Token |
| `b26adfb1-c475-4e14-84d9-17dd43b4c9e1` | 无标注双语段对齐 | 人民对美好生活的向往就是我们的奋斗目标 | 11 段对 |
| `71d92f26-c5e5-485f-ac83-3ebccb6a9acc` | 带 POS 双语句/段对齐 | 湖南农民运动考察报告 | 693 句对、98 段对、27,566 Token |

可重复重建：

```powershell
cd backend
.\.venv\Scripts\python manage.py register_teacher_samples `
  --source-root D:\Desktop\CONC\test_conc `
  --process
```

命令按唯一文件名查找；零个或多个匹配都会失败，避免选错老师文件。

## 真实联调

标注双语《湖南农民运动考察报告》：

```text
中文索引 Tokens: 12,050
默认排除标点后的 Word List Tokens: 9,920
中文 Types: 2,348
“农民” Frequency: 259
“农民协会” 2-gram Frequency: 49
“农民”的高 LogDice 搭配: 协会、的、是、在、运动
Concordance Plot “农民”: 259 hits / 1 document
本地四项统计连续查询: 约 0.04 秒（热缓存开发机，仅作联调记录）
```

英文单语：847 Tokens、356 Types。

登录后的真实 HTTP 联调全部返回 200：语料库列表、Word List、N-Gram、Collocates、Concordance Plot、POS-KWIC、无标注 ParaConc、标注 ParaConc。列表只出现 4 个“老师语料”，旧“阶段4真实样本”和测试上传语料均不存在。

## 自动测试

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q
```

结果：

```text
88 passed in 14.29s
```

新增覆盖：

- Word List 词频、排名、标准化频率和 POS；
- N-Gram 句界、频次、过滤与标点排除；
- Collocates 窗口、POS、频次及三种关联指标；
- Concordance Plot 100 槽聚合；
- 标注中文词级 KWIC 和 POS；
- 统计页面权限 403；
- 统计页真实渲染；
- 阶段 0–8 全量回归。

## 上线约束

- 老师源文件只读，Git 与 Web 静态目录均不暴露全文；
- Web 只读打开 SQLite 索引；
- 旧 schema 缺少 `ngrams/is_punctuation` 时返回 409，要求离线重建；
- 所有 SQL 参数化，动态字段来自固定白名单；
- 大规模基准仍需在完整语料量级执行 P50/P95/P99 和并发测试。
