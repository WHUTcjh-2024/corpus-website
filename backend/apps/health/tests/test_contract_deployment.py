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

    def test_local_compose_matches_safe_concurrency_defaults(self):
        config = (PROJECT_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

        self.assertIn("${PROCESSING_WORKER_CONCURRENCY:-1}", config)
        self.assertIn("${PROCESSING_WORKER_MAX_TASKS_PER_CHILD:-50}", config)
        self.assertIn("--workers=${AUDITOR_WORKERS:-1}", config)
        self.assertIn("DB_CONN_MAX_AGE_SECONDS", config)
        self.assertIn("PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS", config)
