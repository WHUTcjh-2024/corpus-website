from django.test import SimpleTestCase, override_settings

from .views import _support_context


class FeedbackSupportConfigurationTests(SimpleTestCase):
    @override_settings(
        FEEDBACK_SUPPORT_NAME="陈俊宏",
        FEEDBACK_SUPPORT_EMAIL="570372819@qq.com",
    )
    def test_support_context_uses_the_configured_real_contact(self) -> None:
        self.assertEqual(
            _support_context(),
            {
                "feedback_support_name": "陈俊宏",
                "feedback_support_email": "570372819@qq.com",
            },
        )
