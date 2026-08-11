from celery import current_app
from django.conf import settings
from django.test import SimpleTestCase


class CeleryTaskRoutingTests(SimpleTestCase):
    def test_declares_isolated_processing_and_export_queues(self):
        queues = {queue.name for queue in settings.CELERY_TASK_QUEUES}

        self.assertEqual(queues, {"default", "processing", "exports"})

    def test_routes_each_durable_task_to_its_dedicated_queue(self):
        expected_routes = {
            "processing.process_corpus": "processing",
            "exports.build_export": "exports",
        }

        for task_name, expected_queue in expected_routes.items():
            route = current_app.amqp.router.route(
                {}, task_name, args=(), kwargs={}
            )

            self.assertEqual(route["queue"].name, expected_queue)
