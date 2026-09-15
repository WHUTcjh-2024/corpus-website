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
