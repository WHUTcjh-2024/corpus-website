from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.module_loading import import_string


@dataclass(frozen=True, slots=True)
class ScanResult:
    scanner: str
    detail: str


class UploadScanner(Protocol):
    def scan(self, path: Path) -> ScanResult: ...


class DisabledUploadScanner:
    """Explicit local-development scanner; production can swap the backend."""

    def scan(self, path: Path) -> ScanResult:
        return ScanResult(scanner="disabled", detail="scanner disabled by configuration")


class ClamAVUploadScanner:
    """Stream a file to clamd without copying it into another temporary directory."""

    chunk_size = 64 * 1024
    max_response_bytes = 8 * 1024

    def scan(self, path: Path) -> ScanResult:
        try:
            with socket.create_connection(
                (settings.CLAMAV_HOST, settings.CLAMAV_PORT),
                timeout=settings.CLAMAV_TIMEOUT_SECONDS,
            ) as connection:
                connection.sendall(b"zINSTREAM\0")
                with path.open("rb") as source:
                    while chunk := source.read(self.chunk_size):
                        connection.sendall(struct.pack("!I", len(chunk)))
                        connection.sendall(chunk)
                connection.sendall(struct.pack("!I", 0))
                response = _receive_response(connection, self.max_response_bytes)
        except (OSError, TimeoutError) as exc:
            raise ValidationError("上传安全扫描服务暂不可用，请稍后重试。") from exc

        normalized = response.decode("utf-8", errors="replace").strip("\x00\r\n ")
        if normalized.endswith("OK"):
            return ScanResult(scanner="clamav", detail=normalized)
        if "FOUND" in normalized:
            raise ValidationError("文件未通过安全扫描，已拒绝上传。")
        raise ValidationError("上传安全扫描返回异常结果，请稍后重试。")


def scan_uploaded_file(path: Path) -> ScanResult:
    try:
        scanner_class = import_string(settings.UPLOAD_SCANNER_BACKEND)
        scanner: UploadScanner = scanner_class()
    except (ImportError, AttributeError, TypeError) as exc:
        raise ImproperlyConfigured("UPLOAD_SCANNER_BACKEND is invalid.") from exc
    return scanner.scan(path)


def _receive_response(connection: socket.socket, limit: int) -> bytes:
    response = bytearray()
    while len(response) < limit:
        chunk = connection.recv(min(1024, limit - len(response)))
        if not chunk:
            break
        response.extend(chunk)
        if b"\0" in chunk:
            break
    if len(response) >= limit:
        raise ValidationError("上传安全扫描响应过长。")
    return bytes(response)
