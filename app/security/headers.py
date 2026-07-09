from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import get_settings
from security.classification import ProtectionClass, classify_request
from security.client_ip import is_https


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        response = await call_next(request)
        cls = classify_request(request.method, request.url.path)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")

        ui_enabled = settings.dns_debug_ui_enabled
        is_ui = ui_enabled and (
            request.url.path.startswith(settings.dns_debug_ui_base_path.rstrip("/"))
            or "/api/ui/" in request.url.path
        )

        if is_ui:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            if settings.dns_debug_ui_csp:
                response.headers.setdefault("Content-Security-Policy", settings.dns_debug_ui_csp)
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")

        if cls not in (ProtectionClass.PUBLIC, ProtectionClass.METRICS):
            response.headers.setdefault("Cache-Control", "no-store")

        if settings.security_hsts_enabled and settings.require_https and is_https(request, settings):
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={settings.security_hsts_max_age}; includeSubDomains",
            )

        return response
