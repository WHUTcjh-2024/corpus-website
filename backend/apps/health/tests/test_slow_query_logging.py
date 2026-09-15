from __future__ import annotations

from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.middleware import SlowQueryLogger, SlowQueryLoggingMiddleware, sanitize_sql


class SlowQueryLoggerTests(SimpleTestCase):
    def test_slow_query_is_logged_without_parameters_or_literal_values(self):
        execute = Mock(return_value="result")
        wrapper = SlowQueryLogger(threshold_ms=500)

        with (
            patch("config.middleware.time.perf_counter", side_effect=[10.0, 10.75]),
            self.assertLogs("corpus_platform.slow_queries", level="WARNING") as captured,
        ):
            result = wrapper(
                execute,
                "SELECT * FROM accounts WHERE email = 'inline@example.test' AND id = 42",
                ("secret@example.test",),
                False,
                {},
            )

        self.assertEqual(result, "result")
        rendered = captured.output[0]
        self.assertIn("duration_ms=750.00", rendered)
        self.assertNotIn("secret@example.test", rendered)
        self.assertNotIn("inline@example.test", rendered)
        self.assertNotIn("42", rendered)

    def test_sql_sanitizer_keeps_structure_and_bounds_output(self):
        rendered = sanitize_sql("SELECT  *  FROM records WHERE label='private' AND score=123 " + "x" * 2_000)

        self.assertTrue(rendered.startswith("SELECT * FROM records"))
        self.assertNotIn("private", rendered)
        self.assertNotIn("123", rendered)
        self.assertLessEqual(len(rendered), 1_000)


class SlowQueryLoggingMiddlewareTests(SimpleTestCase):
    @override_settings(DATABASE_SLOW_QUERY_MS=0)
    def test_zero_threshold_disables_wrapper(self):
        get_response = Mock(return_value=HttpResponse("ok"))
        middleware = SlowQueryLoggingMiddleware(get_response)
        request = RequestFactory().get("/")

        with patch("config.middleware.connection.execute_wrapper") as execute_wrapper:
            response = middleware(request)

        self.assertEqual(response.status_code, 200)
        execute_wrapper.assert_not_called()
