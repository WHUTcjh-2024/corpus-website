from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError
from django.http import HttpRequest

from apps.corpora.models import Corpus

from .models import AuditEvent, AuditEventType


logger = logging.getLogger(__name__)
_MAX_METADATA_BYTES = 16_384
_MAX_TEXT_LENGTH = 1_000
_MAX_COLLECTION_ITEMS = 50


def record_audit_event(
    event_type: str,
    *,
    request: HttpRequest | None = None,
    actor: Any | None = None,
    corpus: Corpus | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AuditEvent | None:
    """Persist a bounded audit event without making user requests depend on logging."""
    if event_type not in AuditEventType.values:
        raise ValueError(f"Unsupported audit event type: {event_type}")

    resolved_actor = actor
    if resolved_actor is None and request is not None:
        resolved_actor = getattr(request, "user", None)
    if isinstance(resolved_actor, AnonymousUser) or not getattr(
        resolved_actor, "is_authenticated", False
    ):
        resolved_actor = None

    payload = _bounded_metadata(metadata or {})
    request_meta = (getattr(request, "META", {}) or {}) if request is not None else {}
    try:
        return AuditEvent.objects.create(
            actor=resolved_actor,
            event_type=event_type,
            corpus=corpus,
            path=(str(getattr(request, "path", "") or "")[:500] if request is not None else ""),
            method=(str(getattr(request, "method", "") or "")[:10] if request is not None else ""),
            ip_address=_request_ip(request),
            user_agent=str(request_meta.get("HTTP_USER_AGENT", "") or "")[:500],
            metadata=payload,
        )
    except DatabaseError:
        logger.exception("Unable to persist audit event %s", event_type)
        return None


def serializable_form_data(cleaned_data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_value(value)
        for key, value in cleaned_data.items()
        if key not in {"page", "page_size"}
    }


def _bounded_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        str(key)[:100]: _json_value(value)
        for key, value in list(metadata.items())[:_MAX_COLLECTION_ITEMS]
    }
    encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) <= _MAX_METADATA_BYTES:
        return payload
    return {
        "truncated": True,
        "summary": encoded[: _MAX_METADATA_BYTES - 256].decode("utf-8", errors="ignore"),
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_TEXT_LENGTH]
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: _json_value(item)
            for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in list(value)[:_MAX_COLLECTION_ITEMS]]
    if hasattr(value, "pk"):
        return str(value.pk)
    return str(value)[:_MAX_TEXT_LENGTH]


def _request_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    value = str((getattr(request, "META", {}) or {}).get("REMOTE_ADDR", "") or "").strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None
