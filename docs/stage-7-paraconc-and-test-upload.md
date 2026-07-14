# 阶段 7：ParaConc 平行检索与测试账号受限上传

## 交付结论

阶段 7 已完成，并通过自动测试和真实数据联调。实现范围包括：

- 中文检索并同步显示英文译文；
- 英文检索并同步显示中文原文，英文条件忽略大小写；
- 中文/英文 `contains` 与 `not contains` 组合过滤；
- 对齐 TSV 使用句子单元；老师提供的成对文本保留人工段落单元；
- 命中总数、稳定语料顺序、分页及双栏关键词高亮；
- 本人平行语料可流式导出 TSV，教师及 demo 语料禁止导出；
- 测试账号可上传少量私有 `.txt` 语料并自动进入 Celery 加工；
- 测试账号仍不能查看教师语料或其他用户的私有语料。

## 产品语义

本阶段参考 ParaConc 的原文/译文同步检索模式，以及 AntConc 的命中总数、结果分页、稳定结果顺序和证据导向交互。页面不是两个彼此独立的搜索框：每一行都是同一个对齐对，中文和英文条件共同作用于这组记录。

默认查询示例：

```text
主检索词：人民
检索方向：中文 → 英文
中文同时包含：生活
英文同时包含：people
英文排除：impossible
对齐单元：句子
```

所有非空正向条件按 `AND` 组合，排除条件按 `NOT` 组合。结果先完成全量过滤，再按 `global_position` 排序，最后分页，因此翻页不会打乱句对顺序。

## 代码结构

```text
backend/apps/parallel/
├── engine.py       # 只读 SQLite 查询、条件组合、分页、高亮
├── forms.py        # 输入规范化与白名单校验
├── views.py        # 权限、页面和流式 TSV 导出
├── urls.py
└── tests.py

backend/templates/parallel/search.html
backend/apps/processing/artifacts.py
backend/apps/corpora/forms.py
backend/apps/corpora/services.py
backend/apps/corpora/views.py
backend/templates/corpora/corpus_upload.html
```

## 索引设计

加工流水线继续只生成既有的 `kwic_index.sqlite` 文件，不增加新的运行时文件。SQLite 内新增 `parallel_pairs` 表：

```text
global_position     全语料稳定顺序
pair_id             稳定句对 ID
pair_ordinal        源文件内顺序
zh_text/en_text     双语文本
zh_normalized       中文检索值
en_normalized       英文 casefold 检索值
alignment_unit      sentence / paragraph
method              provided / provided_paragraph_order
confidence          对齐置信度
```

查询使用参数化 SQL 和固定字段白名单。Web 请求以 SQLite `mode=ro` 打开索引；旧索引缺少 `parallel_pairs` 表时返回 409，并要求重新加工，不会在请求期间扫描全文或临时建索引。

## 上传安全边界

测试账号上传是受限例外，不等于获得正式账号权限。

| 规则 | 测试账号 | 普通已审核账号 |
|---|---:|---:|
| 格式 | `.txt` | `.txt` |
| 单文件上限 | 2 MB | 30 MB |
| 账号总额 | 5 MB | 30 MB |
| 可见范围 | demo + 本人私有上传 | 等级语料 + 本人私有上传 |
| 教师语料导出 | 禁止 | 禁止 |

上传文件保存到：

```text
data/user_uploads/<user_id>/<corpus_id>/<random_uuid>.txt
```

服务端不使用原始文件名作为磁盘路径；文件名只作为元数据保存。配额检查对用户记录加数据库行锁，实际写入时再次计算字节数。临时文件完成写入和 `fsync` 后原子替换，再创建 `CorpusFile` 与 `ProcessingTask`。

## 自动测试

