from __future__ import annotations

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import (
    ParallelAuditError,
    apply_remote_audit_callback,
    verify_remote_callback,
)


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def remote_auditor_callback(request: HttpRequest, audit_id) -> JsonResponse:
    """Accept a signed, idempotent terminal result from the Go executor."""

    body = request.body
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("callback must be a JSON object")
        payload_hash = verify_remote_callback(
            body=body,
            timestamp=request.headers.get("X-Corpus-Auditor-Timestamp", ""),
            signature=request.headers.get("X-Corpus-Auditor-Signature", ""),
        )
        applied = apply_remote_audit_callback(
            audit_id=audit_id,
            payload=payload,
            payload_hash=payload_hash,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, ParallelAuditError) as exc:
        # A callback endpoint must not disclose whether an audit exists or why
        # a control-plane request was rejected.
        logger.warning("Rejected remote auditor callback: %s", exc)
        return JsonResponse({"accepted": False}, status=400)
    return JsonResponse({"accepted": True, "applied": applied})
