from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.health.context_processors import public_site_metadata


class PublicSiteMetadataTests(SimpleTestCase):
    @override_settings(ICP_LICENSE_NUMBER="鄂ICP备12345678号-1")
    def test_icp_license_is_available_to_every_server_rendered_footer(self) -> None:
        request = RequestFactory().get("/")
        self.assertEqual(
            public_site_metadata(request),
            {"icp_license_number": "鄂ICP备12345678号-1"},
        )

    @override_settings(ICP_LICENSE_NUMBER="")
    def test_footer_renders_an_explicit_placeholder_before_filing(self) -> None:
        response = self.client.get("/privacy/")
        self.assertContains(response, "ICP备案号：待填写")
