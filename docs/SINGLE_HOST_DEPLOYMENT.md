# 2 核 4 GB 单机生产部署

本方案在一台 Linux 主机上运行 Web、PostgreSQL、Redis、ClamAV、Celery、
Go 审计器、Nginx 和自动备份。它面向低流量首发；数据库和 Redis 不暴露宿主机端口，
以后可以只修改连接地址迁移到托管服务。

生产命令始终同时指定两个 Compose 文件：

```bash
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml <command>
```

## 1. 主机准备

建议使用 Ubuntu 24.04 LTS、Docker Engine 和 Docker Compose v2。安全组只开放
SSH、TCP 80 和 TCP 443；不要开放 5432、6379、3310、8000、8090、9090。

2 核 4 GB 主机必须配置至少 2 GB Swap，用于吸收 ClamAV 更新病毒库和语料加工时的
短时内存峰值。Swap 不能替代内存，若持续使用 Swap，应停止增加并发并升级实例。

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

准备仅供 root 和容器使用的持久目录：

```bash
sudo install -d -m 0750 /srv/corpus-platform/data
sudo install -d -m 0700 /srv/corpus-platform/backups
sudo install -d -m 0700 /srv/corpus-platform/letsencrypt
sudo install -d -m 0755 /srv/corpus-platform/certbot-www
```

## 2. 生产配置

```bash
cp .env.single-host.example .env.single-host
chmod 600 .env.single-host
```

替换文件中的全部 `replace-with-`、`your-domain.example` 和示例邮箱。密钥应分别随机
生成，不能复用。数据库和 Redis 密码使用 URL 安全字符，因为它们同时出现在连接 URL
中；修改密码后必须同步修改相应 URL。

```bash
openssl rand -hex 32
```

保持以下低配主机默认值：Gunicorn `2 × 4` 线程、加工/导出/审计并发均为 `1`、
PostgreSQL 最大 50 连接、Redis 最大内存 384 MB 且禁止驱逐。Redis 同时承载任务和审计
消息，不能使用会静默删除键的 LRU 驱逐策略。

## 3. 首次申请 TLS 证书

先完成域名解析，确认 80 端口未被其他程序占用，然后在应用栈尚未启动时执行。将下面
的域名和邮箱替换为真实值；证书名称必须保持为 `corpus-platform`，Nginx 使用这个稳定
路径加载证书。

```bash
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  --profile tls run --rm --publish 80:80 certbot \
  certonly --standalone --preferred-challenges http \
  --cert-name corpus-platform \
  --domain corpus.example.edu.cn \
  --email admin@example.edu.cn \
  --agree-tos --no-eff-email
```

证书会写入 `.env.single-host` 中 `LETSENCRYPT_HOST_PATH` 指定的目录。

## 4. 预检与启动

预检会拒绝示例密钥、非绝对持久化路径、缺失证书、内存不足或无效 Compose 配置；
Swap 不足会给出警告。

```bash
./deploy/scripts/single-host-preflight.sh .env.single-host

docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  up -d --build
```

ClamAV 首次下载和加载病毒库可能需要数分钟，`web` 会等待其健康后启动。不要为了加快
首次启动而关闭上传扫描。

创建唯一的正式管理员，不要运行 `seed_accounts` 或保留验收密码：

```bash
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  exec web python manage.py createsuperuser
```

完成启动检查：

```bash
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml ps

docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  exec web python manage.py check --deploy --fail-level WARNING

docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  exec web python manage.py validate_corpus_indexes

curl --fail --show-error --silent https://corpus.example.edu.cn/healthz
```

## 5. 备份与恢复验证

`backup` 服务在首次启动后立即备份一次，之后按
`BACKUP_INTERVAL_SECONDS` 周期备份。每份备份包含 PostgreSQL custom dump、完整 `data/`
归档、清单和 SHA-256 校验文件。只有数据库与文件归档都通过结构检查后，临时目录才会
原子重命名为正式备份；默认清理七天前的日备份。

需要在发布前立即额外备份时，可运行一次性容器，不必重启常驻备份服务：

```bash
BACKUP_ONCE=true docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  run --rm backup
```

```bash
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml logs --tail 100 backup

cd /srv/corpus-platform/backups/corpus-YYYYMMDDTHHMMSSZ-RANDOM
sha256sum --check SHA256SUMS
pg_restore --list database.dump >/dev/null
tar -tzf data.tar.gz >/dev/null
```

服务器本地备份不能抵御整机或磁盘故障。至少每天把最新目录同步到 COS 或另一台主机，
并保留四个周备份。每月在隔离环境恢复数据库和 `data/`，再运行
`validate_corpus_indexes`；未做恢复验证的备份不能视为可用。

## 6. TLS 自动续期

脚本使用 Webroot 续期并在成功后平滑重载 Nginx。使用 root 的 crontab 每天运行一次：

```cron
17 4 * * * cd /opt/corpus-platform && ./deploy/scripts/renew-tls.sh .env.single-host >> /var/log/corpus-platform-tls.log 2>&1
```

## 7. 发布与回滚

每次发布前确认当前备份成功并记录镜像/提交号，然后执行：

```bash
git fetch origin
git pull --ff-only origin main
docker compose --env-file .env.single-host \
  -f docker-compose.prod.yml \
  -f docker-compose.single-host.yml \
  up -d --build --remove-orphans
```

发布后检查容器健康、首页登录、公开语料列表、检索、导出和管理员审批流程。代码回滚必须
使用已记录的提交，不得回滚数据库文件；数据库迁移不兼容时，从发布前备份恢复。

## 8. 2 核 4 GB 运行边界

- 不提高加工、导出或审计并发。
- Prometheus 默认不启动；需要时使用 `--profile monitoring`，并观察其额外内存占用。
- CPU 持续超过 70%、内存持续超过 80%、发生 OOM、Swap 持续增长或任务队列五分钟内
  无法清空时，停止批量导入并升级主机。
- `/metrics` 和 `/readyz` 不经过公网 Nginx 暴露；外部存活探测使用 `/healthz`。
