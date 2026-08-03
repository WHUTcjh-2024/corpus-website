# 生产部署与恢复

## 上线前

1. 将 `.env.prod.example` 复制为独立的生产环境文件，替换所有示例密钥和数据库地址。
2. 确认 PostgreSQL、Redis、ClamAV 可达，并为 `data/` 配置持久磁盘和每日快照。
3. 先验证配置：

   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet
   ```

4. 构建并启动：

   ```bash
   docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
   docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py check --deploy
   docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py validate_corpus_indexes
   ```

Nginx 对外提供 HTTP；TLS 应在校级网关或独立反向代理终止，并传入 `X-Forwarded-Proto`。Web 启动时自动执行数据库迁移和静态文件收集。

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
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py validate_corpus_indexes
```

索引校验失败时运行 `repair_corpus_indexes`；它从登记的源文件重新加工，不应手工修改 SQLite 索引。

## 回滚与监控

- 发布前记录镜像标签和数据库备份。代码回滚只切回上一镜像；涉及不可逆数据库迁移时从备份恢复。
- 监控 `/healthz`、Web 5xx、Celery 失败任务、队列长度、磁盘剩余空间、ClamAV 状态和备份时间。
- `data/` 剩余空间低于 20%、连续加工失败或索引自动修复反复触发时告警。
