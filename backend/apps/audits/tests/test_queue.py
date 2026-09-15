from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from redis.exceptions import RedisError

from apps.audits.queue import (
    AuditQueue,
    AuditQueueUnavailable,
    _consumer_name,
    _decode_payload,
    _encode_payload,
    _payload_hash,
)


@override_settings(
    CORPUS_AUDITOR_COMMAND_STREAM="commands",
    CORPUS_AUDITOR_RESULT_STREAM="results",
    CORPUS_AUDITOR_RESULT_GROUP="projectors",
    CORPUS_AUDITOR_RESULT_BLOCK_MS=10,
    CORPUS_AUDITOR_RESULT_CLAIM_IDLE_MS=100,
    CORPUS_AUDITOR_STREAM_MAXLEN=1000,
    CORPUS_AUDITOR_MESSAGE_MAX_BYTES=128,
)
class AuditQueueTests(SimpleTestCase):
    def test_publish_ping_group_and_ack_success(self) -> None:
        client = Mock()
        client.xadd.return_value = "1-0"
        queue = AuditQueue(client)

        self.assertEqual(queue.publish_command({"id": "audit"}), "1-0")
        queue.ping()
        queue.ensure_result_group()
        queue.ack_result("1-0")

        client.xadd.assert_called_once()
        client.xgroup_create.assert_called_once_with(
            "results", "projectors", id="0", mkstream=True
        )
        client.xack.assert_called_once_with("results", "projectors", "1-0")

    def test_group_existing_error_is_idempotent_but_other_errors_fail(self) -> None:
        client = Mock()
        client.xgroup_create.side_effect = RedisError("BUSYGROUP exists")
        AuditQueue(client).ensure_result_group()

        client.xgroup_create.side_effect = RedisError("connection refused")
        with self.assertRaises(AuditQueueUnavailable):
            AuditQueue(client).ensure_result_group()

    def test_transport_operations_translate_redis_errors(self) -> None:
        operations = (
            ("ping", lambda queue: queue.ping()),
            ("xadd", lambda queue: queue.publish_command({"id": "audit"})),
            ("xreadgroup", lambda queue: queue.read_results(limit=1)),
            ("xautoclaim", lambda queue: queue.reclaim_results(limit=1)),
            ("xack", lambda queue: queue.ack_result("1-0")),
        )
        for method, invoke in operations:
            with self.subTest(method=method):
                client = Mock()
                getattr(client, method).side_effect = RedisError("offline")
                with self.assertRaises(AuditQueueUnavailable):
                    invoke(AuditQueue(client))

    def test_reads_valid_entries_and_skips_invalid_payloads(self) -> None:
        client = Mock()
        client.xreadgroup.return_value = [
            (
                "results",
                [
                    ("1-0", {"payload": '{"id":"one"}'}),
                    ("2-0", {"payload": "not-json"}),
                ],
            )
        ]
        entries = AuditQueue(client).read_results(limit=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].payload, {"id": "one"})
        self.assertEqual(entries[0].payload_hash, _payload_hash({"id": "one"}))

    def test_reclaims_valid_entries_and_handles_short_server_response(self) -> None:
        client = Mock()
        client.xautoclaim.return_value = ["0-0", [("1-0", {"payload": b'{"id":1}'})]]
        entries = AuditQueue(client).reclaim_results(limit=10)
        self.assertEqual(entries[0].payload["id"], 1)

        client.xautoclaim.return_value = ["0-0"]
        self.assertEqual(AuditQueue(client).reclaim_results(limit=10), [])

    def test_payload_validation_covers_type_size_json_and_object_rules(self) -> None:
        self.assertEqual(_decode_payload({"payload": b'{"id":1}'}, "1-0"), {"id": 1})
        cases = (
            ({}, "no payload"),
            ({"payload": "x" * 129}, "allowed size"),
            ({"payload": "{"}, "invalid JSON"),
            ({"payload": "[]"}, "must be an object"),
        )
        for fields, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesMessage(ValueError, message):
                    _decode_payload(fields, "1-0")

        self.assertEqual(_encode_payload({"value": "中文"}), '{"value":"中文"}')
        with self.assertRaisesMessage(ValueError, "allowed size"):
            _encode_payload({"value": "x" * 200})

    def test_consumer_name_is_process_specific(self) -> None:
        with (
            patch("apps.audits.queue.socket.gethostname", return_value="host"),
            patch("apps.audits.queue.os.getpid", return_value=123),
        ):
            self.assertEqual(_consumer_name(), "python-projector-host-123")
