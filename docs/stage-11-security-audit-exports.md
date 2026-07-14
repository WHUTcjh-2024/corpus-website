# 阶段 11：安全、审计、水印与受控导出

## 结论

阶段 11 已完成并通过自动化、生产配置和真实语料联调。AntConc、ParaConc 和 CQP/KWIC 的既有索引及查询结构未改变；本阶段以独立审计、导出、配额和扫描器服务建立安全边界。

## 实现范围

### 审计与水印

- `AuditEvent` 记录登录成功/失败、退出、KWIC、ParaConc、AntConc 统计、上传、重试、删除、扩容、导出和管理员操作。
- 审计元数据限制总大小、文本长度和集合项数；登录失败只保存用户名，不保存密码。
- IP 使用 `REMOTE_ADDR`，不直接信任 `X-Forwarded-For`。
- 教师语料页面生成“当前用户 + 当前分钟 + HMAC 追踪码”的重复动态水印；demo 和用户私有语料不显示教师水印。
- 审计后台只读，禁止新增、修改和删除。

### Celery 受控导出

- `ExportJob` 保存导出类型、规范化查询、状态、进度、结果行数、文件路径、下载次数和过期时间。
- 仅本人且状态为 `ready` 的用户私有语料可以导出；教师和 demo 语料的 KWIC/ParaConc 导出均返回 403。
- 单用户只允许一个 pending/running 任务，并限制每小时创建次数、最大结果行数、有效期和最大下载次数。
- Celery 将 UTF-8 BOM TSV 写入临时文件，`fsync` 后原子替换到 `data/exports/<corpus>/<user>/`。
- 下载时重新校验所有者、状态、有效期、下载次数、文件存在性和路径必须位于导出根目录。
- TSV 单元格移除换行和 NUL；以 `= + - @` 开头的内容加前缀，避免电子表格公式注入。
- `expire_exports` 命令撤销过期任务并删除受控目录内的结果文件。

### 配额与上传扫描

- 用户可以申请新的单文件上限和账号总额；只允许一个待审核申请。
- 测试账号、未审核账号和停用账号不能申请；申请总额必须高于当前额度。
- 只有启用的 Django 后台管理员可以批准或拒绝；批准后覆盖用户实际上传上限，并记录审计事件。
- 上传内容通过文本校验后、最终落盘前调用可替换扫描器。
- 本地默认 `DisabledUploadScanner`；生产设置若仍禁用扫描器会直接启动失败。
- `ClamAVUploadScanner` 使用 clamd `INSTREAM` 协议，不把上传内容复制到第二个临时目录；连接失败或异常响应时关闭上传。

## 权限矩阵

| 操作 | 教师语料 | Demo | 本人私有语料 | 他人私有语料 |
|---|---:|---:|---:|---:|
| 在线检索 | 按等级允许，带水印 | 允许 | 允许 | 拒绝 |
| KWIC 后台导出 | 403 | 403 | 允许 | 403 |
| ParaConc 后台导出 | 403 | 403 | 允许 | 403 |
| 下载导出文件 | 不适用 | 不适用 | 仅创建人、限次、限时 | 403 |

## 自动化结果

执行环境：Windows 11、Python 3.12.13、Django 5.2.16、PostgreSQL 16、Redis 7。

```text
Stage 11 专项：18 passed in 5.15s
全量回归：134 passed in 31.51s
manage.py check：0 issues
makemigrations --check --dry-run：No changes detected
compileall：通过
git diff --check：通过
docker compose local/prod config --quiet：通过
production check --deploy：0 issues
```

专项测试覆盖：

- 登录成功/失败审计、密码不入库、Django Admin 操作镜像；
- 教师动态水印、demo 无教师水印、KWIC 检索审计；
- KWIC/ParaConc 导出生成、权限、限流、过期、下载次数和路径穿越；
- 配额申请、重复申请、角色限制、管理员审批和生效额度；
- 扫描器允许、拒绝回滚、无残留文件、错误后端关闭上传。

## `test_conc` 真实联调

命令：

```powershell
.\.venv\Scripts\python manage.py register_teacher_samples `
  --source-root D:\Desktop\CONC\test_conc `
  --source-type teacher `
  --access-level advanced `
  --process
```

四个高级教师语料的加工结果：

| 样本 | 文档 | 段落 | 句子 | Token | Type | 对齐单元 |
|---|---:|---:|---:|---:|---:|---:|
| 中国社会各阶级的分析（中文） | 1 | 12 | 107 | 1,833 | 752 | 0 |
| 人民对美好生活的向往就是我们的奋斗目标（英文） | 1 | 11 | 43 | 847 | 356 | 0 |
| 人民对美好生活的向往就是我们的奋斗目标（双语段对齐） | 2 | 22 | 80 | 1,455 | 672 | 11 |
| 湖南农民运动考察报告（双语编号/POS） | 2 | 196 | 1,386 | 27,566 | 4,911 | 791 |

真实 HTTP/Celery 链路结果：

- 老师英文样本复制到用户私有区后，HTTP 上传返回 302，Celery 加工成功，统计与老师源样本一致：43 句、847 tokens。
- 用户私有 KWIC 查询 `people` 返回 17 条；后台任务由 Celery 生成 17 行 TSV。
- 导出状态 API 返回 `success / progress=100 / row_count=17`；HTTP 下载返回 200、3,085 bytes，下载计数从 0 增为 1。
- 同一老师英文语料在线 KWIC 返回 200、17 条并显示动态水印；批量导出返回 403。
- 老师标注双语语料 ParaConc 查询“第三件”返回 200、1 条并显示动态水印；批量导出返回 403。
- 老师英文语料 AntConc Word List 返回 200、356 个 type 并显示动态水印。
- 联调账号累计生成 11 条登录、检索、统计、上传和导出审计事件。

抽取到的 5 个不同老师源文件在联调前后 SHA-256 完全一致；未修改、移动或删除 `test_conc` 中的任何文件。

## 运维说明

- 本地开发可显式使用禁用扫描器；生产必须配置活动扫描器及可达的 ClamAV 服务。
- 将 `python manage.py expire_exports` 配置为周期任务，建议至少每小时执行一次。
- `data/exports/`、审计数据库和应用日志应纳入备份、保留期及磁盘容量监控。
- 阶段 12 仍需完成反向代理、证书、备份恢复演练、监控告警、并发压测和最终发布门禁。
