from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)
_GROUP_EXISTS = "BUSYGROUP"


class AuditQueueUnavailable(RuntimeError):
    """Raised only for recoverable Redis transport or availability errors."""


@dataclass(frozen=True, slots=True)
class ResultEntry:
    message_id: str
    payload: dict[str, Any]
    payload_hash: str


class AuditQueue:
    """The two Redis Streams that separate Python control and Go data planes.

    Payloads are JSON values in one `payload` field. Command IDs are stable
    audit UUIDs; consumers must be idempotent because XACK can race a crash.
    """

    def __init__(self, client: Redis | None = None) -> None:
        self.client = client or Redis.from_url(
            settings.CORPUS_AUDITOR_QUEUE_URL,
            decode_responses=True,
            socket_connect_timeout=settings.CORPUS_AUDITOR_QUEUE_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=settings.CORPUS_AUDITOR_QUEUE_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=30,
        )

    def publish_command(self, payload: dict[str, Any]) -> str:
        return self._xadd(settings.CORPUS_AUDITOR_COMMAND_STREAM, payload)

    def ensure_result_group(self) -> None:
        self._ensure_group(
            stream=settings.CORPUS_AUDITOR_RESULT_STREAM,
            group=settings.CORPUS_AUDITOR_RESULT_GROUP,
        )

    def ping(self) -> None:
        """Verify that the queue dependency is reachable without mutating it."""
        try:
            self.client.ping()
        except RedisError as exc:
            raise AuditQueueUnavailable("Unable to reach the auditor queue.") from exc

    def read_results(self, *, limit: int) -> list[ResultEntry]:
        try:
            batches = self.client.xreadgroup(
                groupname=settings.CORPUS_AUDITOR_RESULT_GROUP,
                consumername=_consumer_name(),
                streams={settings.CORPUS_AUDITOR_RESULT_STREAM: ">"},
                count=limit,
                block=settings.CORPUS_AUDITOR_RESULT_BLOCK_MS,
            )
        except RedisError as exc:
            raise AuditQueueUnavailable("Unable to read auditor result stream.") from exc
        entries: list[ResultEntry] = []
        for _, messages in batches:
            for message_id, fields in messages:
                try:
                    payload = _decode_payload(fields, message_id)
                except ValueError as exc:
                    logger.error("Invalid audit result message %s: %s", message_id, exc)
                    continue
                entries.append(
                    ResultEntry(
                        message_id=message_id,
                        payload=payload,
                        payload_hash=_payload_hash(payload),
                    )
                )
        return entries

    def reclaim_results(self, *, limit: int) -> list[ResultEntry]:
        """Recover result deliveries stranded by a crashed projector."""
        try:
            claimed = self.client.xautoclaim(
                name=settings.CORPUS_AUDITOR_RESULT_STREAM,
                groupname=settings.CORPUS_AUDITOR_RESULT_GROUP,
                consumername=_consumer_name(),
                min_idle_time=settings.CORPUS_AUDITOR_RESULT_CLAIM_IDLE_MS,
                start_id="0-0",
                count=limit,
            )
        except RedisError as exc:
            raise AuditQueueUnavailable("Unable to reclaim auditor result stream.") from exc
        messages = claimed[1] if len(claimed) > 1 else []
        entries: list[ResultEntry] = []
        for message_id, fields in messages:
            try:
                payload = _decode_payload(fields, message_id)
            except ValueError as exc:
                logger.error("Invalid reclaimed audit result message %s: %s", message_id, exc)
                continue
            entries.append(
                ResultEntry(
                    message_id=message_id,
                    payload=payload,
                    payload_hash=_payload_hash(payload),
                )
            )
        return entries

    def ack_result(self, message_id: str) -> None:
        try:
            self.client.xack(
                settings.CORPUS_AUDITOR_RESULT_STREAM,
                settings.CORPUS_AUDITOR_RESULT_GROUP,
                message_id,
            )
        except RedisError as exc:
            raise AuditQueueUnavailable("Unable to acknowledge auditor result stream.") from exc

    def _xadd(self, stream: str, payload: dict[str, Any]) -> str:
        encoded = _encode_payload(payload)
        try:
            message_id = self.client.xadd(
                stream,
                {"payload": encoded},
                maxlen=settings.CORPUS_AUDITOR_STREAM_MAXLEN,
                approximate=True,
            )
        except RedisError as exc:
            raise AuditQueueUnavailable("Unable to publish audit command.") from exc
        return str(message_id)

    def _ensure_group(self, *, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except RedisError as exc:
            if _GROUP_EXISTS not in str(exc):
                raise AuditQueueUnavailable("Unable to initialize auditor result consumer group.") from exc


def _encode_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > settings.CORPUS_AUDITOR_MESSAGE_MAX_BYTES:
        raise ValueError("Audit queue payload exceeds the allowed size.")
    return encoded


def _decode_payload(fields: dict[str, Any], message_id: str) -> dict[str, Any]:
    raw = fields.get("payload")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError(f"Audit result {message_id} has no payload field.")
    if len(raw.encode("utf-8")) > settings.CORPUS_AUDITOR_MESSAGE_MAX_BYTES:
        raise ValueError(f"Audit result {message_id} exceeds the allowed size.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Audit result {message_id} has invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Audit result {message_id} must be an object.")
    return payload


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _consumer_name() -> str:
    return f"python-projector-{socket.gethostname()}-{os.getpid()}"
