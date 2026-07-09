# 在线语料库平台

这是阶段 0 的本地开发骨架，只负责搭建 Django、PostgreSQL、Redis、Celery、Docker Compose、健康检查接口和自动测试。当前阶段不实现语料分类、检索、上传、KWIC、ParaConc 或统计分析功能。

## 本地 Docker 启动

在 `corpus-platform` 目录下执行：

```powershell
docker compose -f docker-compose.local.yml up -d db redis
docker compose -f docker-compose.local.yml build web worker
docker compose -f docker-compose.local.yml run --rm web python manage.py migrate
docker compose -f docker-compose.local.yml up -d web worker
```

访问地址：

- `http://localhost:8010/`
- `http://localhost:8010/healthz`
- `http://localhost:8010/readyz`

在 Docker 中运行测试：

```powershell
docker compose -f docker-compose.local.yml run --rm web pytest
```

检查 Celery 是否能加载并注册任务：

```powershell
docker compose -f docker-compose.local.yml run --rm worker celery -A config inspect registered
```

停止服务：

```powershell
docker compose -f docker-compose.local.yml down
```

## 本地 Python 启动

项目明确使用 PostgreSQL，不使用 SQLite。阶段 0 的单元测试不依赖数据库连接，但运行环境和迁移需要 PostgreSQL。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\pytest
```

如果 Windows 上 `8000` 端口不可用，开发服务使用 `8010`：

```powershell
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8010
```

## 数据保护规则

不要提交教师完整语料、用户上传文件、加工结果、索引、manifest 和导出结果。`data/` 下被忽略的目录只保留 `.gitkeep` 占位文件。

必须避免进入 Git 的目录包括：

- `test_conc/`
- `data/inbox/`
- `data/dev_sample/`
- `data/teacher_private/`
- `data/user_uploads/`
- `data/processed/`
- `data/indexes/`
- `data/exports/`
- `data/manifests/`

## 人工验收步骤

1. 使用 Docker Compose 启动 `db` 和 `redis`。
2. 确认 Django migration 可以正常写入 PostgreSQL。
3. 启动 Django web 服务和 Celery worker。
4. 确认 `/`、`/healthz`、`/readyz` 可以访问。
5. 运行 `pytest` 并确认全部通过。
6. 检查 `test_conc/` 和 `data/` 下的语料文件没有被加入 Git。

## 当前阶段测试报告

阶段 0 的 Word 测试报告位于：

`docs/阶段0-Django-PostgreSQL-Redis-Celery-Docker本地骨架-测试报告.docx`
