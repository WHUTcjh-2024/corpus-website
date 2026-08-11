# 压测与性能回归

`backend/loadtests/public_api.py` 只请求无副作用的 GET 接口，不会写入语料、创建导出任务或修改账号状态。

## 前置条件

1. 用本地 Compose 启动 PostgreSQL、Redis 与 Web 服务。
2. 准备已完成加工的演示语料，使 `/api/public-corpora/` 能覆盖真实序列化路径。
3. 在和目标部署等价的机器、数据库规模和网络条件下执行；本地结果不能代表生产容量。

## 基线命令

```bash
cd backend
python loadtests/public_api.py --base-url http://127.0.0.1:8010 --concurrency 20 --requests 1000
```

默认混合请求 `/healthz`、`/api/session/`、`/api/public-corpora/`。需要定位某个安全读接口时可以重复传入 `--endpoint`：

```bash
python loadtests/public_api.py --base-url http://127.0.0.1:8010 --concurrency 50 --requests 5000 --endpoint /api/public-corpora/
```

脚本输出吞吐、成功率、p50/p95/p99 延迟和 HTTP 状态分布；成功率低于 99% 时以非零状态结束，便于接入发布前检查。

## 回归准则

- 固定数据集、镜像版本、并发度和请求混合后保存基线结果。
- 每次改动检索、ORM 查询或序列化逻辑后，比较 p95、p99、RPS 与错误率；超过约定预算时必须附带 `EXPLAIN (ANALYZE, BUFFERS)` 证据。
- 性能检查分为单独的手动或夜间工作流，不放在 PR 的常规 CI 中，以避免共享 Runner 的噪声。
