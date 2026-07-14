# 在线语料库平台

本项目按模块逐步开发一个本地优先的在线语料库平台。当前完成范围：

- 阶段 0：Django、PostgreSQL、Redis、Celery 与 Docker 本地骨架。
- 阶段 1：语料自动分类与 manifest 生成。
- 阶段 2：用户申请、审核、角色与后端权限控制。
- 阶段 3：语料库登记、可见性控制与 Corpus Documentation。
- 阶段 4：语料加工流水线、统一文件输出与 Celery 异步入口。
- 阶段 5：中文、英文与短语 KWIC 检索、上下文窗口、分页和来源定位。
- 阶段 6：KWIC L1/L2/L3/R1/R2/R3 全结果集排序与稳定分页。
- 阶段 7：ParaConc 中英双向检索、双语 AND/NOT、对齐分页、双侧译词高亮和受控导出。
- 阶段 8：单语/人工对齐双语 TXT 上传、配额、单用户任务队列、状态、重试、安全删除与 AntConc/ParaConc 接入。
- 阶段 9：AntConc Word List、N-Gram/Clusters、Collocates、Keyword、Concordance Plot、Wordcloud 与 POS 条件。
- 阶段 10：CQPweb 风格安全查询子集、word/POS/lemma 属性、通配符、字符函数与 KWIC 复用。
- 阶段 11：数据库审计、教师语料动态水印、Celery 受控导出、下载限次/过期、上传配额审批和外部恶意文件扫描接口。

当前尚未实现阶段 12 的最终生产发布验收。

## 本地 Docker 启动

在项目根目录执行：

```powershell
docker compose -f docker-compose.local.yml up -d db redis
docker compose -f docker-compose.local.yml build web worker
docker compose -f docker-compose.local.yml run --rm web python manage.py migrate
docker compose -f docker-compose.local.yml up -d web worker
```

访问地址：

- 平台首页：`http://localhost:8010/`
- 用户申请：`http://localhost:8010/accounts/apply/`
- 用户登录：`http://localhost:8010/accounts/login/`
- 语料库列表：`http://localhost:8010/corpora/`
- 管理后台：`http://localhost:8010/admin/`
- 存活检查：`http://localhost:8010/healthz`
- 就绪检查：`http://localhost:8010/readyz`

停止服务：

```powershell
docker compose -f docker-compose.local.yml down
```

## 本地 Python 环境

项目只使用 PostgreSQL，不使用 SQLite。先启动 PostgreSQL 和 Redis：

```powershell
docker compose -f docker-compose.local.yml up -d db redis
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\pytest
```

启动开发服务：

```powershell
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8010
```

## 阶段 1：生成 manifest

默认扫描 `data/inbox/`，输出到 `data/manifests/`：

```powershell
cd backend
.\.venv\Scripts\python manage.py scan_corpus_inbox
```

扫描老师提供的测试语料库：

```powershell
.\.venv\Scripts\python manage.py scan_corpus_inbox --inbox D:\Desktop\CONC\test_conc --output-dir D:\Desktop\CONC\corpus-platform\data\manifests
```

生成文件为 `corpus_manifest.csv` 和 `corpus_manifest.json`。扫描只读原始文件，不删除、不改名、不移动语料。

## 阶段 2：用户申请与审核

角色包括 `test`、`junior`、`middle`、`advanced` 和 `admin`。申请状态包括 `pending`、`approved`、`rejected` 和 `disabled`。

创建或更新验收账号时必须显式提供密码：

```powershell
cd backend
.\.venv\Scripts\python manage.py seed_accounts --test-password "请替换为测试密码" --admin-password "请替换为管理员密码"
```

也可以使用环境变量 `SEED_TEST_USER_PASSWORD` 和 `SEED_ADMIN_PASSWORD`。命令可重复执行：

- `test_user`：已审核，角色为 `test`，只允许访问 demo 范围。
- `admin`：已审核，具有 Django 管理后台权限。

