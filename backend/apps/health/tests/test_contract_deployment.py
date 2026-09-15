from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class ContractDeploymentTests(SimpleTestCase):
    def test_nginx_bounds_general_and_research_traffic_per_ip(self):
        config = (PROJECT_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

        self.assertIn("limit_req_zone $binary_remote_addr zone=general_per_ip", config)
        self.assertIn("limit_req_zone $binary_remote_addr zone=research_per_ip", config)
        self.assertIn("limit_conn connections_per_ip 20", config)
        self.assertIn("location ~ ^/(search|parallel|statistics|exports)/", config)
        self.assertIn("limit_req_status 429", config)

    def test_nginx_reuses_upstream_connections(self):
        config = (PROJECT_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

        self.assertIn("upstream corpus_web", config)
        self.assertIn("keepalive 16", config)
        self.assertEqual(config.count("proxy_http_version 1.1"), 2)
        self.assertEqual(config.count('proxy_set_header Connection ""'), 2)

    def test_production_compose_uses_bounded_threaded_workers(self):
        config = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

        self.assertIn("--worker-class gthread", config)
        self.assertIn("--workers ${GUNICORN_WORKERS:-2}", config)
        self.assertIn("--threads ${GUNICORN_THREADS:-4}", config)
        self.assertIn("${PROCESSING_WORKER_CONCURRENCY:-1}", config)
        self.assertIn("--workers=${AUDITOR_WORKERS:-1}", config)
        self.assertIn("DB_CONN_MAX_AGE_SECONDS", config)
        self.assertIn("PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS", config)
        self.assertIn("DJANGO_HEALTHCHECK_HOST", config)
        self.assertIn("headers={'Host': os.environ['DJANGO_HEALTHCHECK_HOST']}", config)

    def test_local_compose_matches_safe_concurrency_defaults(self):
        config = (PROJECT_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

        self.assertIn("${PROCESSING_WORKER_CONCURRENCY:-1}", config)
        self.assertIn("${PROCESSING_WORKER_MAX_TASKS_PER_CHILD:-50}", config)
        self.assertIn("--workers=${AUDITOR_WORKERS:-1}", config)
        self.assertIn("DB_CONN_MAX_AGE_SECONDS", config)
        self.assertIn("PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS", config)

    def test_single_host_compose_supplies_bounded_persistent_dependencies(self):
        config = (PROJECT_ROOT / "docker-compose.single-host.yml").read_text(
            encoding="utf-8"
        )

        for service in ("db", "redis", "clamav", "backup"):
            self.assertIn(f"  {service}:\n", config)
        for volume in ("postgres_data", "redis_data", "clamav_signatures"):
            self.assertIn(f"  {volume}:\n", config)

        self.assertIn("--appendonly yes", config)
        self.assertIn("--maxmemory-policy noeviction", config)
        self.assertIn("POSTGRES_MAX_CONNECTIONS", config)
        self.assertIn("mem_limit:", config)
        self.assertIn("pids_limit:", config)
        self.assertIn("max-size: ${LOG_MAX_SIZE:-10m}", config)
        self.assertNotIn('"5432:5432"', config)
        self.assertNotIn('"6379:6379"', config)

    def test_single_host_nginx_terminates_tls_and_hides_internal_probes(self):
        config = (PROJECT_ROOT / "deploy" / "nginx.single-host.conf").read_text(
            encoding="utf-8"
        )

        self.assertIn("listen 443 ssl", config)
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3", config)
        self.assertIn("location = /metrics", config)
        self.assertIn("location = /readyz", config)
        self.assertIn("zone=login_per_ip", config)
        self.assertIn("proxy_set_header X-Forwarded-Proto https", config)

    def test_single_host_backup_is_validated_before_publication(self):
        script = (PROJECT_ROOT / "deploy" / "scripts" / "backup-loop.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("pg_restore --list", script)
        self.assertIn("tar --list --gzip", script)
        self.assertIn("sha256sum -c", script)
        self.assertIn('mv "$partial_dir" "$final_dir"', script)
        self.assertIn("BACKUP_RETENTION_DAYS", script)
