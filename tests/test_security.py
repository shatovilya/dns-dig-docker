import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

# app modules live in app/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def _reset_settings() -> None:
    from config import get_settings

    get_settings.cache_clear()
    try:
        from security.auth import _load_credential_map

        _load_credential_map.cache_clear()
    except Exception:
        pass


@pytest.fixture
def client_no_auth(monkeypatch):
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("PROMETHEUS_TRUST_INTERNAL_NETWORKS", "false")
    monkeypatch.setenv("AUTONOMOUS_MODE", "false")
    _reset_settings()
    from main import create_app

    return TestClient(create_app())


@pytest.fixture
def client_auth(monkeypatch):
    creds = [
        {"id": "reader", "secret": "read-token", "role": "read-only"},
        {"id": "operator", "secret": "op-token", "role": "operator"},
    ]
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("PROMETHEUS_TRUST_INTERNAL_NETWORKS", "false")
    monkeypatch.setenv("AUTONOMOUS_MODE", "false")
    monkeypatch.setenv("API_STATIC_CREDENTIALS_JSON", json.dumps(creds))
    _reset_settings()
    from main import create_app

    return TestClient(create_app())


def test_health_public_without_auth(client_auth):
    r = client_auth.get("/health")
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")


def test_protected_endpoint_denied_without_token(client_auth):
    r = client_auth.get("/tests")
    assert r.status_code == 401


def test_read_token_can_list_tests(client_auth):
    r = client_auth.get("/tests", headers={"Authorization": "Bearer read-token"})
    assert r.status_code == 200


def test_invalid_token_rejected(client_auth):
    r = client_auth.get("/tests", headers={"Authorization": "Bearer bad-token"})
    assert r.status_code == 401


def test_readonly_cannot_start_test(client_auth):
    r = client_auth.post(
        "/tests",
        headers={"Authorization": "Bearer read-token"},
        json={
            "records": ["example.com"],
            "resolve_modes": ["system"],
            "rps": 1,
            "concurrency": 1,
            "duration_seconds": 1,
            "timeout_seconds": 1,
        },
    )
    assert r.status_code == 403


def test_operator_can_start_test(client_auth):
    r = client_auth.post(
        "/tests",
        headers={"Authorization": "Bearer op-token"},
        json={
            "records": ["example.com"],
            "resolve_modes": ["system"],
            "rps": 1,
            "concurrency": 1,
            "duration_seconds": 1,
            "timeout_seconds": 1,
        },
    )
    assert r.status_code == 201
    assert "test_id" in r.json()


def test_metrics_accessible_no_auth_mode(client_no_auth):
    r = client_no_auth.get("/metrics")
    assert r.status_code == 200


def test_request_validation_unknown_field(client_no_auth):
    r = client_no_auth.post(
        "/tests",
        json={
            "records": ["example.com"],
            "resolve_modes": ["system"],
            "rps": 1,
            "concurrency": 1,
            "duration_seconds": 1,
            "timeout_seconds": 1,
            "evil": True,
        },
    )
    assert r.status_code == 422


def test_live_ready_public(client_auth):
    assert client_auth.get("/live").status_code == 200
    assert client_auth.get("/ready").status_code == 200