正式用户通过 `/accounts/apply/` 提交姓名、单位、邮箱、申请等级、使用目的和申请理由。管理员在 `/admin/` 的“用户申请与权限”中审核、拒绝、调整等级或停用账号。所有工作台权限均在后端校验。

## 阶段 3：语料库登记与 Documentation

管理员可以在 Django 管理后台维护语料库，也可以从阶段 1 的 JSON manifest 选择记录进行登记：

```powershell
cd backend
.\.venv\Scripts\python manage.py register_manifest_corpus `
  --manifest D:\Desktop\CONC\corpus-platform\data\manifests\corpus_manifest.json `
  --file-id "manifest中的file_id" `
  --source-type demo `
  --access-level demo
```

教师语料可使用 `teacher` 来源及 `junior`、`middle`、`advanced` 访问等级。命令只读取 manifest 元数据，不复制语料全文。

已审核的普通用户可以在 `/corpora/create/` 登记个人语料库条目。本阶段不上传文件，也不启动加工任务。个人条目默认为私有，只对所有者和管理员可见。

语料库状态包括：

- `created`
- `pending_processing`
- `processing`
- `ready`
- `failed`
- `disabled`

Corpus Documentation 页面显示来源、类型、语言、访问等级、状态、阶段以及文件、文档、段落、句子、Token、Type 等统计字段。阶段 4 加工成功后会写入真实统计。

## 阶段 4：语料加工流水线

阶段 4 只实现处理器、文件型输出、任务状态和管理命令，不实现检索页面。支持：

- `RawMonoTxtImporter`：中文或英文原文的分段、分句和词级分词；中文固定为 Jieba 0.42.1，英文使用正则 tokenizer。
- `AlignedTsvImporter`：解析每行 `zh<TAB>en` 的双语句对。
- `TaggedCorpusImporter`：解析中文 `word/POS` 和英文 `token_POS`。
- `XmlLikeImporter`：使用 XML 解析器读取 `<head>`、`<p>`、`<s>` 结构。
- `PairedParagraphImporter`：保留老师提供的中英人工段落顺序；两侧段落数不一致时直接拒绝加工。段内仍分别分句、分词供 KWIC 使用，但不再据此重建对齐。
- `PairedTaggedStructureImporter`：解析老师提供的 `<p n>`、`<s n>` 与中英文 POS 标注，按人工编号同时生成句对和段落对；跨语言编号不一致时拒绝加工。

从 manifest 登记语料时会同步创建 `CorpusFile` 元数据。也可以为已有语料添加只读源文件：

```powershell
cd backend
.\.venv\Scripts\python manage.py add_corpus_file `
  --corpus-id "语料库UUID" `
  --path "D:\path\source.txt" `
  --detected-type raw_zh `
  --language zh `
  --encoding utf-8
