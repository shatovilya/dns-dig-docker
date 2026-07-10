import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(item).strip() for item in v if str(item).strip()]
    if isinstance(v, str):
        text = v.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("expected JSON array")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    raise ValueError("expected list or string")


def _parse_int_list(v: Any) -> list[int]:
    return [int(item) for item in _parse_str_list(v)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    # Autonomous mode
    autonomous_mode: bool = False
    autonomous_records: list[str] = []
    autonomous_rps: float = 10.0
    autonomous_concurrency: int = 5
    autonomous_query_types: list[str] = ["A", "AAAA"]
    autonomous_resolve_modes: list[str] = ["system", "absolute_fqdn"]
    autonomous_ndots_values: list[int] = []
    autonomous_timeout_seconds: float = 2.0
    autonomous_test_id: str = "autonomous"

    @field_validator("autonomous_records", mode="before")
    @classmethod
    def parse_autonomous_records(cls, v: Any) -> list[str]:
        return _parse_str_list(v)

    @field_validator("autonomous_query_types", mode="before")
    @classmethod
    def parse_autonomous_query_types(cls, v: Any) -> list[str]:
        return _parse_str_list(v) or ["A", "AAAA"]

    @field_validator("autonomous_resolve_modes", mode="before")
    @classmethod
    def parse_autonomous_resolve_modes(cls, v: Any) -> list[str]:
        return _parse_str_list(v) or ["system", "absolute_fqdn"]

    @field_validator("autonomous_ndots_values", mode="before")
    @classmethod
    def parse_autonomous_ndots_values(cls, v: Any) -> list[int]:
        return _parse_int_list(v)

    # Test defaults
    default_rps: float = 10.0
    default_concurrency: int = 5
    default_duration_seconds: int = 60
    default_timeout_seconds: float = 2.0
    default_records: list[str] = ["example.com", "kubernetes.default.svc.cluster.local"]
    default_query_types: list[str] = ["A", "AAAA"]
    default_resolve_modes: list[str] = ["system", "absolute_fqdn"]
    default_ndots_values: list[int] = []

    @field_validator("default_ndots_values", mode="before")
    @classmethod
    def parse_default_ndots_values(cls, v: Any) -> list[int]:
        return _parse_int_list(v)

    # Limits
    max_rps: float = 100.0
    max_concurrency: int = 50
    max_duration_seconds: int = 3600
    max_records: int = 20

    # Heuristics
    event_buffer_size: int = 1000
    duplicate_window_seconds: float = 2.0
    cache_latency_threshold_ms: float = 5.0
    cache_latency_ratio: float = 0.5

    # Metrics
    metrics_enabled: bool = True

    # --- API Security ---
    environment: str = "production"
    api_auth_enabled: bool = False
    api_bearer_tokens: list[str] = []
    api_keys: list[str] = []
    api_admin_keys: list[str] = []
    api_static_credentials_json: str = ""
    api_basic_auth_enabled: bool = False
    api_basic_auth_username: str = ""
    api_basic_auth_password: str = ""

    api_rate_limit_enabled: bool = True
    api_rate_limit_read_rpm: int = 120
    api_rate_limit_write_rpm: int = 30
    api_rate_limit_expensive_rpm: int = 10
    api_rate_limit_burst: int = 20

    api_ip_allowlist: list[str] = []
    api_admin_ip_allowlist: list[str] = []

    api_cors_enabled: bool = False
    api_cors_allow_origins: list[str] = []
    api_cors_allow_credentials: bool = False

    trust_proxy_enabled: bool = False
    trust_proxy_ips: list[str] = ["127.0.0.1/32", "10.0.0.0/8"]
    require_https: bool = False
    security_hsts_enabled: bool = False
    security_hsts_max_age: int = 31536000

    api_max_body_bytes: int = 262144
    api_max_records_per_run: int = 500
    api_max_fqdn_length: int = 253
    api_max_resolvers_per_run: int = 20
    api_allowed_query_types: list[str] = ["A", "AAAA"]
    api_expose_error_details: bool = False

    dns_max_concurrent_runs: int = 3
    dns_max_run_duration_seconds: int = 300
    dns_max_active_jobs_per_token: int = 2

    mtr_auth_required: bool = True
    mtr_max_concurrent_runs: int = 2
    mtr_max_targets_per_run: int = 20
    mtr_max_hops: int = 32

    prometheus_auth_enabled: bool = False
    prometheus_bearer_token: str = ""
    prometheus_ip_allowlist: list[str] = []
    prometheus_trust_internal_networks: bool = True

    # Web UI (optional observability layer)
    dns_debug_ui_enabled: bool = False
    dns_debug_ui_port: int = 8088
    dns_debug_ui_bind: str = "0.0.0.0"
    dns_debug_ui_base_path: str = "/dns-debug"
    dns_debug_ui_readonly: bool = True
    dns_debug_ui_refresh_seconds: int = 5
    dns_debug_ui_auth_enabled: bool = True
    dns_debug_ui_allowed_roles: list[str] = ["read-only", "operator", "admin"]
    dns_debug_ui_ip_allowlist: list[str] = []
    dns_debug_ui_csp: str = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'"
    )

    @field_validator(
        "api_bearer_tokens",
        "api_keys",
        "api_admin_keys",
        "api_ip_allowlist",
        "api_admin_ip_allowlist",
        "api_cors_allow_origins",
        "trust_proxy_ips",
        "prometheus_ip_allowlist",
        "dns_debug_ui_allowed_roles",
        "dns_debug_ui_ip_allowlist",
        "api_allowed_query_types",
        mode="before",
    )
    @classmethod
    def parse_comma_lists(cls, v: Any) -> list[str]:
        return _parse_str_list(v)

    @field_validator("api_max_records_per_run")
    @classmethod
    def cap_max_records(cls, v: int, info: Any) -> int:
        return max(v, 1)

    # Shutdown
    shutdown_timeout_seconds: float = 5.0

    # Resolver
    resolv_conf_path: str = "/etc/resolv.conf"

    # Diagnosis thresholds
    diagnosis_fqdn_latency_delta_ms: float = 50.0
    diagnosis_search_nxdomain_ratio: float = 0.1
    diagnosis_error_rate_threshold: float = 0.05
    diagnosis_amplification_ratio: float = 2.0

    # MTR network diagnostics
    mtr_enabled: bool = False
    mtr_service_name: str = ""
    mtr_service_port: int = 443
    mtr_count: int = 20
    mtr_interval_seconds: int = 300
    mtr_timeout_seconds: float = 120.0
    mtr_max_history: int = 10

    # UI snapshot persistence (historical mode)
    snapshot_enabled: bool = True
    snapshot_dir: str = "data/snapshots"
    snapshot_retention_count: int = 20

    @field_validator("mtr_service_port")
    @classmethod
    def validate_mtr_service_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("mtr_service_port must be between 1 and 65535")
        return v

    def validate_mtr_startup(self) -> str | None:
        """Return error message if MTR is enabled but misconfigured."""
        if not self.mtr_enabled:
            return None
        if not self.mtr_service_name.strip():
            return "MTR_ENABLED is true but MTR_SERVICE_NAME is empty"
        if not 1 <= self.mtr_service_port <= 65535:
            return "MTR_SERVICE_PORT must be between 1 and 65535"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
