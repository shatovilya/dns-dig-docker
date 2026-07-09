from enum import Enum


class ProtectionClass(str, Enum):
    PUBLIC = "public"
    PROTECTED_READ = "protected_read"
    PROTECTED_WRITE = "protected_write"
    EXPENSIVE = "expensive"
    METRICS = "metrics"
    ADMIN = "admin"


# (method, path prefix or exact) -> protection class
_ROUTE_RULES: list[tuple[str, str, ProtectionClass]] = [
    ("GET", "/health", ProtectionClass.PUBLIC),
    ("GET", "/ready", ProtectionClass.PUBLIC),
    ("GET", "/live", ProtectionClass.PUBLIC),
    ("GET", "/metrics", ProtectionClass.METRICS),
    ("GET", "/resolver", ProtectionClass.PROTECTED_READ),
    ("GET", "/tests", ProtectionClass.PROTECTED_READ),
    ("GET", "/summary", ProtectionClass.PROTECTED_READ),
    ("GET", "/mtr", ProtectionClass.PROTECTED_READ),
    ("POST", "/tests", ProtectionClass.EXPENSIVE),
    ("POST", "/mtr", ProtectionClass.EXPENSIVE),
    ("DELETE", "/tests", ProtectionClass.PROTECTED_WRITE),
]


def classify_request(method: str, path: str) -> ProtectionClass:
    """Map HTTP method + path to protection class."""
    method = method.upper()
    # UI paths: /dns-debug/... or any base path ending with /api/ui/
    if "/api/ui/" in path or path.rstrip("/").endswith("/dns-debug"):
        return ProtectionClass.PROTECTED_READ

    for rule_method, rule_path, cls in _ROUTE_RULES:
        if method != rule_method:
            continue
        if rule_path == path:
            return cls
        if rule_path == "/tests" and path.startswith("/tests/"):
            if method == "DELETE":
                return ProtectionClass.PROTECTED_WRITE
            return ProtectionClass.PROTECTED_READ
        if rule_path == "/mtr" and path.startswith("/mtr/"):
            return ProtectionClass.PROTECTED_READ

    # Default: protect unknown routes when auth is enabled
    return ProtectionClass.PROTECTED_READ


def min_role_for_class(cls: ProtectionClass) -> str | None:
    """Minimum role name required; None means no auth needed."""
    if cls == ProtectionClass.PUBLIC:
        return None
    if cls in (ProtectionClass.PROTECTED_READ, ProtectionClass.METRICS):
        return "read-only"
    if cls in (ProtectionClass.PROTECTED_WRITE, ProtectionClass.EXPENSIVE):
        return "operator"
    if cls == ProtectionClass.ADMIN:
        return "admin"
    return "read-only"