```

默认把加工任务发送到 Celery：

```powershell
.\.venv\Scripts\python manage.py process_corpus --corpus-id "语料库UUID"
```

本地验收可同步执行：

```powershell
.\.venv\Scripts\python manage.py process_corpus --corpus-id "语料库UUID" --sync
```

加工结果保存在 `data/processed/<corpus_id>/`：

- `meta.json`
- `documents.jsonl`
- `paragraphs.jsonl`
- `sentences.jsonl`
- `tokens.jsonl`
- `parallel_pairs.jsonl`
- `documentation.json`
- `processing_report.json`

文件型索引保存在 `data/indexes/<corpus_id>/`。`kwic_index.sqlite` 保存 Token、平行对、2–5 gram、词项总频次和 POS 频次；搭配和分布图从 SQLite 动态聚合，词云从物化词频生成 SVG。PostgreSQL 不保存全文或全量 token。

任务状态为 `pending`、`running`、`success`、`failed`。同一语料库不能同时存在两个活动任务；失败原因写入 `ProcessingTask.error_message`，源文件始终只读。

## 阶段 5：KWIC 检索

状态为 `ready` 的语料库会在列表页和 Corpus Documentation 页面显示 KWIC 入口：

```text
http://localhost:8010/search/<corpus_id>/kwic/
```

支持中文、英文和同句短语检索。默认左右各显示 5 个 token，每页 50 条，单页最多 100 条；结果显示来源文件、段落号和句号。查询只读访问阶段 4 生成的 SQLite 索引，索引缺失时不会回退到原文扫描。

专项测试：

```powershell
cd backend
.\.venv\Scripts\pytest apps\search\tests.py
```

详细原理与验收见 `docs/在线语料库平台-12课学习指南.md`。

## 阶段 6：L/R 排序

KWIC 页面可以按 `L1`、`L2`、`L3`、`R1`、`R2`、`R3` 排序。排序作用于完整命中集，之后再分页；空上下文排在最后，英文忽略大小写，相同键按原始位置稳定排序。

```powershell
cd backend
.\.venv\Scripts\pytest apps\search\tests.py
```

阶段 5–6 搜索模块共 17 项测试；阶段 0–6 全量回归共 65 项测试。当前实现说明已合并到 `docs/在线语料库平台-12课学习指南.md`。

## 阶段 7：ParaConc 与测试账号上传

中英平行语料会显示 `ParaConc` 入口：

```text
http://localhost:8010/parallel/<corpus_id>/
```

支持中文→英文、英文→中文、双语包含/排除条件、句/段对齐单元、稳定分页及高亮。本人平行语料可以流式导出 TSV；教师和 demo 语料禁止导出。

老师语料按真实来源分为两类：无标注成对文本保留人工段落顺序；带结构/POS 标注的成对文本使用 `p@n`、`s@n` 人工编号，支持句子和段落两种 ParaConc 视图。标签不会作为正文展示，POS 会保留在 Token 索引中。

测试账号可以从 `/corpora/upload/` 上传少量私有 `.txt`：单文件 2 MB、账号总额 5 MB。普通账号默认单文件和总额均为 30 MB。文件使用随机磁盘名保存到 `data/user_uploads/`，上传后自动进入 Celery 加工。

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q
```

阶段 7 完成时全量回归为 82 项。详细实现和联调证据见 `docs/stage-7-paraconc-and-test-upload.md`，课程总览见 `docs/在线语料库平台-12课学习指南.md`。

## 阶段 8：用户上传与加工生命周期

上传页支持单语原始 TXT、中英人工段落对齐双 TXT、中英编号/POS 人工对齐双 TXT。单语加工完成后进入 AntConc 工具；双语按老师或用户提供的既有对齐关系进入 ParaConc，不执行自动重排。

`/corpora/mine/` 显示个人配额、任务状态和进度。失败任务可以重试；非活动任务可以由所有者删除，并在数据库提交后清理源文件、processed、indexes 和 exports。每个账号同时只允许一个活动加工任务，数据库包含对应部分唯一约束。

阶段 8 完成后的全量回归为 97 项。实现、真实 HTTP/Celery 联调和人工验收步骤见 `docs/stage-8-user-upload-lifecycle.md`。

## 阶段 9：AntConc 统计工具

状态为 `ready` 的语料显示 `AntConc Tools` 入口，包含：

- Word List：Frequency、Per million、语言、POS、过滤；
- N-Gram / Clusters：2–5 gram、最低频次、分页；
- Collocates：L/R span、POS、MI、T-score、LogDice；
- Keyword：目标/参照 Frequency、Range、Per million、Log-Likelihood、Chi-square、Log Ratio 与负关键词；
- Concordance Plot：每文档 100 槽归一化分布；
- Wordcloud：物化词频、停用词、最小频次、TopN、三套主题与 SVG 防重叠螺旋布局；
- KWIC 首词 POS 条件。

