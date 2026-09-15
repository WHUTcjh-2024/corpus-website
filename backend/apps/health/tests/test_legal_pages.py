from django.test import SimpleTestCase, override_settings
from django.urls import reverse


@override_settings(
    FEEDBACK_SUPPORT_NAME="陈俊宏",
    FEEDBACK_SUPPORT_EMAIL="570372819@qq.com",
)
class LegalPageTests(SimpleTestCase):
    def test_legal_pages_are_public_and_include_contact_details(self) -> None:
        pages = {
            "privacy_policy": "隐私政策",
            "user_agreement": "用户协议",
            "copyright_notice": "版权投诉说明",
        }
        for route_name, heading in pages.items():
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)
                self.assertContains(response, "570372819@qq.com")

    def test_legal_pages_reject_state_changing_methods(self) -> None:
        for route_name in ("privacy_policy", "user_agreement", "copyright_notice"):
            with self.subTest(route_name=route_name):
                response = self.client.post(reverse(route_name))
                self.assertEqual(response.status_code, 405)
