from typing import Callable

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import get_settings
from security.auth import Principal, _authenticate_request, enforce_route_security
from security.client_ip import is_https
from security.errors import http_error_response


def _parse_auth_from_request(request: Request) -> Principal | None:
    auth_header = request.headers.get("authorization", "")
    credentials = None
    if auth_header.lower().startswith("bearer "):
        from fastapi.security import HTTPAuthorizationCredentials

        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=auth_header[7:].strip(),
        )
    api_key = request.headers.get("x-api-key")
    basic = None
    if auth_header.lower().startswith("basic "):
        import base64

        try:
            from fastapi.security import HTTPBasicCredentials

            decoded = base64.b64decode(auth_header[6:].strip()).decode()
            user, _, password = decoded.partition(":")
            basic = HTTPBasicCredentials(username=user, password=password)
        except Exception:
            basic = None

    return _authenticate_request(request, credentials, api_key, basic)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()

        if settings.require_https and not is_https(request, settings):
            return http_error_response(request, HTTPException(400, "https required"))

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.api_max_body_bytes:
                    return http_error_response(request, HTTPException(413, "request body too large"))
            except ValueError:
                pass

        principal = _parse_auth_from_request(request)
        request.state.principal = principal
        try:
            enforce_route_security(request, principal)
        except HTTPException as exc:
            return http_error_response(request, exc)

        return await call_next(request)