默认排除标点，可在页面选择包含。Word List、Keyword 和 Wordcloud 读取索引 1.5 的 `word_totals/word_frequencies` 物化表；未标注中文使用 Jieba 词级分词，老师 POS 语料沿用源文件词项；Keyword 只允许所选语言分词口径一致的目标/参照组合。

## 阶段 10：CQPweb 风格复杂查询

KWIC 页面新增“普通 KWIC / CQP 子集”模式和显式语言选择。安全子集支持：

- 精确词与连续短语；
- `*`、`?` 通配符；
- `starts_with`、`ends_with`、`contains`；
- `[word="..."]`、`[pos="..."]`、`[lemma="..."]`；
- 连续属性条件、L/R 排序与分页。

解析、过滤条件编译和执行分别位于 `query_parser.py`、`filters.py`、`query_engine.py`。属性/函数/SQL alias 均使用白名单，查询值参数化；非法语法在表单中显示中文错误。复杂查询继续复用 KWIC 上下文、来源和权限边界。完整语法、老师语料联调与差异说明见 `docs/在线语料库平台-阶段0-10合并测试报告.md`。当前全量回归为 114 项。

## 阶段 11：安全、审计、水印与受控导出

登录成功/失败、退出、检索、统计、上传、重试、删除、扩容、导出和后台操作写入数据库审计事件。教师语料页面按“用户、语料、分钟”生成带签名追踪码的重复动态水印；demo 和用户私有语料不加教师水印。

本人 `ready` 私有语料可以提交 KWIC 或 ParaConc 后台导出，Celery 将 UTF-8 BOM TSV 原子写入 `data/exports/<corpus>/<user>/`。任务限制单用户并发、每小时创建次数、最大结果行数、有效期和下载次数；创建与下载均重新校验所有者，教师/demo 语料始终禁止批量导出。过期清理命令：

```powershell
cd backend
.\.venv\Scripts\python manage.py expire_exports
```

普通用户可提交上传扩容申请，由后台管理员批准或拒绝。上传在落盘前调用可替换扫描器；本地默认显式禁用，生产配置若未启用外部扫描器会拒绝启动。ClamAV 后端为 `apps.corpora.scanners.ClamAVUploadScanner`。

老师样本可按高级教师语料登记并同步加工：

```powershell
.\.venv\Scripts\python manage.py register_teacher_samples `
  --source-root D:\Desktop\CONC\test_conc `
  --source-type teacher `
  --access-level advanced `
  --process
```

当前全量回归为 134 项。实现和真实 Celery/HTTP 联调证据见 `docs/stage-11-security-audit-exports.md`。

## 权限规则

- 未登录用户不能进入工作台或语料库页面。
- `pending`、`rejected`、`disabled` 用户不能进入工作台。
- `test_user` 可以看到 demo 和本人小额上传，不能登记空语料条目，也不能查看教师语料或他人上传。
- `junior` 看不到 `middle` 或 `advanced` 教师语料库。
- `middle` 看不到 `advanced` 教师语料库。
- 用户只能看到自己的个人语料库，不能访问其他用户的 Documentation。
- 管理员可以查看全部语料库，包括停用语料库。

## 自动测试

```powershell
docker compose -f docker-compose.local.yml up -d db redis
cd backend
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py migrate --check
.\.venv\Scripts\pytest
```

Docker 配置检查：

```powershell
docker compose -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.prod.yml config --quiet
```

## 阶段 0-10 人工验收

