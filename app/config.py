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
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
