from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest


logger = logging.getLogger(__name__)
CACHE_KEY_PREFIX = "login-security:v1"


class LoginSecurityUnavailable(RuntimeError):
    """Raised when production login protection cannot reach its shared cache."""


@dataclass(frozen=True, slots=True)
class LoginDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str = ""


def client_ip(request: HttpRequest) -> str:
    """Return a validated client IP, trusting only a configured reverse proxy."""
    remote_ip = _parse_ip(request.META.get("REMOTE_ADDR"))
    if remote_ip is None:
        return "unknown"

    trusted_networks = tuple(
        ipaddress.ip_network(value, strict=False)
        for value in settings.LOGIN_TRUSTED_PROXY_CIDRS
    )
    if any(remote_ip in network for network in trusted_networks):
        forwarded_ip = _parse_ip(request.META.get("HTTP_X_REAL_IP"))
        if forwarded_ip is not None:
            return forwarded_ip.compressed
    return remote_ip.compressed


def evaluate_login_attempt(request: HttpRequest, username: object) -> LoginDecision:
    ip_value = client_ip(request)
    username_value = _normalize_username(username)
    identities = _identities(ip_value, username_value)
    lock_keys = [_key("lock", kind, value) for kind, value in identities]

    try:
        if any(cache.get_many(lock_keys).values()):
            return LoginDecision(
                allowed=False,
                retry_after_seconds=settings.LOGIN_LOCKOUT_SECONDS,
                reason="locked",
            )

        now = int(time.time())
        window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
        bucket = now // window
        rate_key = _key("rate", "ip", f"{ip_value}:{bucket}")
        attempts = _increment_counter(rate_key, timeout=window + 5)
        if attempts > settings.LOGIN_RATE_LIMIT_ATTEMPTS:
            return LoginDecision(
                allowed=False,
                retry_after_seconds=max(1, window - (now % window)),
                reason="rate_limited",
            )
    except Exception as exc:
        return _cache_failure(exc)

    return LoginDecision(allowed=True)


def record_login_failure(request: HttpRequest, username: object) -> None:
    ip_value = client_ip(request)
    username_value = _normalize_username(username)
    limits = {
        "pair": settings.LOGIN_PAIR_FAILURE_LIMIT,
        "username": settings.LOGIN_USERNAME_FAILURE_LIMIT,
        "ip": settings.LOGIN_IP_FAILURE_LIMIT,
    }
    try:
        for kind, value in _identities(ip_value, username_value):
            failures = _increment_counter(
                _key("failures", kind, value),
                timeout=settings.LOGIN_FAILURE_WINDOW_SECONDS,
            )
            if failures >= limits[kind]:
                cache.set(
                    _key("lock", kind, value),
                    1,
                    timeout=settings.LOGIN_LOCKOUT_SECONDS,
                )
    except Exception as exc:
        _cache_failure(exc)


def record_login_success(request: HttpRequest, username: object) -> None:
    ip_value = client_ip(request)
    username_value = _normalize_username(username)
    keys: list[str] = []
    for kind, value in _identities(ip_value, username_value):
        if kind == "ip":
            continue
        keys.extend((_key("failures", kind, value), _key("lock", kind, value)))
    try:
        cache.delete_many(keys)
    except Exception:
        logger.warning("Failed to clear login failure state after success.", exc_info=True)


def _identities(ip_value: str, username_value: str) -> tuple[tuple[str, str], ...]:
    return (
        ("pair", f"{ip_value}|{username_value}"),
        ("username", username_value),
        ("ip", ip_value),
    )


def _increment_counter(key: str, *, timeout: int) -> int:
    if cache.add(key, 1, timeout=timeout):
        return 1
    try:
        return int(cache.incr(key))
    except ValueError:
        if cache.add(key, 1, timeout=timeout):
            return 1
        return int(cache.incr(key))


def _cache_failure(exc: Exception) -> LoginDecision:
    logger.error("Login protection cache is unavailable.", exc_info=exc)
    if settings.LOGIN_SECURITY_FAIL_CLOSED:
        raise LoginSecurityUnavailable from exc
    return LoginDecision(allowed=True)


def _key(category: str, kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"{CACHE_KEY_PREFIX}:{category}:{kind}:{digest}"


def _normalize_username(value: object) -> str:
    return str(value or "").strip().casefold()[:150] or "<empty>"


def _parse_ip(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
