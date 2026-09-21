from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class RemovedAgentRouteTests(SimpleTestCase):
    def test_removed_page_and_api_routes_do_not_resolve(self):
        for path in ("/agent/", "/api/agent/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
