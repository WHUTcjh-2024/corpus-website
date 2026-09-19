# 生产部署与恢复

本文档描述使用外部 PostgreSQL、Redis 和 TLS 网关的通用生产部署。低流量、2 核 4 GB
单机部署请使用 [SINGLE_HOST_DEPLOYMENT.md](SINGLE_HOST_DEPLOYMENT.md)；该方案在保留
未来迁移托管数据库能力的同时，补充本机 PostgreSQL、Redis、ClamAV、TLS、资源限制和
自动备份。

## 上线前

1. 将 `.env.prod.example` 复制为独立的生产环境文件，替换所有示例密钥和数据库地址。
2. 确认 PostgreSQL、Redis、ClamAV 可达，并为 `data/` 配置持久磁盘和每日快照。
   2 核 4 GB 主机建议保留默认的 2 个 Gunicorn 进程、每进程 4 线程，
   `PROCESSING_WORKER_CONCURRENCY=1`、`AUDITOR_WORKERS=1`；扩容前必须先压测。
3. 先验证配置：

   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet
   ```

4. 构建并启动：

   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml build --pull
   docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm migrate
   docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
   docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py check --deploy
   docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py validate_corpus_indexes
   ```

   生产基础镜像使用摘要固定，构建阶段安装发行版安全更新；CI 会用 Trivy 阻止仍含
   可修复 Critical/High 漏洞的镜像进入 `main`。发布时必须保留 `--pull`，不要绕过
   容器安全门禁或改用未扫描的临时镜像。

5. 验证数据库连接复用、公开查询索引与慢查询日志配置：

   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml exec web \
     python manage.py shell -c "from django.conf import settings; print(settings.DATABASES['default']['CONN_MAX_AGE'], settings.DATABASES['default']['CONN_HEALTH_CHECKS'], settings.DATABASE_SLOW_QUERY_MS)"
   psql "$DATABASE_URL" -c "SELECT indexname FROM pg_indexes WHERE tablename='corpora_corpus' AND indexname='corpus_public_list_idx';"
   ```

   预期依次得到正数连接寿命、`True`、正数慢查询阈值，以及
   `corpus_public_list_idx`。Web 日志中的 `slow_database_query` 记录只保留脱敏 SQL，
   不记录参数值。

Nginx 对外提供 HTTP；TLS 应在校级网关或独立反向代理终止，并传入 `X-Forwarded-Proto`。每次发布先显式运行一次性 `migrate` 服务，再启动无副作用的 Web 服务；数据库迁移、正式管理员同步和静态文件收集不会再与 Gunicorn 启动耦合。`outbox` 服务独立扫描 PostgreSQL 中待投递的任务事件；即使 Celery Broker 临时不可用，已经提交的加工和导出任务也会在 Broker 恢复后补投。请保持该服务常驻，并监控其待投递数量、重试次数和最早事件等待时间。

## 备份

需要同时备份数据库和 `data/`，二者必须来自同一维护窗口：

```bash
pg_dump --format=custom --file=corpus-platform.dump "$DATABASE_URL"
tar --create --gzip --file=corpus-data.tar.gz data/
```

至少保留 7 个日备份和 4 个周备份；每月在隔离环境做一次恢复演练。语料源文件、`processed/` 与 `indexes/` 不应只依赖容器层。

## 恢复

```bash
pg_restore --clean --if-exists --dbname="$DATABASE_URL" corpus-platform.dump
tar --extract --gzip --file=corpus-data.tar.gz
docker compose --env-file .env.prod -f docker-compose.prod.yml build --pull
docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm migrate
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py validate_corpus_indexes
```

索引校验失败时运行 `repair_corpus_indexes`；它从登记的源文件重新加工，不应手工修改 SQLite 索引。

## 回滚与监控

- 发布前记录镜像标签和数据库备份。代码回滚只切回上一镜像；涉及不可逆数据库迁移时从备份恢复。
- 监控 `/healthz`、Web 5xx、Celery 失败任务、队列长度、磁盘剩余空间、ClamAV 状态和备份时间。
- 监控 `slow_database_query` 日志；先执行 `EXPLAIN (ANALYZE, BUFFERS)` 再调整索引，
  不要仅因单条慢日志盲目加索引。
- `data/` 剩余空间低于 20%、连续加工失败或索引自动修复反复触发时告警。
