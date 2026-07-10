import hashlib
import json
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from config import get_settings
from security.audit import audit_event
from security.classification import ProtectionClass, classify_request, min_role_for_class
from security.client_ip import get_client_ip
from security.ip_allowlist import check_ip_allowed
from security.principal import Principal, Role

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_basic_scheme = HTTPBasic(auto_error=False)


def _role_from_str(value: str) -> Role:
    normalized = value.strip().lower().replace("_", "-")
    mapping = {
        "read-only": Role.READ_ONLY,
        "readonly": Role.READ_ONLY,
        "operator": Role.OPERATOR,
        "admin": Role.ADMIN,
    }
    if normalized not in mapping:
        raise ValueError(f"unknown role: {value}")
    return mapping[normalized]


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


@lru_cache
def _load_credential_map(settings_fingerprint: str) -> dict[str, Principal]:
    """Build secret -> Principal map. Cache key is hash of credential config."""
    del settings_fingerprint  # cache bust via caller-provided fingerprint
    settings = get_settings()
    creds: dict[str, Principal] = {}

    if settings.api_static_credentials_json:
        try:
            items = json.loads(settings.api_static_credentials_json)
            if isinstance(items, list):
                for i, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    secret = str(item.get("secret", "")).strip()
                    if not secret:
                        continue
                    cid = str(item.get("id", f"cred-{i}"))
                    role = _role_from_str(str(item.get("role", "read-only")))
                    creds[secret] = Principal(credential_id=cid, role=role)
        except json.JSONDecodeError:
            logger.error("invalid API_STATIC_CREDENTIALS_JSON", extra={"event": "auth_config_error"})

    for i, token in enumerate(settings.api_bearer_tokens):
        token = token.strip()
        if token:
            creds[token] = Principal(credential_id=f"bearer-{i}", role=Role.READ_ONLY)

    for i, key in enumerate(settings.api_keys):
        key = key.strip()
        if key:
            creds[key] = Principal(credential_id=f"apikey-{i}", role=Role.OPERATOR)

    for i, key in enumerate(settings.api_admin_keys):
        key = key.strip()
        if key:
            creds[key] = Principal(credential_id=f"admin-{i}", role=Role.ADMIN)

    return creds


def _credentials_cache_key() -> str:
    s = get_settings()
    parts = [
        s.api_static_credentials_json or "",
        ",".join(s.api_bearer_tokens),
        ",".join(s.api_keys),
        ",".join(s.api_admin_keys),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def resolve_principal(secret: str) -> Principal | None:
    return _load_credential_map(_credentials_cache_key()).get(secret)


def _authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    api_key: str | None,
    basic: HTTPBasicCredentials | None,
) -> Principal | None:
    settings = get_settings()
    if not settings.api_auth_enabled:
        return Principal(credential_id="anonymous", role=Role.ADMIN)

    if credentials and credentials.scheme.lower() == "bearer":
        principal = resolve_principal(credentials.credentials)
        if principal:
            return principal

    if api_key:
        principal = resolve_principal(api_key)
        if principal:
            return principal

    if settings.api_basic_auth_enabled and basic:
        if (
            basic.username == settings.api_basic_auth_username
            and basic.password == settings.api_basic_auth_password
            and settings.api_basic_auth_username
        ):
            return Principal(credential_id=f"basic:{basic.username}", role=Role.OPERATOR)

    return None


async def get_optional_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)] = None,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
    basic: Annotated[HTTPBasicCredentials | None, Security(_basic_scheme)] = None,
) -> Principal | None:
    return _authenticate_request(request, credentials, api_key, basic)


async def require_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)] = None,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
    basic: Annotated[HTTPBasicCredentials | None, Security(_basic_scheme)] = None,
) -> Principal:
    settings = get_settings()
    principal = _authenticate_request(request, credentials, api_key, basic)
    if principal is not None:
        return principal

    client_ip = get_client_ip(request, settings)
    audit_event(
        "auth_failure",
        request,
        principal=None,
        extra={"client_ip": client_ip, "path": request.url.path},
    )
    raise HTTPException(401, "unauthorized")


def require_role(min_role: Role):
    async def _dep(
        request: Request,
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> Principal:
        if not principal.role.satisfies(min_role):
            audit_event(
                "forbidden",
                request,
                principal=principal,
                extra={"required_role": min_role.value},
            )
            raise HTTPException(403, "forbidden")
        return principal

    return _dep


RequireReadOnly = Annotated[Principal, Depends(require_role(Role.READ_ONLY))]
RequireOperator = Annotated[Principal, Depends(require_role(Role.OPERATOR))]
RequireAdmin = Annotated[Principal, Depends(require_role(Role.ADMIN))]


def check_metrics_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
) -> None:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(404, "metrics disabled")

    check_ip_allowed(
        request,
        settings,
        settings.prometheus_ip_allowlist,
        trust_internal=settings.prometheus_trust_internal_networks,
    )

    if settings.prometheus_auth_enabled:
        token = settings.prometheus_bearer_token.strip()
        if not token:
            raise HTTPException(503, "prometheus auth misconfigured")
        auth = request.headers.get("authorization", "")
        if auth.lower() != f"bearer {token}".lower():
            audit_event("auth_failure", request, principal=None, extra={"endpoint": "metrics"})
            raise HTTPException(401, "unauthorized")


def enforce_route_security(
    request: Request,
    principal: Principal | None,
) -> Principal | None:
    """Central gate used by middleware for route-level auth when enabled."""
    settings = get_settings()
    cls = classify_request(request.method, request.url.path)

    if cls == ProtectionClass.PUBLIC:
        return principal

    if cls == ProtectionClass.METRICS:
        check_metrics_access(request)
        return principal

    if not settings.api_auth_enabled:
        if settings.api_ip_allowlist:
            check_ip_allowed(request, settings, settings.api_ip_allowlist)
        return principal or Principal(credential_id="anonymous", role=Role.ADMIN)

    if principal is None:
        client_ip = get_client_ip(request, settings)
        audit_event("auth_failure", request, principal=None, extra={"client_ip": client_ip})
        raise HTTPException(401, "unauthorized")

    required_name = min_role_for_class(cls)
    if required_name:
        required = _role_from_str(required_name)
        if cls == ProtectionClass.EXPENSIVE and settings.mtr_auth_required and request.url.path.startswith("/mtr"):
            required = Role.OPERATOR
        if not principal.role.satisfies(required):
            audit_event("forbidden", request, principal=principal, extra={"required_role": required.value})
            raise HTTPException(403, "forbidden")

    if settings.api_ip_allowlist:
        check_ip_allowed(request, settings, settings.api_ip_allowlist)

    return principal