执行：

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q
```

结果：

```text
82 passed in 10.69s
```

阶段 7 重点覆盖：

- 中译英、英译中检索；
- 英文大小写归一化；
- 双语 AND/NOT；
- 句/段对齐单元；
- 全结果顺序和分页；
- 安全高亮；
- 未授权私有语料返回 403；
- 教师/demo 导出返回 403；
- 所有者 TSV 流式导出；
- 测试账号小文件上传、配额拒绝和私有可见性；
- 人工段落对齐在两侧句数不同时仍保持同段，不按句号错配；
- 人工段落数量不一致时加工失败，禁止静默截断；
- 阶段 0–6 全量回归。

## 真实联调记录

联调日期：2026-07-11。

平行 demo：

```text
corpus_id: 22dcb6b9-b98b-45a9-927e-1eeec5d74bc3
人工段落对: 11
段内派生句数: 中文 37 / 英文 43（只供各自 KWIC）
中文“人民”: 6 个对齐段落
英文“happy life”: 1 个对齐段落，对应中文第 8 段
“人民” AND 英文包含 “happy life”: 1 个对齐段落
对齐方法: provided_paragraph_order
对齐置信度: 1.00（表示采用老师提供的人工顺序）
页面权限请求: HTTP 200
```

### 对齐缺陷修正记录

初版错误地把老师的 11 组人工段落分别切成中文 37 句、英文 43 句，再按句号顺序生成 37 个伪句对，造成第 18 条之后明显漂移。修正后：

- `paired_raw_zh_en` 的输入契约明确为人工段落对；
- 中英文段落数必须相等，否则加工失败；
- `parallel_pairs` 直接使用段落 ID 和完整段落文本；
- 段内分句只服务 AntConc/KWIC，不参与 ParaConc 对齐；
- ParaConc 自动锁定该语料的“段落”选项，不允许误选句子单元；
- 原始文件重建前后 SHA-256 和修改时间完全一致。

### 老师语料的两类人工对齐契约

#### 无标注中英文本

- 中文和官译分别存放；
- 空行形成的段落顺序是老师提供的人工对齐；
- 只生成 `paragraph` 对齐对；
- 两侧段落数不一致时立即加工失败；
- 段内分句和 Token 仅用于各自语言 KWIC。

#### 带结构与 POS 标注的中英文本

- `<p n="...">` 是人工段落编号；
- `<s n="...">` 是人工句子编号；
- 中文使用 `词/POS`，英文使用 `token_POS`；
- 中英文编号必须逐段、逐句完全一致；
- 同时生成 `sentence` 和 `paragraph` 两种 ParaConc 对齐对；
- 页面展示去除结构与 POS 符号，Token 索引保留词性；
- `< /s>`、`/s>` 等已发现的闭合格式变体只在内存中容错，不修改老师源文件；
- 未带 POS 的少量原始 token 保留为 `POS=UNK`，并写入加工警告。

真实标注 demo 联调：

```text
corpus_id: 962f8abb-444e-411e-a2ce-ae2bc8009031
document: 湖南农民运动考察报告
numbered paragraph pairs: 98
numbered sentence pairs: 693
tokens: 27,566
distinct POS tags: 247
tokens preserved as POS=UNK: 90
sentence n=341: 第三件经济上打击地主 ↔ 3 HITTING THE LANDLORDS ECONOMICALLY
```

测试账号真实上传：

```text
corpus_id: cfaf0519-e273-4376-a189-22775cd71716
task_id: 7c4236eb-a669-4cc2-b3ed-256218aa6941
task status: success
progress: 100
KWIC “language”: 1 条
owner visibility: true
```

运行状态：Django `127.0.0.1:8010` 健康检查 200，Celery worker `pong`。

## 人工验收

1. 使用测试账号登录并进入“语料库”。
2. 打开中英平行 demo 的 `ParaConc`。
3. 方向选“中文 → 英文”，检索“人民”，确认两列同步显示且中文高亮。
4. 方向选“英文 → 中文”，检索 `GOAL`，确认忽略大小写且英文高亮。
5. 展开双语条件，组合 `contains` 和 `not contains`，确认命中数变化。
6. 确认 demo 页面只有在线检索提示，没有可用导出入口。
7. 点击“上传 TXT 语料”，上传一个小型中文或英文文件。
8. 等待状态变为“可用”，进入 KWIC 检索本人上传的内容。
9. 确认本人上传只对本人和管理员可见。

## 上线前继续项

- Web 反向代理配置上传体积上限，并与 Django 配额保持一致；
- 上传目录挂载到独立持久卷，禁止 Web 静态服务直接暴露；
- Celery 增加任务监控、失败告警和过期临时文件清理；
- 大型平行索引增加基准测试，并根据真实数据量评估 FTS/倒排索引；
- 增加审计日志、下载频率限制和数据保留策略；
- 生产环境强制 HTTPS、安全 Cookie、可信 Host 与独立密钥。
