# 在线语料库平台

武汉理工大学外国语学院在线语料研究平台。项目提供语料上传与加工、KWIC、平行检索、统计分析、受控导出、账号审批和审计功能。

AntConc 4.4.2 功能对齐和老师语料验收基线见 [docs/ANTCONC_442_PARITY.md](docs/ANTCONC_442_PARITY.md)。

## 技术结构

```text
backend/   Django、REST API、Celery、页面模板和静态资源
frontend/  React 首页源码
data/      本地语料、加工产物和检索索引
```

运行依赖：Python 3.12、PostgreSQL 16、Redis 7、Node.js 20+。

## 启动

推荐使用本地 Docker：

```powershell
docker compose -f docker-compose.local.yml up -d --build
```

访问 `http://localhost:8010/`。本地测试账号为 `test` / `test`，生产配置不会启用该弱口令账号。

直接运行 Django 时：

```powershell
docker compose -f docker-compose.local.yml up -d db redis
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py ensure_test_account
.\.venv\Scripts\python manage.py runserver 127.0.0.1:8010
```

## 前端构建

React 只负责平台首页，构建产物由 Django 统一提供：

```powershell
cd frontend
npm install
npm run build
```

正式入口始终为 `http://localhost:8010/`；`5173` 仅用于 Vite 热更新。

## 常用维护命令

```powershell
cd backend
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py validate_corpus_indexes
.\.venv\Scripts\python manage.py repair_corpus_indexes
.\.venv\Scripts\python manage.py scan_corpus_inbox
```

运行数据位于 `data/`，默认不提交 Git。不要手工修改 `processed/` 和 `indexes/` 中的加工产物。

## 生产部署

使用 `docker-compose.prod.yml` 和 `.env.prod.example`。部署前必须设置独立的密钥、数据库凭据、允许域名和 CSRF 来源，并确认：

- `DJANGO_DEBUG=false`
- `FIXED_TEST_ACCOUNT_ENABLED=false`
- PostgreSQL、Redis 和 Celery worker 可用
- 上传扫描、导出限额、数据备份策略已配置

完整上线、备份、恢复和监控步骤见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。