1. 启动 PostgreSQL、Redis、Django 和 Celery，确认首页、`/healthz`、`/readyz` 可访问。
2. 运行 `scan_corpus_inbox`，核对 manifest 数量，并确认原始语料未被修改。
3. 运行 `seed_accounts`，使用 `test_user` 登录，确认可看到 demo 和本人上传，但看不到教师语料及他人上传。
4. 在 `/accounts/apply/` 提交普通账号申请，确认待审核账号不能登录工作台。
5. 使用管理员账号在 `/admin/` 审核用户并调整角色，确认通过后可以登录；停用后不能登录。
6. 创建 junior、middle、advanced 教师语料，分别登录对应角色，确认等级过滤生效。
7. 使用用户 A、用户 B 分别登记个人语料，确认双方不能查看对方条目或 Documentation。
8. 使用管理员账号确认可以查看全部用户申请和全部语料库。
9. 使用阶段 4 fixtures 或小样本登记 raw_zh、raw_en、aligned_tsv、tagged_zh、tagged_en、xml_like、paired_raw_zh_en 语料。
10. 运行 `process_corpus --sync`，核对 processed 的 8 个标准文件和 indexes 的 7 个标准文件。
11. 打开 Corpus Documentation，核对文件、文档、段落、句子、Token、Type 已更新为真实统计。
12. 故意登记格式错误的 TSV，确认任务、语料和文件状态变为 failed，且 `error_message` 非空。
13. 对比加工前后源文件字节和修改时间，确认原始语料未改变。
14. 在 KWIC 页面分别检索中文、英文和英文短语，核对命中数、上下文、来源文件、段落号和句号。
15. 把 KWIC 每页数量设为 1，确认分页不改变总命中数；使用无权限账号访问相同 URL，确认返回 403。
16. 依次选择 L1/L2/L3/R1/R2/R3，确认空值排后、英文忽略大小写、中文排序正常，分页不改变总命中数。
17. 在 ParaConc 中分别做中文→英文、英文→中文和双语 AND/NOT 检索，确认对齐顺序、高亮及分页正确。
18. 用 `test_user` 上传一个小型 `.txt`，等待加工成功并用 KWIC 检索；上传超过 2 MB 的文件应被拒绝。
19. 尝试导出 demo 平行语料应返回 403；本人平行语料可以导出 TSV。
20. 运行全量 `pytest`，确认阶段 0-7 回归测试全部通过。
21. 打开 `AntConc Tools`，核对 Word List、2–5 gram、Collocates 和 Concordance Plot。
22. 在标注语料中使用 POS 条件，确认正文不显示标注符号、索引仍能按词性过滤。
23. 在 KWIC 选择 CQP 子集，分别查询 `资产*`、`contains(阶级)` 和 `[pos="NN1"]`。
24. 输入未闭合引号、未知属性和纯通配符，确认显示中文错误且没有 500。
25. 将复杂查询每页设为 1 并使用 R1 排序，确认翻页保留表达式、模式和语言。
26. 登录高级账号打开教师 KWIC、AntConc 和 ParaConc 页面，确认水印包含当前用户名、分钟时间和追踪码；demo 页面没有教师水印。
27. 对本人私有语料提交后台导出，确认状态从 pending/running 变为 success，并能在“我的导出”下载；超过下载次数或有效期后应拒绝。
28. 对教师和 demo 语料提交 KWIC/ParaConc 导出，确认后端返回 403，且没有创建导出文件。
29. 提交上传扩容申请，确认普通用户不能自行批准，后台管理员批准后新配额生效，申请与审批均有审计记录。
30. 将测试扫描器配置为拒绝，确认上传事务回滚且临时文件被清理；生产配置使用禁用扫描器时应启动失败。

## 数据保护

以下内容不得进入 Git：老师完整语料、用户上传文件、加工结果、索引、manifest、导出结果和 Word 测试报告。相关目录已由 `.gitignore` 忽略，只保留必要的 `.gitkeep`：

- `test_conc/`
- `data/inbox/`
- `data/dev_sample/`
- `data/teacher_private/`
- `data/user_uploads/`
- `data/processed/`
- `data/indexes/`
- `data/exports/`
- `data/manifests/`
- `*.docx`

## 测试报告

当前阶段报告：

`docs/stage-11-security-audit-exports.md`

阶段 0–10 合并报告与阶段 7、8、9 独立报告保留为历史记录；当前发布判断以阶段 11 报告为准。
