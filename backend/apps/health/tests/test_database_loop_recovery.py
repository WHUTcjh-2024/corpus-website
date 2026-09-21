from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import OperationalError
from django.test import SimpleTestCase

from apps.outbox.services import PublishSummary


class DatabaseLoopRecoveryTests(SimpleTestCase):
    @patch(
        "apps.outbox.management.commands.publish_outbox.purge_published_events",
        return_value=0,
    )
    @patch(
        "apps.outbox.management.commands.publish_outbox.publish_pending_events",
        side_effect=(OperationalError("connection lost"), PublishSummary()),
    )
    @patch(
        "apps.outbox.management.commands.publish_outbox.time.sleep",
        side_effect=(None, KeyboardInterrupt),
    )
    @patch("apps.outbox.management.commands.publish_outbox.close_old_connections")
    def test_outbox_loop_refreshes_connections_after_database_error(
        self,
        close_old_connections,
        _sleep,
        publish_pending_events,
        _purge_published_events,
    ) -> None:
        with self.assertRaises(KeyboardInterrupt):
            call_command(
                "publish_outbox",
                "--loop",
                "--interval=0.01",
                stdout=StringIO(),
            )

        self.assertEqual(publish_pending_events.call_count, 2)
        self.assertEqual(close_old_connections.call_count, 4)

    @patch(
        "apps.audits.management.commands.project_audit_results.consume_parallel_audit_results",
        side_effect=(OperationalError("connection lost"), 0),
    )
    @patch(
        "apps.audits.management.commands.project_audit_results.time.sleep",
        side_effect=(None, KeyboardInterrupt),
    )
    @patch("apps.audits.management.commands.project_audit_results.close_old_connections")
    def test_audit_projector_refreshes_connections_after_database_error(
        self,
        close_old_connections,
        _sleep,
        consume_parallel_audit_results,
    ) -> None:
        with self.assertRaises(KeyboardInterrupt):
            call_command(
                "project_audit_results",
                "--loop",
                "--interval=0.01",
                stdout=StringIO(),
            )

        self.assertEqual(consume_parallel_audit_results.call_count, 2)
        self.assertEqual(close_old_connections.call_count, 4)
