import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import get_settings
from security.audit import audit_event
from security.errors import http_error_response
from security.middleware import _parse_auth_from_request
from security.principal import Principal
from security.classification import ProtectionClass, classify_request
from security.client_ip import get_client_ip


@dataclass
class _Bucket:
    tokens: float
    last_update: float = field(default_factory=time.monotonic)


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(tokens=0.0))

    def _rpm_to_rate(self, rpm: int) -> float:
        return max(rpm, 1) / 60.0

    def allow(self, key: str, rpm: int, burst: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets[key]
        rate = self._rpm_to_rate(rpm)
        elapsed = now - bucket.last_update
        bucket.last_update = now
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * rate)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False


_limiter = RateLimiter()


def _limit_for_class(cls: ProtectionClass) -> tuple[int, int]:
    settings = get_settings()
    burst = settings.api_rate_limit_burst
    if cls == ProtectionClass.EXPENSIVE:
        return settings.api_rate_limit_expensive_rpm, burst
    if cls in (ProtectionClass.PROTECTED_WRITE, ProtectionClass.ADMIN):
        return settings.api_rate_limit_write_rpm, burst
    return settings.api_rate_limit_read_rpm, burst


def check_rate_limit(request: Request, principal: Principal | None) -> None:
    settings = get_settings()
    if not settings.api_rate_limit_enabled:
        return

    cls = classify_request(request.method, request.url.path)
    if cls == ProtectionClass.PUBLIC:
        return

    rpm, burst = _limit_for_class(cls)
    client_ip = get_client_ip(request, settings)
    cred_id = principal.credential_id if principal else "anon"
    key = f"{client_ip}:{cred_id}:{cls.value}"
    if not _limiter.allow(key, rpm, burst):
        audit_event("rate_limited", request, principal=principal, status_code=429)
        raise HTTPException(429, "rate limit exceeded")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if settings.api_rate_limit_enabled:
            principal = getattr(request.state, "principal", None)
            if principal is None:
                principal = _parse_auth_from_request(request)
            try:
                check_rate_limit(request, principal)
            except HTTPException as exc:
                return http_error_response(request, exc)
        return await call_next(request)
