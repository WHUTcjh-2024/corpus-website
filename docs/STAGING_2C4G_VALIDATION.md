# 2C4G staging validation

Date: 2026-09-19

## Environment

The validation used the repository's production container image and a dedicated
PostgreSQL/Redis/Web staging stack defined in
`docker-compose.staging-2c4g.yml`. Docker cgroups enforce the following runtime
ceilings:

| Service | vCPU | Memory | PIDs |
| --- | ---: | ---: | ---: |
| PostgreSQL | 0.50 | 768 MiB | 256 |
| Redis | 0.25 | 256 MiB | 128 |
| Django/Gunicorn | 1.25 | 2 GiB | 256 |
| **Concurrent total** | **2.00** | **3 GiB** | **640** |

The application therefore runs inside the requested 2-vCPU/4-GiB envelope,
with 1 GiB left for container and host overhead. The one-shot migration
container exits before Web starts and is not part of the concurrent total.
The Web container runs as UID/GID `10001:10001`.

This is a real containerized staging run on Docker Desktop with hard cgroup
limits. It does not claim to measure a particular cloud provider's network,
disk, or hypervisor behavior; a provider-hosted soak test remains a separate
release-environment check.

## Workload and gate

After a 300-request warm-up, the measured run used 5,000 requests at concurrency
50 across `/healthz`, `/api/session/`, and `/api/public-corpora/`:

```powershell
backend\.venv\Scripts\python.exe backend/loadtests/public_api.py `
  --base-url http://127.0.0.1:18010 `
  --concurrency 50 --requests 5000 `
  --max-error-rate 0.01 --max-p95-ms 1000 `
  --output docs/test-evidence/staging-2c4g-2026-09-19.json
```

Gate: error rate at most 1%, p95 latency at most 1,000 ms.

## Result

**PASS** — 5,000/5,000 requests returned HTTP 200. Throughput was 337.73
requests/second; p50/p95/p99 latency was 121.99/303.67/374.97 ms. The
machine-readable result is stored in
`docs/test-evidence/staging-2c4g-2026-09-19.json`.

To tear down the disposable stack:

```powershell
docker compose -f docker-compose.staging-2c4g.yml down -v
```
