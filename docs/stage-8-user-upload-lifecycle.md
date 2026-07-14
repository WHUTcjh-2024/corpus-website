# 阶段 8：用户上传、加工生命周期与 AntConc / ParaConc 接入

## 交付结论

阶段 8 已完成。用户私有语料从“只能上传一个单语文件”扩展为完整生命周期：

- 单语中文/英文 TXT 上传，完成后进入 KWIC 与 AntConc Tools；
- 人工段落对齐的中英双 TXT 上传，按既有段落顺序进入 ParaConc；
- 带 `p@n`、`s@n` 和 POS 的中英双 TXT 上传，按人工编号生成句/段两种 ParaConc 视图，同时保留 AntConc 的词级/POS 分析；
- 上传任务状态、进度轮询、失败原因、失败重试；
- “我的语料”列表、配额使用量和加工状态；
- 所有者安全删除，并清理源文件、processed、indexes、exports；
- 测试账号保留小额上传权限：单文件 2 MB、总额 5 MB；正式账号默认 30 MB；
- 每个账号同时最多一个 pending/running 加工任务，应用检查与数据库约束双重防线；
- Corpus Documentation 展示前 5 条人工对齐预览，不重新自动对齐。

开发边界参考 [AntConc 4.4.1 官方手册](https://www.laurenceanthony.net/software/antconc/releases/AntConc441/docs/help.pdf) 与 [ParaConc 官方说明](https://paraconc.com/)。平台复现的是可验证的分析工作流，不复制桌面软件界面。

## 数据契约

### 单语原始文本

```text
upload_mode = monolingual
language = zh | en
source_file = one .txt
```

加工成功后生成统一 Token/句子/段落索引，可进入 KWIC、Word List、N-Gram、Collocates 和 Concordance Plot。

### 人工段落对齐双语

```text
upload_mode = paired_raw
zh_file = one .txt
en_file = one .txt
```

空行划分的段落顺序是权威对齐契约。中英文段落数不一致时，Celery 任务明确失败；平台不会把两侧各自分句后的第 N 句强行配对。

### 人工编号/POS 对齐双语

```text
upload_mode = paired_tagged
zh_file = one .txt
en_file = one .txt
```

中英文 `p@n`、`s@n` 必须逐项一致。正文展示会去除 `/n`、`_NN1` 等标注，POS 仍进入 Token 索引。编号不一致时任务失败并显示具体段落或句子错误。

## 生命周期

```text
上传校验
  → 原子保存到 user_uploads/<user>/<corpus>/
  → 创建私有 Corpus / CorpusFile
  → 创建 ProcessingTask
  → Celery pending → running → success | failed
  → ready 后进入 AntConc / ParaConc
  → failed 可重试
  → 非活动任务可安全删除
```

状态接口：

```text
GET /corpora/<corpus_id>/status/
```

返回 Corpus 状态、阶段、最新任务状态、进度和错误信息。Documentation 在 pending/running 状态下每 2 秒轮询，终态后刷新，不在 Web 请求中执行加工。

## 上线级约束

### 上传安全

- 仅接受 `.txt`，服务层再次校验，不信任前端 `accept`；
- 拒绝空文件、超限文件和伪装成 TXT 的高控制字节内容；
- 原文件名只作元数据，磁盘使用 UUID 随机名；
- 临时文件采用独占创建、`fsync` 与 `os.replace` 原子替换；
- 数据库行锁串行化同一账号的配额检查；
- 写入后按实际字节数复核配额；
- 用户源文件只能位于 `DATA_ROOT/user_uploads`；
- 上传失败时回滚数据库并清理临时/已落盘文件。

### 权限与并发

- 用户语料固定为 `source_type=user`、`access_level=private`；
- 列表、Documentation、状态接口均复用后端 `visible_corpora_for()`；
- 重试和删除在服务层再次校验所有者；
- 重试/删除只接受 POST，并受 Django CSRF 保护；
- pending/running 任务期间禁止删除，避免与 Worker 竞争文件；
- `one_active_processing_task_per_user` 是 PostgreSQL 部分唯一约束，不只依赖页面按钮。

### 删除语义

数据库事务成功后通过 `transaction.on_commit` 清理：

```text
data/user_uploads/<owner_id>/<corpus_id>/
data/processed/<corpus_id>/
data/indexes/<corpus_id>/
data/exports/<corpus_id>/
```

每个目标都先解析绝对路径并验证仍位于对应根目录，禁止删除根目录本身。

## 与 AntConc / ParaConc 的对应和差异

| 工作流 | 当前平台 | 差异说明 |
|---|---|---|
| AntConc 加载单语语料 | 上传后异步建立索引，再进入各分析页 | 不在每次页面请求扫描原文；适合多人在线服务 |
| AntConc 结果工具 | KWIC、Word List、N-Gram、Collocates、Plot、POS | 复用服务端只读 SQLite 索引与统一权限 |
| ParaConc 平行文本 | 中英并排、双向检索、AND/NOT、句/段视图 | 对齐以人工顺序/编号为准，不猜测、不自动重排 |
| 对齐检查 | 加工失败写入具体错误，ready 后展示预览 | 当前不是交互式人工编辑器；修正应回到源文件后重传 |

## 自动测试

命令：

```powershell
cd backend
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py makemigrations --check --dry-run
.\.venv\Scripts\python -m pytest -q
```

结果（2026-07-12）：

```text
System check identified no issues (0 silenced).
No changes detected
97 passed in 16.58s
```

新增覆盖包括：

- 单语、人工段落对齐、人工编号/POS 对齐上传；
- 双文件语言标记、文件数和 ParaConc 索引结果；
- 二进制内容伪装 TXT；
- 单用户单活动任务；
- 状态 JSON、失败重试；
- 所有者删除及四类运行时目录清理；
- 非所有者不能查看状态、重试或删除；
- 对齐预览的数量上限、稳定顺序和页面渲染；
- 阶段 0–9 全量回归。

## 真实 HTTP / Celery 联调

联调账号：`test_user`。联调文件直接选自老师提供的：

```text
D:\Desktop\CONC\test_conc\...\习近平-中文-1-5-段对齐\
1-习近平1-中1-2012-11-15-人民对美好生活的向往就是我们的奋斗目标.txt

D:\Desktop\CONC\test_conc\...\习近平-官译-1-5-段对齐\
1-习近平1-官译1-2012-11-15-人民对美好生活的向往就是我们的奋斗目标.txt
```

联调结果：

```text
登录：302 → /accounts/dashboard/
双语上传：302 → /corpora/<id>/documentation/
Celery：pending → running → success
Corpus：ready
Progress：100
ParaConc 查询“人民”：HTTP 200，页面显示对应英文
删除：HTTP 302，数据库与四类运行时目录均清理
```

联调产生的用户语料已删除。运行环境最终仍只有 4 个老师样本，没有残留测试上传。

## 人工验收步骤

1. 登录 `test_user`，打开 `/corpora/upload/`。
2. 选择“单语原始 TXT”，上传中文或英文，观察 Documentation 进度自动更新到 100%。
3. 打开 KWIC 与 AntConc Tools，验证检索、词频和统计入口。
4. 再上传老师提供的一组“中英段落人工对齐 TXT”。
5. 加工完成后在 Documentation 检查前 5 条中英对齐预览。
6. 打开 ParaConc，分别执行中文→英文、英文→中文、双语包含和排除查询。
7. 人为上传段落数不一致的双文件，确认任务失败并显示错误；这类源数据错误应删除后上传修正版，“重试加工”只用于源文件未变的临时加工故障。
8. 对已结束的个人语料执行删除，确认返回“我的语料”且配额释放。
9. 用另一账号访问该 corpus 的 Documentation、status、retry、delete，确认 404 或 403。

## 后续边界

阶段 8 已达到开发手册“上传、排队、ready、权限隔离”的进入下一阶段条件。以下内容按手册属于后续安全/审计阶段：

- ClamAV 等外部恶意文件扫描器接入；
- 管理员按用户覆盖配额和扩容审批；
- 上传、检索、导出、管理员操作的数据库审计日志；
- 用户在线编辑并重新确认对齐关系。
