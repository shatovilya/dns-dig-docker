import logging
from typing import Any

from starlette.requests import Request

from security.principal import Principal
from security.request_id import get_request_id

logger = logging.getLogger(__name__)


def audit_event(
    event: str,
    request: Request,
    *,
    principal: Principal | None = None,
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "request_id": get_request_id(request),
        "method": request.method,
        "path": request.url.path,
    }
    if principal:
        payload["credential_id"] = principal.credential_id
        payload["role"] = principal.role.value
    if status_code is not None:
        payload["status_code"] = status_code
    if extra:
        payload.update(extra)
    logger.info("security_audit", extra={"audit": payload})
