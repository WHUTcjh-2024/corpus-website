# 在线语料库平台

面向教学与研究的中英双语语料库平台，提供账号与权限、语料导入、KWIC/CQP 检索、平行语料检索、词频与搭配统计、图表、导出、质量审计和受控 Agent 工作流。

## 技术栈

- Django 5.2、Django REST Framework、Celery
- PostgreSQL、Redis、SQLite 检索索引
- React 19、TypeScript、Vite
- Go 平行语料审计服务
- Docker Compose、Nginx、Prometheus

## 本地启动

要求 Docker Engine 与 Docker Compose v2。

```powershell
Copy-Item .env.local.example .env.local
docker compose --env-file .env.local -f docker-compose.local.yml up --build
```

访问 `http://127.0.0.1:8010/`。首次启动由一次性 `migrate` 服务完成迁移，Web 服务只负责应用启动。

## 开发与测试

后端：

```powershell
backend/.venv/Scripts/python.exe backend/manage.py check
backend/.venv/Scripts/python.exe -m ruff check backend
$env:DJANGO_SETTINGS_MODULE='config.settings.test_sqlite'
backend/.venv/Scripts/python.exe backend/manage.py test
```

前端：

```powershell
Set-Location frontend
npm ci
npm run lint
npm run test:coverage
npm run build
```

前端构建产物写入 `backend/static/frontend/`，由 Docker 多阶段构建生成，不提交到 Git。

## Python 依赖

直接依赖维护在 `backend/requirements.in` 与 `backend/requirements-dev.in`，带哈希的完整锁文件为 `requirements.txt` 与 `requirements-dev.txt`。

```powershell
Set-Location backend
.venv/Scripts/pip-compile.exe --generate-hashes --no-header --resolver=backtracking --strip-extras --output-file=requirements.txt requirements.in
.venv/Scripts/pip-compile.exe --generate-hashes --no-header --allow-unsafe --resolver=backtracking --strip-extras --output-file=requirements-dev.txt requirements-dev.in
```

CI 会重新生成并校验锁文件，禁止未锁定依赖进入 `main`。

## 目录

- `backend/`：Django 应用、迁移、检索与统计模块
- `frontend/`：React 前端源码
- `backend/go/corpus-auditor/`：Go 审计服务
- `deploy/`：Nginx、监控、备份与单机部署脚本
- `docs/`：用户、运维、部署、验收与测试文档
- `scripts/`：正式语料导入、证据与报告生成工具

## 生产部署

- 通用部署：[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- 2 核 4 GB 单机部署：[docs/SINGLE_HOST_DEPLOYMENT.md](docs/SINGLE_HOST_DEPLOYMENT.md)
- 运维手册：[docs/OPERATIONS.md](docs/OPERATIONS.md)
- 用户手册：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- 合同验收：[docs/CONTRACT_ACCEPTANCE.md](docs/CONTRACT_ACCEPTANCE.md)

生产镜像以非 root 用户运行；发布流程必须先执行一次性 `migrate` 服务，再启动 Web 和后台工作进程。
