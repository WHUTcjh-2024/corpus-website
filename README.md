# 在线语料库平台

本项目按模块逐步开发一个本地优先的在线语料库平台。当前完成范围：

- 阶段 0：Django、PostgreSQL、Redis、Celery 与 Docker 本地骨架。
- 阶段 1：语料自动分类与 manifest 生成。
- 阶段 2：用户申请、审核、角色与后端权限控制。
- 阶段 3：语料库登记、可见性控制与 Corpus Documentation。
- 阶段 4：语料加工流水线、统一文件输出与 Celery 异步入口。

当前未实现 KWIC 查询页面、ParaConc、AntConc 扩展、CQPweb 查询、用户文件上传和结果导出。

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

- `RawMonoTxtImporter`：中文或英文原文的分段、分句、基础分词。
- `AlignedTsvImporter`：解析每行 `zh<TAB>en` 的双语句对。
- `TaggedCorpusImporter`：解析中文 `word/POS` 和英文 `token_POS`。
- `XmlLikeImporter`：使用 XML 解析器读取 `<head>`、`<p>`、`<s>` 结构。
- `AutoAlignImporter`：基础版按句子顺序配对一份中文原文和一份英文原文。

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

文件型索引保存在 `data/indexes/<corpus_id>/`。`kwic_index.sqlite` 和 `word_frequency.json` 在本阶段生成；N-gram、搭配、分布图和词云文件明确标记为 `deferred`，由后续模块实现。PostgreSQL 不保存全文或全量 token。

任务状态为 `pending`、`running`、`success`、`failed`。同一语料库不能同时存在两个活动任务；失败原因写入 `ProcessingTask.error_message`，源文件始终只读。

## 权限规则

- 未登录用户不能进入工作台或语料库页面。
- `pending`、`rejected`、`disabled` 用户不能进入工作台。
- `test_user` 只能看到 demo 语料库，不能登记个人语料库。
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

## 阶段 0-4 人工验收

1. 启动 PostgreSQL、Redis、Django 和 Celery，确认首页、`/healthz`、`/readyz` 可访问。
2. 运行 `scan_corpus_inbox`，核对 manifest 数量，并确认原始语料未被修改。
3. 运行 `seed_accounts`，使用 `test_user` 登录，确认只能看到 demo 语料库且不能登记个人语料库。
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
14. 运行全量 `pytest`，确认阶段 0-4 回归测试全部通过。

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

阶段 0-4 合并 Word 测试报告保存在：

`docs/在线语料库平台-阶段0-4合并测试报告.docx`
